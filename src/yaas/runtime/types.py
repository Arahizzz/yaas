"""Data types and protocol for container runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..config import Config


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

    def list_containers(
        self,
        prefix: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """List containers matching optional name prefix and/or labels."""
        ...

    def inspect_container(self, name: str) -> dict[str, Any] | None:
        """Inspect a container. Returns None if not found."""
        ...

    def adjust_config(self, config: Config) -> None:
        """Adjust config for runtime compatibility. Default: no-op."""
        ...
