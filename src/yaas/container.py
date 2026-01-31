"""Builds ContainerSpec from Config - handles all mount logic."""

from __future__ import annotations

import os
import shutil
from importlib import resources
from pathlib import Path

from .config import Config
from .constants import (
    API_KEYS,
    CACHE_VOLUME,
    MISE_CONFIG_PATH,
    MISE_DATA_VOLUME,
    RUNTIME_IMAGE,
)
from .logging import get_logger
from .platform import (
    get_container_socket_paths,
    get_ssh_agent_socket,
    get_uid_gid,
    is_linux,
    is_macos,
)
from .runtime import ContainerSpec, Mount

logger = get_logger()


def build_container_spec(
    config: Config,
    project_dir: Path,
    command: list[str],
    *,
    tty: bool = True,
    stdin_open: bool = True,
) -> ContainerSpec:
    """Build complete container specification from config.

    Args:
        config: Container configuration
        project_dir: Project directory to mount
        command: Command to run in container
        tty: Allocate a pseudo-TTY (requires stdin to be a TTY)
        stdin_open: Keep stdin open (needed for piped input)
    """
    uid, gid = get_uid_gid()
    home = Path.home()
    sandbox_home = "/home"

    # Build mounts and collect supplementary groups
    mounts, groups = _build_mounts(config, project_dir, home, sandbox_home)

    # Build environment
    environment = _build_environment(config, project_dir, sandbox_home)

    # Use real UID:GID. With --userns=keep-id, this maps correctly in rootless podman.
    container_user = f"{uid}:{gid}"

    return ContainerSpec(
        image=RUNTIME_IMAGE,
        command=command,
        working_dir=str(project_dir),
        user=container_user,
        environment=environment,
        mounts=mounts,
        network_mode="none" if config.no_network else None,
        tty=tty,
        stdin_open=stdin_open,
        groups=groups or None,
        # Resource limits
        memory=config.resources.memory,
        memory_swap=config.resources.memory_swap,
        cpus=config.resources.cpus,
        pids_limit=config.resources.pids_limit,
    )


def _build_mounts(
    config: Config,
    project_dir: Path,
    home: Path,
    sandbox_home: str,
) -> tuple[list[Mount], list[int]]:
    """Assemble all mounts and supplementary groups."""
    mounts: list[Mount] = []
    groups: list[int] = []

    # UID/GID passthrough - mount passwd/group so user is recognized
    # Only on Linux - macOS Docker Desktop handles this differently
    if is_linux():
        mounts.append(Mount("/etc/passwd", "/etc/passwd", read_only=True))
        mounts.append(Mount("/etc/group", "/etc/group", read_only=True))

    # Project directory at real path (critical for docker-in-docker!)
    mounts.append(
        Mount(
            str(project_dir),
            str(project_dir),
            read_only=config.readonly_project,
        )
    )

    # Direct config mounts (enabled by UID passthrough!)
    if config.git_config:
        _add_config_mounts(mounts, home, sandbox_home, [".gitconfig", ".config/git"])

    if config.ai_config:
        _add_config_mounts(
            mounts,
            home,
            sandbox_home,
            [
                ".claude",
                ".claude.json",
                ".codex",
                ".gemini",
                ".config/opencode",
                ".local/share/opencode",
            ],
        )

    # SSH agent
    if config.ssh_agent:
        _add_ssh_agent(mounts)

    # Container socket (docker/podman inside sandbox)
    if config.container_socket:
        _add_container_socket(mounts, groups)

    # Clipboard support (for image pasting)
    if config.clipboard:
        _add_clipboard_support(mounts)

    # Mise tool management support
    _add_mise_support(mounts)

    # User-defined mounts
    for mount_spec in config.mounts:
        mounts.append(_parse_mount_spec(mount_spec, project_dir))

    return mounts, groups


def _add_ssh_agent(mounts: list[Mount]) -> None:
    """Mount SSH agent socket with platform-aware detection."""
    # Use platform-aware socket detection (handles macOS launchd sockets)
    sock_path = get_ssh_agent_socket()
    if not sock_path:
        logger.warning("SSH agent socket not found, skipping SSH agent")
        return

    mounts.append(Mount(str(sock_path), "/ssh-agent"))


def _add_container_socket(mounts: list[Mount], groups: list[int]) -> None:
    """Mount container runtime socket for docker-in-docker.

    Uses platform-aware socket detection to support Docker Desktop and
    Colima on macOS, in addition to standard Linux socket paths.
    """
    for sock_path in get_container_socket_paths():
        if sock_path.exists():
            # Mount at same path so docker/podman CLI works unchanged
            mounts.append(Mount(str(sock_path), str(sock_path)))
            # Add socket's group to supplementary groups for access
            # Skip on macOS - GID from Docker Desktop VM isn't useful
            if is_linux():
                sock_gid = sock_path.stat().st_gid
                if sock_gid not in groups:
                    groups.append(sock_gid)
            return

    logger.warning("No container socket found, docker/podman won't work inside sandbox")


