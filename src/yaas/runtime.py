"""Container runtime abstraction with Podman/Docker implementations."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .config import Config

from .logging import get_logger
from .platform import get_container_socket_paths, is_linux

logger = get_logger()


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
    parts = [f"type={m.type}"]

    # tmpfs mounts don't have a source
    if m.type != "tmpfs":
        parts.append(f"source={m.source}")

    parts.append(f"target={m.target}")

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

    # PID namespace mode
    pid_mode: str | None = None

    # Resource limits
    memory: str | None = None  # e.g., "8g"
    memory_swap: str | None = None  # None = same as memory (no swap)
    cpus: float | None = None  # e.g., 2.0
    pids_limit: int | None = None  # e.g., 1000

    # Port publishing
    ports: list[str] | None = None  # e.g., ["8080:8080", "3000:3000"]

    # Security
    capabilities: list[str] | None = None  # Exact cap set; triggers --cap-drop ALL + --cap-add each
    seccomp_profile: str | None = None  # path to seccomp JSON profile


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

    def create_volume(self, name: str) -> bool:
        """Create a named volume. Returns True on success."""
        ...

    def remove_volume(self, name: str) -> bool:
        """Remove a named volume. Returns True on success."""
        ...

    def adjust_config(self, config: Config) -> None:
        """Adjust config for runtime compatibility. Default: no-op."""
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

    def create_volume(self, name: str) -> bool:
        """Create a named volume. Returns True on success."""
        result = subprocess.run(
            [*self.command_prefix, "volume", "create", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.debug(f"Failed to create volume {name}: {result.stderr}")
        return result.returncode == 0

    def remove_volume(self, name: str) -> bool:
        """Remove a named volume. Returns True on success."""
        result = subprocess.run(
            [*self.command_prefix, "volume", "rm", "-f", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.debug(f"Failed to remove volume {name}: {result.stderr}")
        return result.returncode == 0

    def adjust_config(self, config: Config) -> None:
        pass

    def _add_userns_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        """Add user namespace flags. Override in subclasses for different behavior."""
        # Use keep-id to preserve UID mapping in rootless podman.
        # This makes host UID 1000 = container UID 1000, so files are
        # readable and YOLO flags work (Claude blocks them for root).
        cmd.append("--userns=keep-id")

    def _add_user_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        """Add user identity flags. Override in subclasses for different behavior."""
        cmd.extend(["--user", spec.user])
        # Preserve host supplementary groups (needed for docker socket access with userns)
        if spec.groups:
            cmd.extend(["--group-add", "keep-groups"])

    def _build_command(self, spec: ContainerSpec) -> list[str]:
        cmd = [*self.command_prefix, "run", "--rm"]

        self._add_userns_flags(cmd, spec)

        # Disable SELinux label confinement (needed for bind mounts: project dir, configs, sockets)
        cmd.extend(["--security-opt", "label=disable"])

        # Interactive/TTY
        if spec.tty:
            cmd.append("-t")
        if spec.stdin_open:
            cmd.append("-i")

        self._add_user_flags(cmd, spec)

        # Working directory
        cmd.extend(["--workdir", spec.working_dir])

        # Network
        if spec.network_mode:
            cmd.extend(["--network", spec.network_mode])

        # Port publishing
        if spec.ports:
            for port in spec.ports:
                cmd.extend(["-p", port])

        # PID namespace
        if spec.pid_mode:
            cmd.extend(["--pid", spec.pid_mode])

        # Runtime identifier
        cmd.extend(["-e", f"YAAS_RUNTIME={self.name}"])

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

        # Security: capabilities (explicit set = drop ALL + add back each)
        if spec.capabilities is not None:
            cmd.extend(["--cap-drop", "ALL"])
            for cap in spec.capabilities:
                cmd.extend(["--cap-add", cap])

        # Security: seccomp profile
        if spec.seccomp_profile:
            cmd.extend(["--security-opt", f"seccomp={spec.seccomp_profile}"])

        # Image and command
        cmd.append(spec.image)
        cmd.extend(spec.command)

        return cmd


class PodmanKrunRuntime(PodmanRuntime):
    """Podman with libkrun MicroVM isolation.

    Uses crun's krun handler to run containers inside lightweight VMs (KVM).
    Requires crun-krun package (provides krun binary + libkrun.so).
    """

    name = "podman-krun"

    def is_available(self) -> bool:
        return super().is_available() and shutil.which("krun") is not None

    # Features incompatible with libkrun MicroVMs (host sockets, FUSE mounts, etc.)
    _INCOMPATIBLE_FEATURES: dict[str, str] = {
        "lxcfs": "lxcfs (not needed, VM has its own /proc)",
        "container_socket": "container socket passthrough (virtiofs can't forward Unix sockets)",
        "clipboard": "clipboard passthrough (virtiofs can't forward Unix sockets)",
        "ssh_agent": "SSH agent forwarding (virtiofs can't forward Unix sockets)",
    }

    def adjust_config(self, config: Config) -> None:
        for field, description in self._INCOMPATIBLE_FEATURES.items():
            if getattr(config, field):
                logger.warning(f"{description} is not supported with libkrun — disabling")
                setattr(config, field, False)
        if config.network_mode == "host":
            logger.warning("--network host is not supported with libkrun — falling back to bridge")
            config.network_mode = "bridge"
        if config.security.capabilities is not None:
            logger.warning("capability restrictions are not supported with libkrun — disabling")
            config.security.capabilities = None

    def _add_userns_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        """No userns for krun — VM boots as root, rootless podman maps UID 0 → host UID."""
        pass

    def _add_user_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        """No --user for krun — VM ignores it. LD_PRELOAD spoofs UID instead."""
        pass

    def _build_command(self, spec: ContainerSpec) -> list[str]:
        cmd = super()._build_command(spec)
        image_idx = cmd.index(spec.image)
        krun_flags: list[str] = [
            # krun's getifaddrs() returns no interfaces, making Nix think it's
            # offline and disabling parallel substitutions. Force substituters on.
            "-e", "NIX_CONFIG=substitute = true",
            "--annotation=run.oci.handler=krun",
        ]
        # Pass fake UID/GID for LD_PRELOAD spoofing in the entrypoint
        if spec.user:
            uid, gid = spec.user.split(":")
            krun_flags[0:0] = [
                "-e", f"YAAS_FAKE_UID={uid}",
                "-e", f"YAAS_FAKE_GID={gid}",
            ]
        cmd[image_idx:image_idx] = krun_flags
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

    def create_volume(self, name: str) -> bool:
        """Create a named volume. Returns True on success."""
        result = subprocess.run(
            [*self.command_prefix, "volume", "create", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.debug(f"Failed to create volume {name}: {result.stderr}")
        return result.returncode == 0

    def remove_volume(self, name: str) -> bool:
        """Remove a named volume. Returns True on success."""
        result = subprocess.run(
            [*self.command_prefix, "volume", "rm", "-f", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.debug(f"Failed to remove volume {name}: {result.stderr}")
        return result.returncode == 0

    def adjust_config(self, config: Config) -> None:
        pass

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

        # Port publishing
        if spec.ports:
            for port in spec.ports:
                cmd.extend(["-p", port])

        # PID namespace
        if spec.pid_mode:
            cmd.extend(["--pid", spec.pid_mode])

        # Runtime identifier
        cmd.extend(["-e", f"YAAS_RUNTIME={self.name}"])

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

        # Security: capabilities (explicit set = drop ALL + add back each)
        if spec.capabilities is not None:
            cmd.extend(["--cap-drop", "ALL"])
            for cap in spec.capabilities:
                cmd.extend(["--cap-add", cap])

        # Security: seccomp profile
        if spec.seccomp_profile:
            cmd.extend(["--security-opt", f"seccomp={spec.seccomp_profile}"])

        # Image and command
        cmd.append(spec.image)
        cmd.extend(spec.command)

        return cmd


def get_runtime(preference: str | None = None) -> ContainerRuntime:
    """Get available container runtime, with optional preference."""
    runtimes: list[
        tuple[str, type[PodmanRuntime] | type[PodmanKrunRuntime] | type[DockerRuntime]]
    ] = [
        ("podman", PodmanRuntime),
        ("podman-krun", PodmanKrunRuntime),
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
