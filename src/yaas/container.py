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
    NIX_VOLUME,
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
from .worktree import (
    WorktreeError,
    get_main_repo_root,
    get_worktree_base_dir,
)

logger = get_logger()


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
        network_mode=config.network_mode,
        tty=tty,
        stdin_open=stdin_open,
        groups=groups or None,
        pid_mode=config.pid_mode,
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

    # UID/GID passthrough on Linux
    if is_linux():
        mounts.append(Mount("/etc/passwd", "/etc/passwd", read_only=True))
        mounts.append(Mount("/etc/group", "/etc/group", read_only=True))

    # Mount clone volume at /workspace
    mounts.append(Mount(clone_volume, CLONE_WORKSPACE, type="volume"))

    # Add standard mounts (config, SSH, clipboard, mise, etc.)
    _add_optional_mounts(config, mounts, groups, home, sandbox_home)

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
        memory=config.resources.memory,
        memory_swap=config.resources.memory_swap,
        cpus=config.resources.cpus,
        pids_limit=config.resources.pids_limit,
    )


def _add_worktree_mounts(
    mounts: list[Mount],
    project_dir: Path,
    read_only: bool,
) -> bool:
    """Add worktree-related mounts and return whether to skip the project_dir mount.

    For worktree sessions (project_dir is inside the worktree base dir):
    - Mount the main repo's .git directory read-write (needed for shared objects, refs,
      worktree state, and lock files). The working tree is not mounted at all.
    - Mount the worktree base dir with the caller's read_only setting
    - Signal to skip the normal project_dir mount (already covered by wt_base)

    For normal sessions:
    - Mount the worktree base dir with the caller's read_only setting if it exists
      (prevents git marking worktrees as prunable inside the container)
    - Signal to keep the normal project_dir mount

    Returns True if the project_dir mount should be skipped.
    """
    try:
        main_repo = get_main_repo_root(project_dir)
        wt_base = get_worktree_base_dir(project_dir, main_repo=main_repo)
    except WorktreeError:
        return False

    # Resolve symlinks for reliable containment check
    resolved_project = project_dir.resolve()
    resolved_wt_base = wt_base.resolve()

    try:
        resolved_project.relative_to(resolved_wt_base)
        is_worktree_session = True
    except ValueError:
        is_worktree_session = False

    if is_worktree_session:
        # Mount main repo's .git dir read-write for shared objects/refs/worktree state
        git_dir = main_repo / ".git"
        mounts.append(Mount(str(git_dir), str(git_dir)))
        # Mount worktree base dir using resolved path (covers this worktree + siblings)
        mounts.append(
            Mount(str(resolved_wt_base), str(resolved_wt_base), read_only=read_only)
        )
        return True

    # Normal session: mount worktree base dir if it exists (use resolved path)
    if wt_base.exists():
        mounts.append(
            Mount(str(resolved_wt_base), str(resolved_wt_base), read_only=read_only)
        )

    return False


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

    # Add worktree mounts (may signal to skip the project_dir mount)
    skip_project_mount = _add_worktree_mounts(mounts, project_dir, config.readonly_project)

    # Project directory at real path (critical for docker-in-docker!)
    if not skip_project_mount:
        mounts.append(
            Mount(
                str(project_dir),
                str(project_dir),
                read_only=config.readonly_project,
            )
        )

    # Add optional mounts
    _add_optional_mounts(config, mounts, groups, home, sandbox_home)

    # User-defined mounts
    for mount_spec in config.mounts:
        mounts.append(_parse_mount_spec(mount_spec, project_dir))

    return mounts, groups


def _add_optional_mounts(
    config: Config,
    mounts: list[Mount],
    groups: list[int],
    home: Path,
    sandbox_home: str,
) -> None:
    """Add optional mounts based on config (git, AI, SSH, clipboard, mise)."""
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

    if config.clipboard:
        _add_clipboard_support(mounts)

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
    mounts.append(Mount(NIX_VOLUME, "/nix", type="volume"))

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
