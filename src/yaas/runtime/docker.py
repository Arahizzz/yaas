"""Docker container runtime implementation."""

from __future__ import annotations

import os
import shutil
import subprocess

from ..platform import get_container_socket_paths
from .base import BaseRuntime
from .types import ContainerSpec


def _can_access_docker_socket() -> bool:
    """Check if Docker socket is accessible without sudo."""
    for sock_path in get_container_socket_paths(docker_only=True):
        if sock_path.exists() and os.access(sock_path, os.R_OK | os.W_OK):
            return True
    return False


class DockerRuntime(BaseRuntime):
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

    def _add_runtime_specific_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        pass

    def _add_user_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        """Pass host UID/GID and rootful flag for entrypoint user setup."""
        if spec.user:
            uid, gid = spec.user.split(":")
            cmd.extend(["-e", f"YAAS_HOST_UID={uid}", "-e", f"YAAS_HOST_GID={gid}"])
        if not self._is_rootless():
            cmd.extend(["-e", "YAAS_DOCKER_ROOTFUL=1"])
