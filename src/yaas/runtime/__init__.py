"""Container runtime abstraction with Podman/Docker implementations."""

from __future__ import annotations

from .docker import DockerRuntime
from .krun import PodmanKrunRuntime
from .podman import PodmanRuntime
from .types import ContainerRuntime, ContainerSpec, ExecSpec, Mount, _format_mount

__all__ = [
    "ContainerRuntime",
    "ContainerSpec",
    "DockerRuntime",
    "ExecSpec",
    "Mount",
    "PodmanKrunRuntime",
    "PodmanRuntime",
    "_format_mount",
    "get_runtime",
]


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
