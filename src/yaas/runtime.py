"""Container runtime abstraction with Podman/Docker implementations."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol

from .platform import get_container_socket_paths, is_linux


def _can_access_docker_socket() -> bool:
    """Check if Docker socket is accessible without sudo."""
    for sock_path in get_container_socket_paths(docker_only=True):
        if sock_path.exists() and os.access(sock_path, os.R_OK | os.W_OK):
            return True
    return False


@dataclass
class Mount:
    """Container mount specification."""

    source: str
    target: str
    type: str = "bind"  # bind, volume, tmpfs
    read_only: bool = False


def _format_mount(m: Mount) -> str:
    """Format mount for --mount flag (shared by Podman and Docker)."""
    parts = [f"type={m.type}", f"source={m.source}", f"target={m.target}"]

    if m.read_only:
        parts.append("readonly")

    return ",".join(parts)


@dataclass
class ContainerSpec:
    """Full container run specification."""

    image: str
    command: list[str]
    working_dir: str
    user: str  # "uid:gid"
    environment: dict[str, str]
    mounts: list[Mount]
    network_mode: str | None  # None or "none"
    tty: bool
    stdin_open: bool

    # Supplementary groups (GIDs)
    groups: list[int] | None = None

    # Resource limits
    memory: str | None = None  # e.g., "8g"
    memory_swap: str | None = None  # None = same as memory (no swap)
    cpus: float | None = None  # e.g., 2.0
    pids_limit: int | None = None  # e.g., 1000


class ContainerRuntime(Protocol):
    """Protocol for container runtimes."""

    name: str

    @property
    def command_prefix(self) -> list[str]:
        """Command prefix for invoking the runtime (e.g., ['docker'] or ['sudo', 'docker'])."""
        ...

    def is_available(self) -> bool:
        """Check if this runtime is available."""
        ...

    def run(self, spec: ContainerSpec) -> int:
        """Run container, return exit code."""
        ...


class PodmanRuntime:
    """Podman implementation using CLI subprocess.

    Note: Only supported on Linux. macOS Podman is experimental and
    doesn't support the same rootless features.
    """

    name = "podman"

    @property
    def command_prefix(self) -> list[str]:
        return ["podman"]

    def is_available(self) -> bool:
        # Podman only supported on Linux
        if not is_linux():
            return False
        return shutil.which("podman") is not None

    def run(self, spec: ContainerSpec) -> int:
        cmd = self._build_command(spec)
        result = subprocess.run(cmd)
        return result.returncode

    def _build_command(self, spec: ContainerSpec) -> list[str]:
        cmd = [*self.command_prefix, "run", "--rm"]

        # Use keep-id to preserve UID mapping in rootless podman.
        # This makes host UID 1000 = container UID 1000, so files are
        # readable and YOLO flags work (Claude blocks them for root).
        cmd.append("--userns=keep-id")

        # Disable SELinux label confinement (needed for /etc/passwd mount)
        cmd.extend(["--security-opt", "label=disable"])

        # Interactive/TTY
        if spec.tty:
            cmd.append("-t")
        if spec.stdin_open:
            cmd.append("-i")

        # User
        cmd.extend(["--user", spec.user])

        # Preserve host supplementary groups (needed for docker socket access with userns)
        if spec.groups:
            cmd.extend(["--group-add", "keep-groups"])

        # Working directory
        cmd.extend(["--workdir", spec.working_dir])

        # Network
        if spec.network_mode:
            cmd.extend(["--network", spec.network_mode])

        # Environment
        for key, value in spec.environment.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Mounts
        for m in spec.mounts:
            cmd.extend(["--mount", _format_mount(m)])

        # Resource limits
        if spec.memory:
            cmd.extend(["--memory", spec.memory])
            # If swap not specified, set to same as memory (disables swap)
            swap = spec.memory_swap or spec.memory
            cmd.extend(["--memory-swap", swap])

        if spec.cpus:
            cmd.extend(["--cpus", str(spec.cpus)])

        if spec.pids_limit:
            cmd.extend(["--pids-limit", str(spec.pids_limit)])

        # Image and command
        cmd.append(spec.image)
        cmd.extend(spec.command)

        return cmd


class DockerRuntime:
    """Docker implementation using CLI subprocess."""

    name = "docker"

    def __init__(self) -> None:
        self._use_sudo = False
        # Check if we need sudo to access docker socket
        if not _can_access_docker_socket() and shutil.which("sudo") is not None:
            self._use_sudo = True

    @property
    def command_prefix(self) -> list[str]:
        if self._use_sudo:
            return ["sudo", "docker"]
        return ["docker"]

    def is_available(self) -> bool:
        if shutil.which("docker") is None:
            return False
        # Available if we can access socket directly OR via sudo
        return _can_access_docker_socket() or self._use_sudo

    def run(self, spec: ContainerSpec) -> int:
        cmd = self._build_command(spec)
        result = subprocess.run(cmd)
        return result.returncode

    def _build_command(self, spec: ContainerSpec) -> list[str]:
        cmd = [*self.command_prefix, "run", "--rm"]

        # Interactive/TTY
        if spec.tty:
            cmd.append("-t")
        if spec.stdin_open:
            cmd.append("-i")

        # User
        cmd.extend(["--user", spec.user])

        # Supplementary groups
        if spec.groups:
            for gid in spec.groups:
                cmd.extend(["--group-add", str(gid)])

        # Working directory
        cmd.extend(["--workdir", spec.working_dir])

        # Network
        if spec.network_mode:
            cmd.extend(["--network", spec.network_mode])

        # Environment
        for key, value in spec.environment.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Mounts
        for m in spec.mounts:
            cmd.extend(["--mount", _format_mount(m)])

        # Resource limits
        if spec.memory:
            cmd.extend(["--memory", spec.memory])
            # If swap not specified, set to same as memory (disables swap)
            swap = spec.memory_swap or spec.memory
            cmd.extend(["--memory-swap", swap])

        if spec.cpus:
            cmd.extend(["--cpus", str(spec.cpus)])

        if spec.pids_limit:
            cmd.extend(["--pids-limit", str(spec.pids_limit)])

        # Image and command
        cmd.append(spec.image)
        cmd.extend(spec.command)

        return cmd


def get_runtime(preference: str | None = None) -> ContainerRuntime:
    """Get available container runtime, with optional preference."""
    runtimes: list[tuple[str, type[PodmanRuntime] | type[DockerRuntime]]] = [
        ("podman", PodmanRuntime),
        ("docker", DockerRuntime),
    ]

    # If preference specified, try it first
    if preference:
        runtimes.sort(key=lambda x: x[0] != preference)

    for _, cls in runtimes:
        runtime = cls()
        if runtime.is_available():
            return runtime

    raise RuntimeError("No container runtime found. Install podman or docker.")
