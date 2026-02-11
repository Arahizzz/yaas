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
    CLONE_WORKSPACE,
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
    is_wsl,
)
from .runtime import ContainerSpec, Mount

logger = get_logger()

# WSLg socket paths (Windows Subsystem for Linux GUI support)
_WSLG_RUNTIME_DIR = Path("/mnt/wslg/runtime-dir")
_WSLG_PULSE_SERVER = Path("/mnt/wslg/PulseServer")


def extract_repo_name(url: str) -> str:
    """Extract repository name from a git URL.

    Handles both HTTPS and SSH URLs:
    - https://github.com/user/repo.git -> repo
    - git@github.com:user/repo.git -> repo
    - https://github.com/user/repo -> repo

    Raises:
        ValueError: If URL is empty or repo name cannot be extracted
    """
    if not url or not url.strip():
        raise ValueError("Empty repository URL")

    # Strip whitespace and trailing slashes
    url = url.strip().rstrip("/")

    # Remove query parameters and fragments
    for sep in ("?", "#"):
        if sep in url:
            url = url.split(sep, 1)[0]

    # Remove trailing .git if present
    if url.endswith(".git"):
        url = url[:-4]

    # Extract the last path component
    if "/" in url:
        name = url.rsplit("/", 1)[-1]
    elif ":" in url:
        # SSH format: git@github.com:user/repo
        name = url.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
    else:
        name = url

    if not name:
        raise ValueError(f"Could not extract repository name from URL: {url}")

    return name


def build_clone_spec(
    config: Config,
    clone_url: str,
    clone_volume: str,
    repo_name: str,
    ref: str | None = None,
) -> ContainerSpec:
    """Build container spec for cloning a git repository.

    This creates a minimal container that runs git clone into the ephemeral volume.
    Always has network access (even if config.no_network is True) since it needs
    to fetch from remote.

    Args:
        config: Configuration object
        clone_url: Git repository URL to clone
        clone_volume: Name of the volume to clone into
        repo_name: Name of the repository (used for subdirectory)
        ref: Optional git ref (tag or branch) to checkout via --branch
    """
    uid, gid = get_uid_gid()
    sandbox_home = "/home"

    mounts: list[Mount] = []

    # UID/GID passthrough on Linux
    if is_linux():
        mounts.append(Mount("/etc/passwd", "/etc/passwd", read_only=True))
        mounts.append(Mount("/etc/group", "/etc/group", read_only=True))

    # Mount the clone volume at workspace
    mounts.append(Mount(clone_volume, CLONE_WORKSPACE, type="volume"))

    # SSH agent for private repos
    if config.ssh_agent:
        _add_ssh_agent(mounts)

    environment: dict[str, str] = {
        "HOME": sandbox_home,
    }

    # SSH agent environment
    if config.ssh_agent and get_ssh_agent_socket():
        environment["SSH_AUTH_SOCK"] = "/ssh-agent"

    # Forward TERM
    if term := os.environ.get("TERM"):
        environment["TERM"] = term

    container_user = f"{uid}:{gid}"
    clone_path = f"{CLONE_WORKSPACE}/{repo_name}"

    # Build git clone command
    clone_cmd = ["git", "clone", "--depth", "1"]
    if ref:
        clone_cmd.extend(["--branch", ref])
    clone_cmd.extend([clone_url, clone_path])

    return ContainerSpec(
        image=RUNTIME_IMAGE,
        command=clone_cmd,
        working_dir=CLONE_WORKSPACE,
        user=container_user,
        environment=environment,
        mounts=mounts,
        network_mode=None,  # Always need network for cloning
        tty=False,
        stdin_open=False,
        # Resource limits from config
        memory=config.resources.memory,
        memory_swap=config.resources.memory_swap,
        cpus=config.resources.cpus,
        pids_limit=config.resources.pids_limit,
    )


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

    # Build mounts and collect supplementary groups and devices
    mounts, groups, devices = _build_mounts(config, project_dir, home, sandbox_home)

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
        network_mode=config.network_mode,
        tty=tty,
        stdin_open=stdin_open,
        groups=groups or None,
        pid_mode=config.pid_mode,
        devices=devices or None,
        # Resource limits
        memory=config.resources.memory,
        memory_swap=config.resources.memory_swap,
        cpus=config.resources.cpus,
        pids_limit=config.resources.pids_limit,
    )


