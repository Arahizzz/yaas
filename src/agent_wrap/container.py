"""Builds ContainerSpec from Config - handles all mount logic."""

from __future__ import annotations

import os
from pathlib import Path

from rich.console import Console

from .config import Config
from .constants import API_KEYS, CONTAINER_SOCKETS
from .runtime import ContainerSpec, Mount

console = Console()


def build_container_spec(
    config: Config,
    project_dir: Path,
    command: list[str],
    interactive: bool = True,
) -> ContainerSpec:
    """Build complete container specification from config."""
    uid = os.getuid()
    gid = os.getgid()
    home = Path.home()
    sandbox_home = "/home"

    # Build mounts and collect supplementary groups
    mounts, groups = _build_mounts(config, project_dir, home, sandbox_home, uid)

    # Build environment
    environment = _build_environment(config, project_dir, sandbox_home)

    # Use real UID:GID. With --userns=keep-id, this maps correctly in rootless podman.
    container_user = f"{uid}:{gid}"

    return ContainerSpec(
        image=config.image,
        command=command,
        working_dir=str(project_dir),
        user=container_user,
        environment=environment,
        mounts=mounts,
        network_mode="none" if config.no_network else None,
        tty=interactive,
        stdin_open=interactive,
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
    uid: int,
) -> tuple[list[Mount], list[int]]:
    """Assemble all mounts and supplementary groups."""
    mounts: list[Mount] = []
    groups: list[int] = []

    # UID/GID passthrough - mount passwd/group so user is recognized
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
        _add_container_socket(mounts, groups, uid)

    # User-defined mounts
    for mount_spec in config.mounts:
        mounts.append(_parse_mount_spec(mount_spec, project_dir))

    return mounts, groups


def _add_ssh_agent(mounts: list[Mount]) -> None:
    """Mount SSH agent socket."""
    ssh_sock = os.environ.get("SSH_AUTH_SOCK")
    if not ssh_sock:
        console.print("[yellow]Warning: SSH_AUTH_SOCK not set, skipping SSH agent[/]")
        return

    if not Path(ssh_sock).exists():
        console.print(f"[yellow]Warning: SSH socket {ssh_sock} not found[/]")
        return

    mounts.append(Mount(ssh_sock, "/ssh-agent"))


def _add_container_socket(mounts: list[Mount], groups: list[int], uid: int) -> None:
    """Mount container runtime socket for docker-in-docker."""
    for sock_template in CONTAINER_SOCKETS:
        sock = sock_template.format(uid=uid)
        sock_path = Path(sock)
        if sock_path.exists():
            # Mount at same path so docker/podman CLI works unchanged
            mounts.append(Mount(sock, sock))
            # Add socket's group to supplementary groups for access
            sock_gid = sock_path.stat().st_gid
            if sock_gid not in groups:
                groups.append(sock_gid)
            return

    console.print(
        "[yellow]Warning: No container socket found, docker/podman won't work inside sandbox[/]"
    )


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
        "AGENT_WRAP": "1",
        # Make npm use XDG-compliant cache path
        "npm_config_cache": f"{sandbox_home}/.cache/npm",
    }

    # Forward TERM
    if term := os.environ.get("TERM"):
        env["TERM"] = term

    # Forward API keys
    for key in API_KEYS:
        if value := os.environ.get(key):
            env[key] = value

    # SSH agent
    if config.ssh_agent and os.environ.get("SSH_AUTH_SOCK"):
        env["SSH_AUTH_SOCK"] = "/ssh-agent"
        # Override git SSH signing to use ssh-keygen with agent (not 1Password's op-ssh-sign)
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "gpg.ssh.program"
        env["GIT_CONFIG_VALUE_0"] = "ssh-keygen"

    # User-defined env
    env.update(config.env)

    return env


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