def _add_clipboard_support(mounts: list[Mount]) -> None:
    """Mount display sockets for clipboard access (Wayland or X11).

    Only works on Linux where display server sockets can be mounted.
    macOS/Windows don't have accessible display sockets.
    """
    # Clipboard via display sockets only works on Linux
    if not is_linux():
        if is_macos():
            logger.warning(
                "Clipboard not supported on macOS (no display server sockets available)"
            )
        return

    wayland_display = os.environ.get("WAYLAND_DISPLAY")
    xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")

    # Prefer Wayland if available - mount only the socket file (not whole dir)
    # to leave /run/user/$UID writable for GPG sockets
    if wayland_display and xdg_runtime_dir:
        wayland_socket = Path(xdg_runtime_dir) / wayland_display
        if wayland_socket.exists():
            mounts.append(Mount(str(wayland_socket), str(wayland_socket), read_only=True))
            return

    # Fall back to X11
    x_display = os.environ.get("DISPLAY")
    if x_display:
        x11_socket = Path("/tmp/.X11-unix")
        if x11_socket.exists():
            mounts.append(Mount(str(x11_socket), str(x11_socket), read_only=True))
            return

    logger.warning("No display server detected, clipboard won't work inside sandbox")


def _add_mise_support(mounts: list[Mount]) -> None:
    """Add mise volumes and config mount for tool management."""
    sandbox_home = "/home"

    # Named volumes for persistence between runs
    mounts.append(Mount(MISE_DATA_VOLUME, f"{sandbox_home}/.local/share/mise", type="volume"))
    mounts.append(Mount(CACHE_VOLUME, f"{sandbox_home}/.cache", type="volume"))

    # Ensure config directory exists
    config_dir = MISE_CONFIG_PATH.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    # Auto-create default mise.toml if missing (copy from vendored file)
    if not MISE_CONFIG_PATH.exists():
        with resources.files("yaas.data").joinpath("mise.toml").open("rb") as src:
            with open(MISE_CONFIG_PATH, "wb") as dst:
                shutil.copyfileobj(src, dst)
        logger.info(f"Created default mise config at {MISE_CONFIG_PATH}")
        logger.info("Update this file to customize which tools are available in your sandbox")

    # Mount mise config
    mounts.append(Mount(str(MISE_CONFIG_PATH), f"{sandbox_home}/.config/mise/config.toml"))


def _add_config_mounts(
    mounts: list[Mount],
    home: Path,
    sandbox_home: str,
    configs: list[str],
) -> None:
    """Add config file/directory mounts."""
    for config_path in configs:
        src = home / config_path
        if src.exists():
            dst = f"{sandbox_home}/{config_path}"
            mounts.append(Mount(str(src), dst))


def _build_environment(
    config: Config,
    project_dir: Path,
    sandbox_home: str,
) -> dict[str, str]:
    """Build environment variables."""
    env: dict[str, str] = {
        "HOME": sandbox_home,
        "PROJECT_PATH": str(project_dir),
        "YAAS": "1",
        # Make npm use XDG-compliant cache path
        "npm_config_cache": f"{sandbox_home}/.cache/npm",
        # Mise configuration
        "MISE_DATA_DIR": f"{sandbox_home}/.local/share/mise",
        "MISE_CACHE_DIR": f"{sandbox_home}/.cache/mise",
        # Trust project mise configs automatically
        "MISE_TRUSTED_CONFIG_PATHS": str(project_dir),
        # Auto-confirm trust prompts
        "MISE_YES": "1",
    }

    # Forward TERM
    if term := os.environ.get("TERM"):
        env["TERM"] = term

    # Forward API keys
    if config.forward_api_keys:
        for key in API_KEYS:
            if value := os.environ.get(key):
                env[key] = value

    # SSH agent (use platform-aware socket detection)
    if config.ssh_agent and get_ssh_agent_socket():
        env["SSH_AUTH_SOCK"] = "/ssh-agent"
        # Override git SSH signing to use ssh-keygen with agent (not 1Password's op-ssh-sign)
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "gpg.ssh.program"
        env["GIT_CONFIG_VALUE_0"] = "ssh-keygen"

    # Clipboard support (forward display env vars)
    if config.clipboard:
        _add_clipboard_environment(env)

    # User-defined env
    env.update(config.env)

    return env


def _add_clipboard_environment(env: dict[str, str]) -> None:
    """Forward display-related environment variables for clipboard tools.

    Only applies on Linux where display servers are available.
    """
    if not is_linux():
        return

    # Wayland
    if wayland_display := os.environ.get("WAYLAND_DISPLAY"):
        env["WAYLAND_DISPLAY"] = wayland_display
    if xdg_runtime_dir := os.environ.get("XDG_RUNTIME_DIR"):
        env["XDG_RUNTIME_DIR"] = xdg_runtime_dir

    # X11
    if x_display := os.environ.get("DISPLAY"):
        env["DISPLAY"] = x_display


def _parse_mount_spec(spec: str, project_dir: Path) -> Mount:
    """Parse mount spec like '~/data:/data:ro'."""
    parts = spec.split(":")

    src = parts[0]
    dst = parts[1] if len(parts) > 1 else src
    opts = parts[2] if len(parts) > 2 else ""

    # Expand ~ and relative paths
    src_path = Path(src).expanduser()
    if not src_path.is_absolute():
        src_path = project_dir / src_path

    return Mount(
        str(src_path),
        dst,
        read_only="ro" in opts,
    )