def build_clone_work_spec(
    config: Config,
    clone_volume: str,
    repo_name: str,
    command: list[str],
    *,
    tty: bool = True,
    stdin_open: bool = True,
) -> ContainerSpec:
    """Build container spec for working in a cloned repository.

    This is used for the work container in clone mode, after the repo has been
    cloned into the ephemeral volume.
    """
    uid, gid = get_uid_gid()
    home = Path.home()
    sandbox_home = "/home"
    working_dir = f"{CLONE_WORKSPACE}/{repo_name}"

    mounts: list[Mount] = []
    groups: list[int] = []
    devices: list[str] = []

    # UID/GID passthrough on Linux
    if is_linux():
        mounts.append(Mount("/etc/passwd", "/etc/passwd", read_only=True))
        mounts.append(Mount("/etc/group", "/etc/group", read_only=True))

    # Mount clone volume at /workspace
    mounts.append(Mount(clone_volume, CLONE_WORKSPACE, type="volume"))

    # Add standard mounts (config, SSH, clipboard, mise, etc.)
    _add_optional_mounts(config, mounts, groups, devices, home, sandbox_home)

    # Build environment (use working_dir as project path for mise trust)
    environment = _build_environment(config, Path(working_dir), sandbox_home)

    container_user = f"{uid}:{gid}"

    return ContainerSpec(
        image=RUNTIME_IMAGE,
        command=command,
        working_dir=working_dir,
        user=container_user,
        environment=environment,
        mounts=mounts,
        network_mode=config.network_mode,
        tty=tty,
        stdin_open=stdin_open,
        groups=groups or None,
        pid_mode=config.pid_mode,
        devices=devices or None,
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
) -> tuple[list[Mount], list[int], list[str]]:
    """Assemble all mounts, supplementary groups, and devices."""
    mounts: list[Mount] = []
    groups: list[int] = []
    devices: list[str] = []

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

    # Add optional mounts
    _add_optional_mounts(config, mounts, groups, devices, home, sandbox_home)

    # User-defined mounts
    for mount_spec in config.mounts:
        mounts.append(_parse_mount_spec(mount_spec, project_dir))

    return mounts, groups, devices


def _add_optional_mounts(
    config: Config,
    mounts: list[Mount],
    groups: list[int],
    devices: list[str],
    home: Path,
    sandbox_home: str,
) -> None:
    """Add optional mounts based on config (git, AI, SSH, display, clipboard, etc.)."""
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
        # Mount IDE lock directory as read-only to prevent container from deleting lock files
        ide_dir = home / ".claude" / "ide"
        if ide_dir.exists():
            mounts.append(Mount(str(ide_dir), f"{sandbox_home}/.claude/ide", read_only=True))

    if config.ssh_agent:
        _add_ssh_agent(mounts)

    if config.container_socket:
        _add_container_socket(mounts, groups)

    # Display supersedes clipboard (read-write superset)
    if config.display:
        _add_display_support(mounts)
    elif config.clipboard:
        _add_clipboard_support(mounts)

    if config.dbus:
        _add_dbus_support(mounts)

    if config.gpu:
        _add_gpu_support(devices, groups)

    if config.audio:
        _add_audio_support(mounts)

    _add_mise_support(mounts)


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


