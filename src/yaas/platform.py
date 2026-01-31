"""Platform detection and cross-platform compatibility helpers."""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path


class PlatformError(Exception):
    """Raised when an operation is not supported on the current platform."""

    pass


def is_linux() -> bool:
    """Check if running on Linux."""
    return sys.platform == "linux"


def is_macos() -> bool:
    """Check if running on macOS."""
    return sys.platform == "darwin"


def is_windows() -> bool:
    """Check if running on Windows (native, not WSL)."""
    return sys.platform == "win32"


def is_wsl() -> bool:
    """Check if running in WSL (Windows Subsystem for Linux)."""
    if sys.platform != "linux":
        return False
    # WSL sets this in /proc/version
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def get_uid_gid() -> tuple[int, int]:
    """Get UID and GID for container user mapping.

    On Linux: Returns actual UID/GID for proper file ownership.
    On macOS: Returns 1000:1000 (Docker Desktop VM default).
    """
    if is_linux():
        return os.getuid(), os.getgid()
    elif is_macos():
        # Docker Desktop on macOS runs containers in a Linux VM.
        # UID/GID mapping doesn't work the same way - use defaults.
        return 1000, 1000
    else:
        # Windows should use WSL, but fallback to defaults
        return 1000, 1000


def get_ssh_agent_socket() -> Path | None:
    """Get SSH agent socket path, with platform-specific detection.

    Returns None if no SSH agent socket is found.
    """
    # First check SSH_AUTH_SOCK (works on all platforms)
    ssh_sock = os.environ.get("SSH_AUTH_SOCK")
    if ssh_sock:
        sock_path = Path(ssh_sock)
        if sock_path.exists():
            return sock_path

    # macOS: Check launchd socket locations
    if is_macos():
        # macOS SSH agent sockets are in /private/tmp/com.apple.launchd.*/Listeners
        pattern = "/private/tmp/com.apple.launchd.*/Listeners"
        matches = glob.glob(pattern)
        for match in matches:
            sock_path = Path(match)
            if sock_path.exists():
                return sock_path

    return None


def get_container_socket_paths(*, docker_only: bool = False) -> list[Path]:
    """Get possible container runtime socket paths.

    Returns platform-specific paths for Docker (and optionally Podman) sockets.
    Custom socket paths can be specified via DOCKER_HOST environment variable.

    Args:
        docker_only: If True, only return Docker socket paths (excludes Podman).
                     Used by DockerRuntime for availability checking.
    """
    paths: list[Path] = []
    home = Path.home()

    # Check DOCKER_HOST for custom socket path (highest priority)
    docker_host = os.environ.get("DOCKER_HOST", "")
    if docker_host.startswith("unix://"):
        paths.append(Path(docker_host[7:]))  # Strip unix:// prefix

    if is_macos():
        # Docker Desktop socket locations
        paths.append(home / ".docker/run/docker.sock")
        paths.append(Path("/var/run/docker.sock"))  # Symlink by Docker Desktop
    else:
        # Linux socket paths
        uid = os.getuid() if is_linux() else 1000

        # Podman sockets (skip if docker_only)
        if not docker_only:
            paths.append(Path(f"/run/user/{uid}/podman/podman.sock"))
            paths.append(Path("/run/podman/podman.sock"))

        # Docker sockets
        paths.append(Path("/var/run/docker.sock"))
        paths.append(Path("/run/docker.sock"))

        # XDG_RUNTIME_DIR for rootless docker
        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
        if xdg_runtime:
            paths.append(Path(xdg_runtime) / "docker.sock")

    return paths


def check_platform_support() -> None:
    """Check if the current platform is supported.

    Raises PlatformError with helpful message for unsupported platforms.
    """
    if is_windows():
        raise PlatformError(
            "YAAS does not support native Windows.\n"
            "Please run YAAS inside WSL2 (Windows Subsystem for Linux).\n"
            "\n"
            "To get started:\n"
            "  1. Install WSL2: wsl --install\n"
            "  2. Open WSL2 terminal\n"
            "  3. Install Docker or Podman inside WSL2\n"
            "  4. Install YAAS: pip install yaas"
        )
