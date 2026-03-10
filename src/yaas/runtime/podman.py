"""Podman container runtime implementation."""

from __future__ import annotations

import shutil

from ..platform import is_linux
from .base import BaseRuntime
from .types import ContainerSpec


class PodmanRuntime(BaseRuntime):
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

    def _add_userns_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        """No userns — rootless podman maps container UID 0 -> host UID."""
        pass

    def _add_runtime_specific_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        self._add_userns_flags(cmd, spec)

    def _add_user_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        """Pass host UID/GID for entrypoint user setup."""
        if spec.user:
            uid, gid = spec.user.split(":")
            cmd.extend(["-e", f"YAAS_HOST_UID={uid}", "-e", f"YAAS_HOST_GID={gid}"])