def _add_display_sockets(mounts: list[Mount], *, read_only: bool) -> bool:
    """Mount display sockets (Wayland or X11). Returns True if any socket was mounted.

    Only works on Linux where display server sockets can be mounted.
    Supports WSLg paths as fallback on WSL2.
    """
    if not is_linux():
        if is_macos():
            logger.warning("Display sockets not available on macOS")
        return False

    wayland_display = os.environ.get("WAYLAND_DISPLAY")
    xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")

    # Prefer Wayland if available - mount only the socket file (not whole dir)
    if wayland_display:
        # Try standard path first, then WSLg fallback
        candidates = []
        if xdg_runtime_dir:
            candidates.append(Path(xdg_runtime_dir) / wayland_display)
        if is_wsl():
            candidates.append(_WSLG_RUNTIME_DIR / wayland_display)

        for wayland_socket in candidates:
            if wayland_socket.exists():
                mounts.append(
                    Mount(str(wayland_socket), str(wayland_socket), read_only=read_only)
                )
                return True

    # Fall back to X11 (same path on native Linux and WSLg)
    x_display = os.environ.get("DISPLAY")
    if x_display:
        x11_socket = Path("/tmp/.X11-unix")
        if x11_socket.exists():
            mounts.append(Mount(str(x11_socket), str(x11_socket), read_only=read_only))
            # Mount .Xauthority for X11 authentication
            xauthority = os.environ.get("XAUTHORITY")
            if xauthority:
                xauth_path = Path(xauthority)
            else:
                xauth_path = Path.home() / ".Xauthority"
            if xauth_path.exists():
                mounts.append(Mount(str(xauth_path), str(xauth_path), read_only=True))
            return True

    return False


def _add_display_support(mounts: list[Mount]) -> None:
    """Mount display sockets read-write for full GUI app rendering."""
    if not _add_display_sockets(mounts, read_only=False):
        logger.warning("No display server detected, display passthrough won't work inside sandbox")


def _add_clipboard_support(mounts: list[Mount]) -> None:
    """Mount display sockets read-only for clipboard access."""
    if not _add_display_sockets(mounts, read_only=True):
        logger.warning("No display server detected, clipboard won't work inside sandbox")


def _add_dbus_support(mounts: list[Mount]) -> None:
    """Mount D-Bus session bus socket for inter-process communication."""
    if not is_linux():
        if is_macos():
            logger.warning("D-Bus not supported on macOS")
        return

    if is_wsl():
        logger.warning("D-Bus session bus not available in WSL2")
        return

    xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not xdg_runtime_dir:
        logger.warning("XDG_RUNTIME_DIR not set, D-Bus session bus won't work inside sandbox")
        return

    bus_socket = Path(xdg_runtime_dir) / "bus"
    if bus_socket.exists():
        mounts.append(Mount(str(bus_socket), str(bus_socket)))
    else:
        logger.warning("D-Bus session bus socket not found, D-Bus won't work inside sandbox")


def _add_gpu_support(devices: list[str], groups: list[int]) -> None:
    """Add GPU device passthrough (/dev/dri)."""
    if not is_linux():
        if is_macos():
            logger.warning("GPU passthrough not supported on macOS")
        return

    dri_path = Path("/dev/dri")
    if not dri_path.exists():
        logger.warning("/dev/dri not found, GPU passthrough won't work inside sandbox")
        return

    devices.append("/dev/dri")

    # Add render node GID for access permissions
    render_node = Path("/dev/dri/renderD128")
    if render_node.exists():
        render_gid = render_node.stat().st_gid
        if render_gid not in groups:
            groups.append(render_gid)


