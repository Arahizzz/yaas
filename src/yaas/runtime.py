"""Container runtime abstraction with Podman/Docker implementations."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

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

    # Devices
    devices: list[str] | None = None  # e.g., ["/dev/fuse"]

    # Container name (for persistent containers)
    name: str | None = None

    # Entrypoint override (Docker semantics)
    entrypoint: list[str] | None = None

    # Use --init (tini/catatonit as PID 1)
    init: bool = False

    # Container labels
    labels: dict[str, str] = field(default_factory=dict)

    # Security
    privileged: bool = False  # --privileged (all caps, no seccomp, all devices)
    capabilities: list[str] | None = None  # Exact cap set; triggers --cap-drop ALL + --cap-add each
    seccomp_profile: str | None = None  # path to seccomp JSON profile


@dataclass
class ExecSpec:
    """Specification for exec-ing into a running container."""

    container_name: str
    command: list[str]
    working_dir: str | None = None
    user: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    tty: bool = True
    stdin_open: bool = True


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

    def create_container(self, spec: ContainerSpec) -> bool:
        """Create a container without starting it. Returns True on success."""
        ...

    def start_container(self, name: str) -> bool:
        """Start a stopped container. Returns True on success."""
        ...

    def stop_container(self, name: str) -> bool:
        """Stop a running container. Returns True on success."""
        ...

    def remove_container(self, name: str, force: bool = False) -> bool:
        """Remove a container. Returns True on success."""
        ...

    def exec_container(self, spec: ExecSpec) -> int:
        """Exec into a running container, return exit code."""
        ...

    def list_containers(self, prefix: str) -> list[dict[str, Any]]:
        """List containers matching a name prefix."""
        ...

    def inspect_container(self, name: str) -> dict[str, Any] | None:
        """Inspect a container. Returns None if not found."""
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
        """No userns — rootless podman maps container UID 0 → host UID."""
        pass

    def _add_user_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        """Pass host UID/GID for entrypoint user setup."""
        if spec.user:
            uid, gid = spec.user.split(":")
            cmd.extend(["-e", f"YAAS_HOST_UID={uid}", "-e", f"YAAS_HOST_GID={gid}"])

    def _build_common_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        """Append flags shared between run and create commands."""
        self._add_userns_flags(cmd, spec)

        # Disable SELinux label confinement (needed for bind mounts)
        cmd.extend(["--security-opt", "label=disable"])

        # Interactive/TTY
        if spec.tty:
            cmd.append("-t")
        if spec.stdin_open:
            cmd.append("-i")

        self._add_user_flags(cmd, spec)

        # Container name
        if spec.name:
            cmd.extend(["--name", spec.name])

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

        # Init (tini/catatonit as PID 1)
        if spec.init:
            cmd.append("--init")

        # Labels
        for key, value in spec.labels.items():
            cmd.extend(["--label", f"{key}={value}"])

        # Entrypoint override
        if spec.entrypoint is not None:
            cmd.extend(["--entrypoint", json.dumps(spec.entrypoint)])

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
            swap = spec.memory_swap or spec.memory
            cmd.extend(["--memory-swap", swap])

        if spec.cpus:
            cmd.extend(["--cpus", str(spec.cpus)])

        if spec.pids_limit:
            cmd.extend(["--pids-limit", str(spec.pids_limit)])

        # Devices
        if spec.devices:
            for device in spec.devices:
                cmd.extend(["--device", device])

        # Security
        if spec.privileged:
            cmd.append("--privileged")
        else:
            if spec.capabilities is not None:
                cmd.extend(["--cap-drop", "ALL"])
                for cap in spec.capabilities:
                    cmd.extend(["--cap-add", cap])
            if spec.seccomp_profile:
                cmd.extend(["--security-opt", f"seccomp={spec.seccomp_profile}"])

    def _build_command(self, spec: ContainerSpec) -> list[str]:
        cmd = [*self.command_prefix, "run", "--rm"]
        self._build_common_flags(cmd, spec)
        cmd.append(spec.image)
        cmd.extend(spec.command)
        return cmd

    def _build_create_command(self, spec: ContainerSpec) -> list[str]:
        cmd = [*self.command_prefix, "create"]
        self._build_common_flags(cmd, spec)
        cmd.append(spec.image)
        cmd.extend(spec.command)
        return cmd

    def _build_exec_command(self, spec: ExecSpec) -> list[str]:
        cmd = [*self.command_prefix, "exec"]
        if spec.tty:
            cmd.append("-t")
        if spec.stdin_open:
            cmd.append("-i")
        if spec.working_dir:
            cmd.extend(["--workdir", spec.working_dir])
        if spec.user:
            cmd.extend(["--user", spec.user])
        for key, value in spec.environment.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.append(spec.container_name)
        cmd.extend(spec.command)
        return cmd

    def create_container(self, spec: ContainerSpec) -> bool:
        cmd = self._build_create_command(spec)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.debug(f"Failed to create container: {result.stderr}")
        return result.returncode == 0

    def start_container(self, name: str) -> bool:
        result = subprocess.run(
            [*self.command_prefix, "start", name], capture_output=True, text=True
        )
        if result.returncode != 0:
            logger.debug(f"Failed to start container {name}: {result.stderr}")
        return result.returncode == 0

    def stop_container(self, name: str) -> bool:
        result = subprocess.run(
            [*self.command_prefix, "stop", name], capture_output=True, text=True
        )
        if result.returncode != 0:
            logger.debug(f"Failed to stop container {name}: {result.stderr}")
        return result.returncode == 0

    def remove_container(self, name: str, force: bool = False) -> bool:
        cmd = [*self.command_prefix, "rm"]
        if force:
            cmd.append("-f")
        cmd.append(name)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.debug(f"Failed to remove container {name}: {result.stderr}")
        return result.returncode == 0

    def exec_container(self, spec: ExecSpec) -> int:
        cmd = self._build_exec_command(spec)
        result = subprocess.run(cmd)
        return result.returncode

    def list_containers(self, prefix: str) -> list[dict[str, Any]]:
        result = subprocess.run(
            [
                *self.command_prefix,
                "ps",
                "-a",
                "--filter",
                f"name={prefix}",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        try:
            # Podman outputs one JSON object per line; Docker outputs a JSON array
            lines = result.stdout.strip().splitlines()
            containers: list[dict[str, Any]] = []
            for line in lines:
                if line.strip():
                    parsed = json.loads(line)
                    if isinstance(parsed, list):
                        containers.extend(parsed)
                    else:
                        containers.append(parsed)
            return containers
        except json.JSONDecodeError:
            return []

    def inspect_container(self, name: str) -> dict[str, Any] | None:
        result = subprocess.run(
            [*self.command_prefix, "inspect", name], capture_output=True, text=True
        )
        if result.returncode != 0:
            return None
        try:
            data = json.loads(result.stdout)
            result_data: dict[str, Any] = data[0] if isinstance(data, list) and data else data
            return result_data
        except json.JSONDecodeError:
            return None


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
        "clipboard": "clipboard passthrough (virtiofs can't forward Unix sockets)",
        "ssh_agent": "SSH agent forwarding (virtiofs can't forward Unix sockets)",
    }

    def adjust_config(self, config: Config) -> None:
        for feat, description in self._INCOMPATIBLE_FEATURES.items():
            if getattr(config, feat):
                logger.warning(f"{description} is not supported with libkrun — disabling")
                setattr(config, feat, False)
        if config.network_mode == "host":
            logger.warning("--network host is not supported with libkrun — falling back to bridge")
            config.network_mode = "bridge"
        if config.security.capabilities is not None:
            logger.warning("capability restrictions are not supported with libkrun — disabling")
            config.security.capabilities = None

    def _inject_krun_flags(self, cmd: list[str], spec: ContainerSpec) -> list[str]:
        """Insert krun-specific flags before the image argument."""
        image_idx = cmd.index(spec.image)
        krun_flags: list[str] = [
            "-e",
            "NIX_CONFIG=substitute = true",
            "--annotation=run.oci.handler=krun",
        ]
        cmd[image_idx:image_idx] = krun_flags
        # Strip --init (VM has its own init)
        if "--init" in cmd:
            cmd.remove("--init")
        return cmd

    def _build_command(self, spec: ContainerSpec) -> list[str]:
        cmd = super()._build_command(spec)
        return self._inject_krun_flags(cmd, spec)

    def _build_create_command(self, spec: ContainerSpec) -> list[str]:
        cmd = super()._build_create_command(spec)
        return self._inject_krun_flags(cmd, spec)


class DockerRuntime:
    """Docker implementation using CLI subprocess."""

    name = "docker"

    def __init__(self) -> None:
        self._use_sudo = False
        self._rootless: bool | None = None  # Lazy-detected
        # Check if we need sudo to access docker socket
        if not _can_access_docker_socket() and shutil.which("sudo") is not None:
            self._use_sudo = True

    def _is_rootless(self) -> bool:
        """Detect if Docker is running in rootless mode (cached)."""
        if self._rootless is None:
            try:
                result = subprocess.run(
                    [*self.command_prefix, "info", "-f", "{{.SecurityOptions}}"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self._rootless = "rootless" in (result.stdout or "")
            except (subprocess.TimeoutExpired, OSError):
                self._rootless = False
        return self._rootless

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

    def _build_common_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        """Append flags shared between run and create commands."""
        # Interactive/TTY
        if spec.tty:
            cmd.append("-t")
        if spec.stdin_open:
            cmd.append("-i")

        # User identity
        if spec.user:
            uid, gid = spec.user.split(":")
            cmd.extend(["-e", f"YAAS_HOST_UID={uid}", "-e", f"YAAS_HOST_GID={gid}"])
        if not self._is_rootless():
            cmd.extend(["-e", "YAAS_DOCKER_ROOTFUL=1"])

        # Container name
        if spec.name:
            cmd.extend(["--name", spec.name])

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

        # Init
        if spec.init:
            cmd.append("--init")

        # Labels
        for key, value in spec.labels.items():
            cmd.extend(["--label", f"{key}={value}"])

        # Entrypoint override
        if spec.entrypoint is not None:
            cmd.extend(["--entrypoint", json.dumps(spec.entrypoint)])

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
            swap = spec.memory_swap or spec.memory
            cmd.extend(["--memory-swap", swap])

        if spec.cpus:
            cmd.extend(["--cpus", str(spec.cpus)])

        if spec.pids_limit:
            cmd.extend(["--pids-limit", str(spec.pids_limit)])

        # Devices
        if spec.devices:
            for device in spec.devices:
                cmd.extend(["--device", device])

        # Security
        if spec.privileged:
            cmd.append("--privileged")
        else:
            if spec.capabilities is not None:
                cmd.extend(["--cap-drop", "ALL"])
                for cap in spec.capabilities:
                    cmd.extend(["--cap-add", cap])
            if spec.seccomp_profile:
                cmd.extend(["--security-opt", f"seccomp={spec.seccomp_profile}"])

    def _build_command(self, spec: ContainerSpec) -> list[str]:
        cmd = [*self.command_prefix, "run", "--rm"]
        self._build_common_flags(cmd, spec)
        cmd.append(spec.image)
        cmd.extend(spec.command)
        return cmd

    def _build_create_command(self, spec: ContainerSpec) -> list[str]:
        cmd = [*self.command_prefix, "create"]
        self._build_common_flags(cmd, spec)
        cmd.append(spec.image)
        cmd.extend(spec.command)
        return cmd

    def _build_exec_command(self, spec: ExecSpec) -> list[str]:
        cmd = [*self.command_prefix, "exec"]
        if spec.tty:
            cmd.append("-t")
        if spec.stdin_open:
            cmd.append("-i")
        if spec.working_dir:
            cmd.extend(["--workdir", spec.working_dir])
        if spec.user:
            cmd.extend(["--user", spec.user])
        for key, value in spec.environment.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.append(spec.container_name)
        cmd.extend(spec.command)
        return cmd

    def create_container(self, spec: ContainerSpec) -> bool:
        cmd = self._build_create_command(spec)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.debug(f"Failed to create container: {result.stderr}")
        return result.returncode == 0

    def start_container(self, name: str) -> bool:
        result = subprocess.run(
            [*self.command_prefix, "start", name], capture_output=True, text=True
        )
        if result.returncode != 0:
            logger.debug(f"Failed to start container {name}: {result.stderr}")
        return result.returncode == 0

    def stop_container(self, name: str) -> bool:
        result = subprocess.run(
            [*self.command_prefix, "stop", name], capture_output=True, text=True
        )
        if result.returncode != 0:
            logger.debug(f"Failed to stop container {name}: {result.stderr}")
        return result.returncode == 0

    def remove_container(self, name: str, force: bool = False) -> bool:
        cmd = [*self.command_prefix, "rm"]
        if force:
            cmd.append("-f")
        cmd.append(name)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.debug(f"Failed to remove container {name}: {result.stderr}")
        return result.returncode == 0

    def exec_container(self, spec: ExecSpec) -> int:
        cmd = self._build_exec_command(spec)
        result = subprocess.run(cmd)
        return result.returncode

    def list_containers(self, prefix: str) -> list[dict[str, Any]]:
        result = subprocess.run(
            [
                *self.command_prefix,
                "ps",
                "-a",
                "--filter",
                f"name={prefix}",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        try:
            lines = result.stdout.strip().splitlines()
            containers: list[dict[str, Any]] = []
            for line in lines:
                if line.strip():
                    parsed = json.loads(line)
                    if isinstance(parsed, list):
                        containers.extend(parsed)
                    else:
                        containers.append(parsed)
            return containers
        except json.JSONDecodeError:
            return []

    def inspect_container(self, name: str) -> dict[str, Any] | None:
        result = subprocess.run(
            [*self.command_prefix, "inspect", name], capture_output=True, text=True
        )
        if result.returncode != 0:
            return None
        try:
            data = json.loads(result.stdout)
            result_data: dict[str, Any] = data[0] if isinstance(data, list) and data else data
            return result_data
        except json.JSONDecodeError:
            return None


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
