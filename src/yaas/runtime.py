"""Container runtime abstraction with Podman/Docker implementations."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Mount:
    """Container mount specification."""

    source: str
    target: str
    type: str = "bind"  # bind, volume, tmpfs
    read_only: bool = False


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

    def is_available(self) -> bool:
        """Check if this runtime is available."""
        ...

    def run(self, spec: ContainerSpec) -> int:
        """Run container, return exit code."""
        ...


class PodmanRuntime:
    """Podman implementation using CLI subprocess."""

    name = "podman"

    def is_available(self) -> bool:
        return shutil.which("podman") is not None

    def run(self, spec: ContainerSpec) -> int:
        cmd = self._build_command(spec)
        result = subprocess.run(cmd)
        return result.returncode

    def _build_command(self, spec: ContainerSpec) -> list[str]:
        cmd = ["podman", "run", "--rm"]

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
            mount_str = self._format_mount(m)
            cmd.extend(["--mount", mount_str])

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

    def _format_mount(self, m: Mount) -> str:
        """Format mount for --mount flag."""
        parts = [f"type={m.type}"]

        if m.type == "volume":
            parts.append(f"source={m.source}")
        else:
            parts.append(f"source={m.source}")

        parts.append(f"target={m.target}")

        if m.read_only:
            parts.append("readonly")

        return ",".join(parts)


class DockerRuntime:
    """Docker implementation using CLI subprocess."""

    name = "docker"

    def is_available(self) -> bool:
        return shutil.which("docker") is not None

    def run(self, spec: ContainerSpec) -> int:
        cmd = self._build_command(spec)
        result = subprocess.run(cmd)
        return result.returncode

    def _build_command(self, spec: ContainerSpec) -> list[str]:
        cmd = ["docker", "run", "--rm"]

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
            mount_str = self._format_mount(m)
            cmd.extend(["--mount", mount_str])

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

    def _format_mount(self, m: Mount) -> str:
        """Format mount for --mount flag."""
        parts = [f"type={m.type}"]

        if m.type == "volume":
            parts.append(f"source={m.source}")
        else:
            parts.append(f"source={m.source}")

        parts.append(f"target={m.target}")

        if m.read_only:
            parts.append("readonly")

        return ",".join(parts)


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