def _add_audio_support(mounts: list[Mount]) -> None:
    """Mount audio sockets (PipeWire and/or PulseAudio).

    On PipeWire systems, both the native socket and the PulseAudio compatibility
    socket are mounted so that both PipeWire-native and PulseAudio clients work.
    On WSL2, the WSLg PulseAudio socket is used.
    """
    if not is_linux():
        if is_macos():
            logger.warning("Audio passthrough not supported on macOS")
        return

    found = False
    xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")

    if xdg_runtime_dir:
        # PipeWire native socket
        pipewire_socket = Path(xdg_runtime_dir) / "pipewire-0"
        if pipewire_socket.exists():
            mounts.append(Mount(str(pipewire_socket), str(pipewire_socket)))
            found = True

        # PulseAudio socket (standalone or PipeWire compatibility layer via pipewire-pulse)
        pulse_socket = Path(xdg_runtime_dir) / "pulse" / "native"
        if pulse_socket.exists():
            mounts.append(Mount(str(pulse_socket), str(pulse_socket)))
            found = True

    # WSLg PulseAudio socket fallback
    if not found and is_wsl() and _WSLG_PULSE_SERVER.exists():
        mounts.append(Mount(str(_WSLG_PULSE_SERVER), str(_WSLG_PULSE_SERVER), read_only=True))
        found = True

    if not found:
        logger.warning(
            "No audio socket found (PipeWire/PulseAudio), audio won't work inside sandbox"
        )


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

    # Forward terminal info
    if term := os.environ.get("TERM"):
        env["TERM"] = term
    if colorterm := os.environ.get("COLORTERM"):
        env["COLORTERM"] = colorterm

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

    # Display or clipboard support (forward display env vars)
    if config.display or config.clipboard:
        _add_display_environment(env)

    # D-Bus support
    if config.dbus:
        _add_dbus_environment(env)

    # Audio support
    if config.audio:
        _add_audio_environment(env)

    # User-defined env
    env.update(config.env)

    return env


def _add_display_environment(env: dict[str, str]) -> None:
    """Forward display-related environment variables for GUI/clipboard tools.

    Only applies on Linux where display servers are available.
    On WSL2, uses WSLg runtime dir if XDG_RUNTIME_DIR is not set.
    """
    if not is_linux():
        return

    # Wayland
    if wayland_display := os.environ.get("WAYLAND_DISPLAY"):
        env["WAYLAND_DISPLAY"] = wayland_display
    if xdg_runtime_dir := os.environ.get("XDG_RUNTIME_DIR"):
        env["XDG_RUNTIME_DIR"] = xdg_runtime_dir
    elif is_wsl() and _WSLG_RUNTIME_DIR.exists():
        env["XDG_RUNTIME_DIR"] = str(_WSLG_RUNTIME_DIR)

    # X11 (only set XAUTHORITY when DISPLAY is forwarded)
    if x_display := os.environ.get("DISPLAY"):
        env["DISPLAY"] = x_display
        if xauthority := os.environ.get("XAUTHORITY"):
            env["XAUTHORITY"] = xauthority
        else:
            default_xauth = Path.home() / ".Xauthority"
            if default_xauth.exists():
                env["XAUTHORITY"] = str(default_xauth)


def _add_dbus_environment(env: dict[str, str]) -> None:
    """Forward D-Bus environment variables."""
    if not is_linux() or is_wsl():
        return

    if xdg_runtime_dir := os.environ.get("XDG_RUNTIME_DIR"):
        env["XDG_RUNTIME_DIR"] = xdg_runtime_dir
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={xdg_runtime_dir}/bus"


def _add_audio_environment(env: dict[str, str]) -> None:
    """Forward audio-related environment variables."""
    if not is_linux():
        return

    if xdg_runtime_dir := os.environ.get("XDG_RUNTIME_DIR"):
        env["XDG_RUNTIME_DIR"] = xdg_runtime_dir

    # Set PULSE_SERVER when the PulseAudio socket exists (standalone or via pipewire-pulse)
    if xdg_runtime_dir:
        pulse_socket = Path(xdg_runtime_dir) / "pulse" / "native"
        if pulse_socket.exists():
            env["PULSE_SERVER"] = str(pulse_socket)
            return
    if is_wsl() and _WSLG_PULSE_SERVER.exists():
        env["PULSE_SERVER"] = str(_WSLG_PULSE_SERVER)


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
