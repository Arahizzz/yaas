"""Builds ContainerSpec from Config - handles all mount logic."""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from importlib import resources
from pathlib import Path

from .config import Config, resolve_effective_config
from .constants import (
    CLONE_WORKSPACE,
    HOME_VOLUME,
    MISE_CONFIG_PATH,
    NIX_VOLUME,
    RUNTIME_IMAGE,
)
from .logging import get_logger
from .platform import (
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

LXCFS_PROC_FILES = (
    "cpuinfo",
    "meminfo",
    "stat",
    "uptime",
    "diskstats",
    "swaps",
    "loadavg",
)


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
        # Security
        capabilities=config.security.capabilities,
        seccomp_profile=config.security.seccomp_profile,
    )


def build_container_spec(
    config: Config,
    project_dir: Path | None,
    command: list[str],
    *,
    tty: bool = True,
    stdin_open: bool = True,
) -> ContainerSpec:
    """Build complete container specification from config.

    Args:
        config: Container configuration
        project_dir: Project directory to mount, or None to skip project mount
        command: Command to run in container
        tty: Allocate a pseudo-TTY (requires stdin to be a TTY)
        stdin_open: Keep stdin open (needed for piped input)
    """
    config = resolve_effective_config(config)
    uid, gid = get_uid_gid()
    home = Path.home()
    sandbox_home = "/home"

    # Build mounts and collect supplementary groups
    mounts, groups = _build_mounts(config, project_dir, home, sandbox_home)

    # Build environment
    environment = _build_environment(config, project_dir, sandbox_home)
    if config.preamble:
        environment["YAAS_PREAMBLE"] = _build_preamble(config, project_dir, mounts)

    # Use real UID:GID. Runtimes pass as YAAS_HOST_UID/GID env vars for entrypoint.
    container_user = f"{uid}:{gid}"

    working_dir = str(project_dir) if project_dir else sandbox_home

    # Collect ports (global + tool-specific)
    ports = list(config.ports)
    if config.active_tool:
        tool = config.tools.get(config.active_tool)
        if tool:
            ports.extend(tool.ports)

    # Collect devices (global + tool-specific)
    devices = list(config.devices)
    if config.active_tool:
        tool = config.tools.get(config.active_tool)
        if tool:
            devices.extend(tool.devices)

    # Resolve security (start from config, may be overridden by podman mode)
    capabilities = list(config.security.capabilities) if config.security.capabilities else None
    seccomp_profile = config.security.seccomp_profile
    privileged = False

    # Podman DinD: use --privileged for nested containers.
    # Selective caps (SYS_ADMIN, MKNOD, etc.) are insufficient — nested container
    # runtimes need the full capability set, unconfined seccomp, and device access.
    podman_enabled = config.podman or config.podman_docker_socket
    if podman_enabled:
        privileged = True
        if "/dev/fuse" not in devices:
            devices.append("/dev/fuse")
        # Overlay-on-overlay is denied by the kernel — inner container storage
        # must live on a non-overlay filesystem. Named volume persists pulled images.
        mounts.append(Mount(source="yaas-podman-data", target="/var/lib/containers", type="volume"))

    # Podman: set env var for entrypoint to auto-install if needed
    if podman_enabled:
        environment["YAAS_PODMAN"] = "1"

    # Podman docker socket: set env var for entrypoint to start podman service
    if config.podman_docker_socket:
        environment["YAAS_PODMAN_DOCKER_SOCKET"] = "1"

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
        ports=ports or None,
        devices=devices or None,
        # Resource limits
        memory=config.resources.memory,
        memory_swap=config.resources.memory_swap,
        cpus=config.resources.cpus,
        pids_limit=config.resources.pids_limit,
        # Security
        privileged=privileged,
        capabilities=capabilities,
        seccomp_profile=seccomp_profile,
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
    cloned into the ephemeral volume. Delegates to build_container_spec with
    no project mount, then prepends the clone volume and adjusts the working dir.
    """
    working_dir = f"{CLONE_WORKSPACE}/{repo_name}"

    spec = build_container_spec(
        config, None, command, tty=tty, stdin_open=stdin_open,
    )

    # Prepend clone volume mount and override working dir / project env
    clone_mount = Mount(clone_volume, CLONE_WORKSPACE, type="volume")
    env = {**spec.environment, "PROJECT_PATH": working_dir}
    mounts = [clone_mount, *spec.mounts]
    return replace(spec, working_dir=working_dir, mounts=mounts, environment=env)


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
        mounts.append(Mount(str(resolved_wt_base), str(resolved_wt_base), read_only=read_only))
        return True

    # Normal session: mount worktree base dir if it exists (use resolved path)
    if wt_base.exists():
        mounts.append(Mount(str(resolved_wt_base), str(resolved_wt_base), read_only=read_only))

    return False


def _add_lxcfs_mounts(config: Config, mounts: list[Mount]) -> None:
    """Add lxcfs bind mounts for resource visibility inside the container.

    lxcfs virtualizes /proc files (cpuinfo, meminfo, etc.) so tools see
    real cgroup limits instead of host resources. Requires lxcfs running on host.
    """
    if not is_linux() or not config.lxcfs:
        return

    lxcfs_base = Path("/var/lib/lxcfs/proc")
    if not lxcfs_base.exists():
        logger.warning("lxcfs enabled but /var/lib/lxcfs/proc not found — is lxcfs installed?")
        return

    for proc_file in LXCFS_PROC_FILES:
        src = lxcfs_base / proc_file
        if src.exists():
            mounts.append(Mount(str(src), f"/proc/{proc_file}", read_only=True))


def _build_mounts(
    config: Config,
    project_dir: Path | None,
    home: Path,
    sandbox_home: str,
) -> tuple[list[Mount], list[int]]:
    """Assemble all mounts and supplementary groups."""
    mounts: list[Mount] = []
    groups: list[int] = []

    if project_dir is not None:
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
    fallback_dir = project_dir or Path.cwd()
    _add_optional_mounts(config, mounts, groups, home, sandbox_home, fallback_dir)

    # lxcfs mounts for resource visibility
    _add_lxcfs_mounts(config, mounts)

    # User-defined mounts
    for mount_spec in config.mounts:
        if mount := _parse_mount_spec(mount_spec, fallback_dir):
            mounts.append(mount)

    return mounts, groups


def _add_optional_mounts(
    config: Config,
    mounts: list[Mount],
    groups: list[int],
    home: Path,
    sandbox_home: str,
    project_dir: Path,
) -> None:
    """Add optional mounts based on config (git, SSH, clipboard, mise, tool mounts).

    Order matters: the home volume must be mounted first so that subsequent
    bind mounts for config files overlay on top of it.
    """
    # Home volume first — bind mounts below overlay specific paths within it
    _add_mise_support(mounts)

    if config.git_config:
        _add_git_config_mounts(mounts, home, sandbox_home)

    # Tool-specific mounts (only when active_tool is set)
    if config.active_tool:
        tool = config.tools.get(config.active_tool)
        if tool:
            for mount_spec in tool.mounts:
                if mount := _parse_mount_spec(mount_spec, project_dir, sandbox_home):
                    mounts.append(mount)

    if config.ssh_agent:
        _add_ssh_agent(mounts)

    if config.clipboard:
        _add_clipboard_support(mounts)


def _add_ssh_agent(mounts: list[Mount]) -> None:
    """Mount SSH agent socket with platform-aware detection."""
    # Use platform-aware socket detection (handles macOS launchd sockets)
    sock_path = get_ssh_agent_socket()
    if not sock_path:
        logger.warning("SSH agent socket not found, skipping SSH agent")
        return

    mounts.append(Mount(str(sock_path), "/ssh-agent"))

    # Mount known_hosts so SSH recognizes previously-verified hosts
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    if known_hosts.exists():
        mounts.append(Mount(str(known_hosts), "/etc/ssh/ssh_known_hosts", read_only=True))



def _add_clipboard_support(mounts: list[Mount]) -> None:
    """Mount display sockets for clipboard access (Wayland or X11).

    Only works on Linux where display server sockets can be mounted.
    macOS/Windows don't have accessible display sockets.
    """
    # Clipboard via display sockets only works on Linux
    if not is_linux():
        if is_macos():
            logger.warning("Clipboard not supported on macOS (no display server sockets available)")
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
    mounts.append(Mount(HOME_VOLUME, sandbox_home, type="volume"))
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


def _add_git_config_mounts(
    mounts: list[Mount],
    home: Path,
    sandbox_home: str,
) -> None:
    """Add git config file/directory mounts."""
    for config_path in (".gitconfig", ".config/git"):
        src = home / config_path
        if src.exists():
            dst = f"{sandbox_home}/{config_path}"
            mounts.append(Mount(str(src), dst))


def _build_preamble(
    config: Config, project_dir: Path | None, mounts: list[Mount],
) -> str:
    """Generate a sandbox preamble describing the container environment."""
    lines = [
        "You are running inside a YAAS (Yet Another Agent Sandbox) container.",
        "The purpose of this sandbox is to allow you to run commands, install tools,",
        "and make changes freely without affecting the host system.",
        "You have sudo access. Use it when needed without hesitation.",
        "However, apply caution when working with user's files in mounted directories —",
        "those changes affect the real host filesystem.",
        "Project: https://github.com/arahizzz/yaas",
        "",
        "Environment:",
    ]

    # Runtime
    if config.runtime:
        lines.append(f"- Runtime: {config.runtime}")

    # Network
    lines.append(f"- Network: {config.network_mode}")

    # Ports
    if config.ports:
        lines.append(f"- Published ports: {', '.join(config.ports)}")

    # Resource limits
    mem = config.resources.memory or "unlimited"
    cpu = str(config.resources.cpus) if config.resources.cpus else "unlimited"
    pids = str(config.resources.pids_limit) if config.resources.pids_limit else "unlimited"
    lines.append(f"- Memory limit: {mem}")
    lines.append(f"- CPU limit: {cpu}")
    lines.append(f"- PID limit: {pids}")

    # Project
    if project_dir is not None:
        ro = "read-only" if config.readonly_project else "read-write"
        lines.append(f"- Project: {project_dir} ({ro})")
    else:
        lines.append("- Project: none (no project directory mounted)")

    # Mounts
    bind_mounts = [m for m in mounts if m.type == "bind"]
    if bind_mounts:
        lines.append("")
        lines.append("Bind mounts (host filesystem — changes are reflected on host):")
        for m in bind_mounts:
            ro_label = " (read-only)" if m.read_only else ""
            lines.append(f"- {m.target}{ro_label}")

    volume_mounts = [m for m in mounts if m.type == "volume"]
    if volume_mounts:
        lines.append("")
        lines.append("Volumes (persistent across container restarts):")
        for m in volume_mounts:
            lines.append(f"- {m.target}")

    lines.append("")
    lines.append(
        "Files outside mounted directories are container-local"
        " and will be lost when the container stops."
    )

    lines.append("")
    lines.append("Installing tools:")
    lines.append("- Use `nix run nixpkgs#<package>` for quick one-off tool invocations")
    lines.append("  when a tool is not already installed (100k+ packages available).")
    lines.append("- Use `sudo apt-get install <package>` for system packages that cannot")
    lines.append("  be run with Nix. APT packages are ephemeral and lost on container stop.")
    lines.append("- Use `mise use <tool>` to manage shared persistent tools (Node.js, Python,")
    lines.append("  Go, etc.). Mise tools persist across restarts. Consult with the user")
    lines.append("  before adding tools to mise, as it affects the shared tool configuration.")
    lines.append("  Mise also supports Nix packages via the mise-nix plugin —")
    lines.append("  use `mise use nix:<package>` for Nix packages that should persist.")

    return "\n".join(lines)


def _build_environment(
    config: Config,
    project_dir: Path | None,
    sandbox_home: str,
) -> dict[str, str]:
    """Build environment variables."""
    env: dict[str, str] = {
        "HOME": sandbox_home,
        "YAAS": "1",
        # Make npm use XDG-compliant cache path
        "npm_config_cache": f"{sandbox_home}/.cache/npm",
        # Mise configuration
        "MISE_DATA_DIR": f"{sandbox_home}/.local/share/mise",
        "MISE_CACHE_DIR": f"{sandbox_home}/.cache/mise",
        # Auto-confirm trust prompts
        "MISE_YES": "1",
    }

    if project_dir is not None:
        env["PROJECT_PATH"] = str(project_dir)
        # Trust project mise configs automatically
        env["MISE_TRUSTED_CONFIG_PATHS"] = str(project_dir)

    # Forward terminal info
    if term := os.environ.get("TERM"):
        env["TERM"] = term
    if colorterm := os.environ.get("COLORTERM"):
        env["COLORTERM"] = colorterm

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

    # User-defined env (global + tool env, pre-merged by resolve_effective_config)
    _apply_env_dict(env, config.env)

    return env


def _apply_env_dict(env: dict[str, str], env_dict: dict[str, str | bool]) -> None:
    """Apply an env dict to the environment.

    - true values: forward from host (pass-through via os.environ)
    - string values: set directly
    """
    for key, value in env_dict.items():
        if value is True:
            host_val = os.environ.get(key)
            if host_val is not None:
                env[key] = host_val
        elif isinstance(value, str):
            env[key] = value


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


def _parse_mount_spec(spec: str, project_dir: Path, sandbox_home: str = "/home") -> Mount | None:
    """Parse mount spec with unified format.

    Formats:
    - "~/.claude"            → auto-dst: ~/.claude → /home/.claude
    - "~/.claude:ro"         → auto-dst + read-only
    - "~/a:/data"            → explicit src:dst
    - "~/a:/data:ro"         → explicit src:dst + read-only
    - "./rel:/container"     → relative to project_dir
    - "/abs:/container:ro"   → absolute + read-only

    Auto-dst: when no explicit destination is given and src starts with ~,
    the destination is computed as sandbox_home + path relative to home.
    Container destinations always start with /, so :ro as the second part
    is unambiguous (it can't be a destination).
    """
    parts = spec.split(":")
    read_only = False

    if len(parts) == 1:
        # "~/.claude" or "./data" — no dst, no opts
        src = parts[0]
        dst = None
    elif len(parts) == 2 and not parts[1].startswith("/"):
        # "~/.claude:ro" — second part is opts, not a dst
        src = parts[0]
        dst = None
        read_only = "ro" in parts[1]
    elif len(parts) == 2:
        # "~/a:/data" — explicit src:dst
        src = parts[0]
        dst = parts[1]
    elif len(parts) >= 3:
        # "~/a:/data:ro" — explicit src:dst:opts
        src = parts[0]
        dst = parts[1]
        read_only = "ro" in parts[2]
    else:
        src = parts[0]
        dst = None

    # Expand ~ and relative paths
    src_path = Path(src).expanduser()
    if not src_path.is_absolute():
        src_path = project_dir / src_path

    if not src_path.exists():
        logger.warning("Mount source does not exist, skipping: %s", src_path)
        return None

    # Auto-compute destination for ~ paths
    if dst is None:
        if src.startswith("~"):
            # ~/X → sandbox_home/X (strip leading ~/)
            rel = src.removeprefix("~").lstrip("/")
            dst = f"{sandbox_home}/{rel}" if rel else sandbox_home
        else:
            dst = str(src_path)

    return Mount(str(src_path), dst, read_only=read_only)
