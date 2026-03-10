"""Typer CLI application with commands."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from .completions import NetworkMode, RuntimeChoice, complete_box, complete_worktree
from .config import (
    _CONTAINER_FIELDS,
    _SPECIAL_FIELDS,
    BoxSpec,
    Config,
    ResourceLimits,
    ToolConfig,
    load_config,
    load_tool_commands,
    resolve_effective_config,
)
from .constants import (
    BOX_CONTAINER_PREFIX,
    HOME_VOLUME,
    NIX_VOLUME,
    RUNTIME_IMAGE,
)
from .container import (
    build_box_spec,
    build_container_spec,
)
from .logging import get_logger, setup_logging
from .platform import PlatformError, check_platform_support
from .runtime import ContainerRuntime, ExecSpec, get_runtime
from .startup_ui import (
    is_interactive,
    print_startup_footer,
    print_startup_header,
    print_step,
    stdin_is_tty,
)
from .worktree import (
    WorktreeError,
    check_worktree_in_use,
    get_worktree_path,
    get_yaas_worktrees,
)
from .worktree import (
    add_worktree as wt_add,
)
from .worktree import (
    remove_worktree as wt_remove,
)
from .worktree import (
    repair_worktrees as wt_repair,
)

# Initialize logging
setup_logging()
logger = get_logger()

app = typer.Typer(
    name="yaas",
    help="Run AI coding agents in sandboxed containers",
    no_args_is_help=True,
)
box_app = typer.Typer(
    name="box",
    help="Manage persistent sandbox containers (boxes)",
    no_args_is_help=True,
)
app.add_typer(box_app, name="box")
worktree_app = typer.Typer(
    name="worktree",
    help="Manage git worktrees for parallel development",
    no_args_is_help=True,
)
app.add_typer(worktree_app, name="worktree")
cleanup_app = typer.Typer(
    name="cleanup",
    help="Clean up volumes and ephemeral resources",
    no_args_is_help=True,
)
app.add_typer(cleanup_app, name="cleanup")
console = Console()


@app.callback()
def main_callback() -> None:
    """Check platform support before running any command."""
    try:
        check_platform_support()
    except PlatformError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command(
    context_settings={
        "allow_extra_args": True,
        "allow_interspersed_args": False,
        "ignore_unknown_options": True,
    },
)
def run(
    ctx: typer.Context,
    worktree: str | None = typer.Option(
        None, "--worktree", "-w", help="Run in worktree", autocompletion=complete_worktree
    ),
    ssh_agent: bool = typer.Option(False, "--ssh-agent", help="Forward SSH agent"),
    git_config: bool = typer.Option(False, "--git-config", help="Mount git config"),
    podman: bool = typer.Option(False, "--podman", help="Enable rootless Podman inside container"),
    podman_docker_socket: bool = typer.Option(
        False, "--podman-docker-socket", help="Start Podman socket (Docker-compatible API)"
    ),
    clipboard: bool = typer.Option(
        False, "--clipboard", help="Enable clipboard access for image pasting"
    ),
    network: NetworkMode | None = typer.Option(None, "--network", help="Network mode"),
    memory: str | None = typer.Option(None, "--memory", "-m", help="Memory limit (e.g., 8g)"),
    cpus: float | None = typer.Option(None, "--cpus", help="CPU limit (e.g., 2.0)"),
    mount: list[str] | None = typer.Option(None, "--mount", "-v", help="Ad-hoc mount (mount spec)"),
    port: list[str] | None = typer.Option(
        None, "--port", "-p", help="Publish port (host:container)"
    ),
    device: list[str] | None = typer.Option(
        None, "--device", help="Pass through host device (e.g., /dev/fuse)"
    ),
    env: list[str] | None = typer.Option(None, "--env", "-e", help="Ad-hoc env (KEY=VALUE or KEY)"),
    no_project: bool = typer.Option(False, "--no-project", help="Don't mount project directory"),
    runtime: RuntimeChoice | None = typer.Option(None, "--runtime", help="Container runtime"),
) -> None:
    """Run a command in the sandbox."""
    if not ctx.args:
        raise typer.BadParameter("Missing command to run")

    # Validate mutual exclusion
    if no_project and worktree:
        raise typer.BadParameter("--no-project and --worktree are mutually exclusive")

    project_dir, worktree_name = _resolve_worktree(worktree)
    config = load_config(project_dir)

    # CLI flags override config
    _apply_cli_flags(
        config,
        config,
        ssh_agent=ssh_agent,
        git_config=git_config,
        podman=podman,
        podman_docker_socket=podman_docker_socket,
        clipboard=clipboard,
        network=network,
        memory=memory,
        cpus=cpus,
        no_project=no_project,
        runtime=runtime,
        mounts=mount,
        ports=port,
        devices=device,
        envs=env,
    )

    _run_container(config, project_dir, ctx.args, worktree_name)


def _create_tool_command(tool: str, tool_config: ToolConfig) -> None:
    """Create a tool-specific command (claude, aider, etc.)."""

    @app.command(
        name=tool,
        context_settings={
            "allow_extra_args": True,
            "allow_interspersed_args": False,
            "ignore_unknown_options": True,
        },
    )
    def tool_command(
        ctx: typer.Context,
        worktree: str | None = typer.Option(
            None, "--worktree", "-w", help="Run in worktree", autocompletion=complete_worktree
        ),
        ssh_agent: bool = typer.Option(False, "--ssh-agent", help="Forward SSH agent"),
        git_config: bool = typer.Option(False, "--git-config", help="Mount git config"),
        podman: bool = typer.Option(
            False, "--podman", help="Enable rootless Podman inside container"
        ),
        podman_docker_socket: bool = typer.Option(
            False, "--podman-docker-socket", help="Start Podman socket (Docker-compatible API)"
        ),
        clipboard: bool = typer.Option(
            False, "--clipboard", help="Enable clipboard access for image pasting"
        ),
        network: NetworkMode | None = typer.Option(None, "--network", help="Network mode"),
        no_yolo: bool = typer.Option(False, "--no-yolo", help="Disable auto-confirm mode"),
        memory: str | None = typer.Option(None, "--memory", "-m", help="Memory limit (e.g., 8g)"),
        cpus: float | None = typer.Option(None, "--cpus", help="CPU limit (e.g., 2.0)"),
        mount: list[str] | None = typer.Option(
            None, "--mount", "-v", help="Ad-hoc mount (mount spec)"
        ),
        port: list[str] | None = typer.Option(
            None, "--port", "-p", help="Publish port (host:container)"
        ),
        device: list[str] | None = typer.Option(
            None, "--device", help="Pass through host device (e.g., /dev/fuse)"
        ),
        env: list[str] | None = typer.Option(
            None, "--env", "-e", help="Ad-hoc env (KEY=VALUE or KEY)"
        ),
        no_project: bool = typer.Option(
            False, "--no-project", help="Don't mount project directory"
        ),
        runtime: RuntimeChoice | None = typer.Option(None, "--runtime", help="Container runtime"),
    ) -> None:
        """Run AI tool in sandbox with YOLO mode (auto-confirm)."""
        # Validate mutual exclusion
        if no_project and worktree:
            raise typer.BadParameter("--no-project and --worktree are mutually exclusive")

        project_dir, worktree_name = _resolve_worktree(worktree)
        config = load_config(project_dir)

        # Set active tool and resolve overrides before CLI flags
        config.active_tool = tool
        config = resolve_effective_config(config)

        # CLI flags override tool config (highest priority)
        _apply_cli_flags(
            config,
            config,
            ssh_agent=ssh_agent,
            git_config=git_config,
            podman=podman,
            podman_docker_socket=podman_docker_socket,
            clipboard=clipboard,
            network=network,
            memory=memory,
            cpus=cpus,
            no_project=no_project,
            runtime=runtime,
            mounts=mount,
            ports=port,
            devices=device,
            envs=env,
        )

        # Build command with YOLO flags (unless --no-yolo)
        tc = config.tools.get(tool)
        command = list(tc.command) if tc and tc.command else [tool]
        if not no_yolo and tc:
            command.extend(tc.yolo_flags)
        command.extend(ctx.args)

        _run_container(config, project_dir, command, worktree_name)

    # Build descriptive help text from tool config
    cmd_name = " ".join(tool_config.command) if tool_config.command else tool
    parts = [f"Run `{cmd_name}` in sandbox."]
    if tool_config.yolo_flags:
        parts.append(f"YOLO: {' '.join(tool_config.yolo_flags)}")
    if tool_config.mounts:
        parts.append(f"Mounts: {', '.join(tool_config.mounts)}")
    # Show container setting overrides in help (generic via ContainerSettings fields)
    overrides = []
    for field_name in sorted(_CONTAINER_FIELDS - _SPECIAL_FIELDS):
        value = getattr(tool_config, field_name)
        if value is not None:
            overrides.append(f"{field_name}={value}")
    if overrides:
        parts.append(f"Overrides: {', '.join(overrides)}")
    tool_command.__doc__ = " ".join(parts)


# Reserved command names that tools cannot override
_RESERVED_COMMANDS = {
    "run",
    "config",
    "config-cmd",
    "cleanup",
    "pull-image",
    "worktree",
    "box",
}

# Register tool commands from config
_tools = load_tool_commands()
for _tool_name, _tool_config in _tools.items():
    if _tool_name in _RESERVED_COMMANDS:
        logger.warning("Tool '%s' conflicts with built-in command, skipping", _tool_name)
        continue
    _create_tool_command(_tool_name, _tool_config)


# --- Box helpers ---


def _box_container_name(name: str) -> str:
    """Convert box name to container name."""
    return f"{BOX_CONTAINER_PREFIX}{name}"


def _get_box_label(info: dict[str, Any], key: str) -> str | None:
    """Extract a label value from container inspect data."""
    labels = info.get("Config", {}).get("Labels", {})
    val: str | None = labels.get(key)
    return val


# --- Box subcommands ---


@box_app.command(name="create")
def box_create(
    name: str = typer.Argument(..., help="Name for the box"),
    spec: str | None = typer.Argument(
        None, help="Box spec from config (e.g., shell)", autocompletion=complete_box
    ),
    ssh_agent: bool = typer.Option(False, "--ssh-agent", help="Forward SSH agent"),
    git_config: bool = typer.Option(False, "--git-config", help="Mount git config"),
    podman: bool = typer.Option(False, "--podman", help="Enable rootless Podman inside container"),
    podman_docker_socket: bool = typer.Option(
        False, "--podman-docker-socket", help="Start Podman socket (Docker-compatible API)"
    ),
    clipboard: bool = typer.Option(
        False, "--clipboard", help="Enable clipboard access for image pasting"
    ),
    network: NetworkMode | None = typer.Option(None, "--network", help="Network mode"),
    memory: str | None = typer.Option(None, "--memory", "-m", help="Memory limit (e.g., 8g)"),
    cpus: float | None = typer.Option(None, "--cpus", help="CPU limit (e.g., 2.0)"),
    mount: list[str] | None = typer.Option(None, "--mount", "-v", help="Ad-hoc mount (mount spec)"),
    port: list[str] | None = typer.Option(
        None, "--port", "-p", help="Publish port (host:container)"
    ),
    device: list[str] | None = typer.Option(
        None, "--device", help="Pass through host device (e.g., /dev/fuse)"
    ),
    env: list[str] | None = typer.Option(None, "--env", "-e", help="Ad-hoc env (KEY=VALUE or KEY)"),
    base: str | None = typer.Option(None, "--base", help="Config base: default, minimal, none"),
    runtime_opt: RuntimeChoice | None = typer.Option(None, "--runtime", help="Container runtime"),
) -> None:
    """Create a persistent box container."""
    project_dir = Path.cwd()
    config = load_config(project_dir)

    # Use spec name, or create ad-hoc spec
    effective_spec = spec or "__adhoc__"
    if effective_spec not in config.boxes:
        config.boxes[effective_spec] = BoxSpec()

    # Apply base and CLI flag overrides to box spec
    box_spec = config.boxes[effective_spec]
    if base is not None:
        box_spec.base = base
    _apply_cli_flags(
        box_spec,
        config,
        ssh_agent=ssh_agent,
        git_config=git_config,
        podman=podman,
        podman_docker_socket=podman_docker_socket,
        clipboard=clipboard,
        network=network,
        memory=memory,
        cpus=cpus,
        runtime=runtime_opt,
        mounts=mount,
        ports=port,
        devices=device,
        envs=env,
    )

    runtime = get_runtime(config.runtime)
    config.runtime = runtime.name
    runtime.adjust_config(config)

    container_name = _box_container_name(name)

    # Pull image if enabled
    if config.auto_pull_image:
        print_step("Pulling image")
        _pull_image(runtime)

    # Build and create container
    container_spec = build_box_spec(config, effective_spec, container_name)

    print_step(f"Creating box '{name}'")
    if not runtime.create_container(container_spec):
        console.print(f"[red]Failed to create box '{name}'[/]")
        raise typer.Exit(1)

    # Start the container
    if not runtime.start_container(container_name):
        console.print(f"[red]Failed to start box '{name}'[/]")
        raise typer.Exit(1)

    console.print(f"[green]Box '{name}' created and running.[/]")
    console.print(f"[dim]Enter with: yaas box enter {name}[/]")


@box_app.command(name="enter")
def box_enter(
    name: str = typer.Argument(..., help="Box name"),
) -> None:
    """Enter a running box with an interactive shell."""
    project_dir = Path.cwd()
    config = load_config(project_dir)
    runtime = get_runtime(config.runtime)
    container_name = _box_container_name(name)

    # Check container exists
    info = runtime.inspect_container(container_name)
    if info is None:
        console.print(f"[red]Box '{name}' not found[/]")
        raise typer.Exit(1)

    # Determine shell from spec
    shell_cmd = ["bash"]
    spec_name = _get_box_label(info, "yaas.box.spec")
    if spec_name:
        box_spec = config.boxes.get(spec_name)
        if box_spec and box_spec.shell:
            shell_cmd = list(box_spec.shell)

    exec_spec = ExecSpec(
        container_name=container_name,
        command=shell_cmd,
        tty=stdin_is_tty(),
        stdin_open=True,
    )
    exit_code = runtime.exec_container(exec_spec)
    raise typer.Exit(exit_code)


@box_app.command(
    name="exec",
    context_settings={
        "allow_extra_args": True,
        "allow_interspersed_args": False,
        "ignore_unknown_options": True,
    },
)
def box_exec(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Box name"),
) -> None:
    """Execute a command in a running box."""
    if not ctx.args:
        raise typer.BadParameter("Missing command to run")

    config = load_config(Path.cwd())
    runtime = get_runtime(config.runtime)
    container_name = _box_container_name(name)

    info = runtime.inspect_container(container_name)
    if info is None:
        console.print(f"[red]Box '{name}' not found[/]")
        raise typer.Exit(1)

    exec_spec = ExecSpec(
        container_name=container_name,
        command=ctx.args,
        tty=stdin_is_tty(),
        stdin_open=True,
    )
    exit_code = runtime.exec_container(exec_spec)
    raise typer.Exit(exit_code)


@box_app.command(name="stop")
def box_stop(
    name: str = typer.Argument(..., help="Box name"),
) -> None:
    """Stop a running box."""
    runtime = get_runtime()
    container_name = _box_container_name(name)

    if runtime.stop_container(container_name):
        console.print(f"[green]Box '{name}' stopped.[/]")
    else:
        console.print(f"[red]Failed to stop box '{name}'[/]")
        raise typer.Exit(1)


@box_app.command(name="start")
def box_start(
    name: str = typer.Argument(..., help="Box name"),
) -> None:
    """Start a stopped box."""
    runtime = get_runtime()
    container_name = _box_container_name(name)

    if runtime.start_container(container_name):
        console.print(f"[green]Box '{name}' started.[/]")
    else:
        console.print(f"[red]Failed to start box '{name}'[/]")
        raise typer.Exit(1)


@box_app.command(name="remove")
def box_remove(
    name: str = typer.Argument(..., help="Box name"),
    force: bool = typer.Option(False, "--force", "-f", help="Force removal (stop if running)"),
) -> None:
    """Remove a box container."""
    runtime = get_runtime()
    container_name = _box_container_name(name)

    if runtime.remove_container(container_name, force=force):
        console.print(f"[green]Box '{name}' removed.[/]")
    else:
        console.print(f"[red]Failed to remove box '{name}'[/]")
        raise typer.Exit(1)


@box_app.command(name="list")
def box_list() -> None:
    """List all boxes."""
    runtime = get_runtime()
    containers = runtime.list_containers(BOX_CONTAINER_PREFIX)

    if not containers:
        console.print("[dim]No boxes found.[/]")
        return

    table = Table(title="Boxes")
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Spec", style="dim")
    table.add_column("Image", style="dim")

    for c in containers:
        # Podman and Docker have different JSON formats
        full_name = c.get("Names", c.get("Name", ""))
        if isinstance(full_name, list):
            full_name = full_name[0] if full_name else ""
        # Strip prefix to get box name
        box_name = full_name.removeprefix(BOX_CONTAINER_PREFIX)

        status = c.get("State", c.get("Status", "unknown"))
        image = c.get("Image", "")
        spec_label = c.get("Labels", {}).get("yaas.box.spec", "")

        table.add_row(box_name, status, spec_label, image)

    console.print(table)


@box_app.command(name="info")
def box_info(
    name: str = typer.Argument(..., help="Box name"),
) -> None:
    """Show detailed info about a box."""
    runtime = get_runtime()
    container_name = _box_container_name(name)

    info = runtime.inspect_container(container_name)
    if info is None:
        console.print(f"[red]Box '{name}' not found[/]")
        raise typer.Exit(1)

    state = info.get("State", {})
    config_section = info.get("Config", {})

    console.print(f"[bold]Name:[/] {name}")
    console.print(f"[bold]Container:[/] {container_name}")
    console.print(f"[bold]Status:[/] {state.get('Status', 'unknown')}")
    console.print(f"[bold]Image:[/] {config_section.get('Image', 'unknown')}")

    spec_name = _get_box_label(info, "yaas.box.spec")
    if spec_name:
        console.print(f"[bold]Spec:[/] {spec_name}")

    # Show labels
    labels = config_section.get("Labels", {})
    if labels:
        console.print("[bold]Labels:[/]")
        for k, v in sorted(labels.items()):
            console.print(f"  {k}: {v}")

    # Show mounts
    mounts = info.get("Mounts", [])
    if mounts:
        console.print("[bold]Mounts:[/]")
        for m in mounts:
            src = m.get("Source", m.get("Name", ""))
            dst = m.get("Destination", "")
            mount_type = m.get("Type", "")
            console.print(f"  {src} -> {dst} ({mount_type})")


@app.command()
def config_cmd() -> None:
    """Show current configuration."""
    project_dir = Path.cwd()
    cfg = load_config(project_dir)

    console.print(f"[bold]runtime:[/] {cfg.runtime or 'auto'}")
    console.print(f"[bold]ssh_agent:[/] {cfg.ssh_agent}")
    console.print(f"[bold]git_config:[/] {cfg.git_config}")
    console.print(f"[bold]podman:[/] {cfg.podman}")
    console.print(f"[bold]podman_docker_socket:[/] {cfg.podman_docker_socket}")
    console.print(f"[bold]clipboard:[/] {cfg.clipboard}")
    console.print(f"[bold]network_mode:[/] {cfg.network_mode}")
    console.print(f"[bold]readonly_project:[/] {cfg.readonly_project}")
    console.print("\n[bold]Auto-update:[/]")
    console.print(f"  auto_pull_image: {cfg.auto_pull_image}")
    console.print(f"  auto_upgrade_tools: {cfg.auto_upgrade_tools}")
    console.print("\n[bold]Resource limits:[/]")
    console.print(f"  memory: {cfg.resources.memory}")
    console.print(f"  cpus: {cfg.resources.cpus or 'unlimited'}")
    console.print(f"  pids_limit: {cfg.resources.pids_limit}")
    if cfg.tools:
        console.print("\n[bold]Tools:[/]")
        for name, tc in sorted(cfg.tools.items()):
            console.print(f"  [bold]{name}:[/]")
            if tc.command:
                console.print(f"    command: {tc.command}")
            if tc.yolo_flags:
                console.print(f"    yolo_flags: {tc.yolo_flags}")
            if tc.mounts:
                console.print(f"    mounts: {tc.mounts}")
            if tc.env:
                console.print(f"    env: {tc.env}")
            # Container setting overrides (generic via ContainerSettings fields)
            for field_name in sorted(_CONTAINER_FIELDS - _SPECIAL_FIELDS):
                value = getattr(tc, field_name)
                if value is not None:
                    console.print(f"    {field_name}: {value}")
            if tc.resources is not None:
                console.print(f"    resources: {tc.resources}")
    if cfg.mounts:
        console.print(f"\n[bold]Global mounts:[/] {cfg.mounts}")
    if cfg.env:
        console.print(f"[bold]Global env:[/] {cfg.env}")


# Add alias for config command
app.command(name="config")(config_cmd)


@cleanup_app.command(name="volumes")
def cleanup_volumes(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Reset yaas volumes (removes home directory, tools, cache, and Nix store)."""
    volumes = [HOME_VOLUME, NIX_VOLUME]

    if not force:
        console.print(
            "[yellow]This will delete the entire persistent home directory"
            " (shell history, dotfiles, mise tools, cache, and tool configs)"
            " and the Nix store.[/]"
        )
        console.print(f"Volumes: {', '.join(volumes)}")
        confirm = typer.confirm("Continue?")
        if not confirm:
            raise typer.Abort()

    runtime = get_runtime()
    for volume in volumes:
        result = subprocess.run(
            [*runtime.command_prefix, "volume", "rm", "-f", volume],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            console.print(f"[green]Removed volume: {volume}[/]")
        elif "no such volume" in result.stderr.lower():
            console.print(f"[dim]Volume not found: {volume}[/]")
        else:
            console.print(f"[red]Failed to remove {volume}: {result.stderr}[/]")

    console.print("[green]Reset complete. Tools will be reinstalled on next run.[/]")


@app.command(name="pull-image")
def pull_image() -> None:
    """Pull the latest container image."""
    runtime = get_runtime()
    if _pull_image(runtime):
        console.print("[green]Image updated successfully.[/]")
    else:
        console.print("[red]Failed to pull image.[/]")
        raise typer.Exit(1)


def _pull_image(runtime: ContainerRuntime) -> bool:
    """Pull container image. Returns True on success."""
    result = subprocess.run([*runtime.command_prefix, "pull", RUNTIME_IMAGE])
    return result.returncode == 0


def _upgrade_tools(config: Config, project_dir: Path, runtime: ContainerRuntime) -> bool:
    """Run mise upgrade in container. Returns True on success."""
    cmd = ["mise", "upgrade", "--yes"]
    spec = build_container_spec(config, project_dir, cmd, tty=is_interactive(), stdin_open=False)
    return runtime.run(spec) == 0


def _apply_cli_flags(
    target: Config | BoxSpec,
    config: Config,
    *,
    ssh_agent: bool = False,
    git_config: bool = False,
    podman: bool = False,
    podman_docker_socket: bool = False,
    clipboard: bool = False,
    network: NetworkMode | None = None,
    memory: str | None = None,
    cpus: float | None = None,
    no_project: bool = False,
    runtime: RuntimeChoice | None = None,
    mounts: list[str] | None = None,
    ports: list[str] | None = None,
    devices: list[str] | None = None,
    envs: list[str] | None = None,
) -> None:
    """Apply CLI flag overrides to a target (Config or BoxSpec).

    Boolean/scalar overrides go to `target`. List/env overrides always go to `config`
    (since they're merged at the config level in container.py).
    """
    if ssh_agent:
        target.ssh_agent = True
    if git_config:
        target.git_config = True
    if podman:
        target.podman = True
    if podman_docker_socket:
        target.podman_docker_socket = True
    if clipboard:
        target.clipboard = True
    if network is not None:
        target.network_mode = network.value
    if memory:
        if target.resources is None:
            target.resources = ResourceLimits()
        target.resources.memory = memory
    if cpus:
        if target.resources is None:
            target.resources = ResourceLimits()
        target.resources.cpus = cpus
    if no_project:
        target.mount_project = False
    if runtime:
        target.runtime = runtime.value

    # List/env overrides always go to config
    if mounts:
        config.mounts.extend(mounts)
    if ports:
        config.ports.extend(ports)
    if devices:
        config.devices.extend(devices)
    if envs:
        for entry in envs:
            if "=" in entry:
                key, value = entry.split("=", 1)
                config.env[key] = value
            else:
                config.env[entry] = True


def _run_container(
    config: Config,
    project_dir: Path,
    command: list[str],
    worktree_name: str | None = None,
) -> None:
    """Build spec and run container."""
    runtime = get_runtime(config.runtime)
    config.runtime = runtime.name
    runtime.adjust_config(config)

    # Show startup header
    print_startup_header()

    # Pull image if enabled
    if config.auto_pull_image:
        print_step("Pulling image")
        _pull_image(runtime)

    # Normal mode
    if config.auto_upgrade_tools:
        print_step("Upgrading tools")
        _upgrade_tools(config, project_dir, runtime)

    # Build container spec - TTY only if stdin is a terminal, but always allow stdin
    effective_project_dir = project_dir if config.mount_project else None
    spec = build_container_spec(config, effective_project_dir, command, tty=stdin_is_tty())

    # Check for concurrent usage warning
    if worktree_name and check_worktree_in_use(project_dir, runtime.command_prefix):
        logger.warning(f"Worktree '{worktree_name}' may already be in use by another container")

    print_step("Launching sandbox")
    print_startup_footer()

    # Run the interactive container
    exit_code = runtime.run(spec)
    raise typer.Exit(exit_code)


def _resolve_worktree(worktree_name: str | None) -> tuple[Path, str | None]:
    """Resolve worktree name to project directory.

    Returns (project_dir, worktree_name) tuple.
    If worktree_name is None, returns (cwd, None).
    """
    if worktree_name is None:
        return Path.cwd(), None

    try:
        worktree_path = get_worktree_path(worktree_name)
        if worktree_path is None:
            console.print(f"[red]Worktree '{worktree_name}' not found[/]")
            console.print("[dim]Use 'yaas worktree list' to see available worktrees[/]")
            raise typer.Exit(1)
        return worktree_path, worktree_name
    except WorktreeError as e:
        console.print(f"[red]Error: {e}[/]")
        raise typer.Exit(1)


# Worktree subcommands
@worktree_app.command(name="add")
def worktree_add(
    name: str = typer.Argument(..., help="Name for the new worktree"),
    branch: str | None = typer.Option(None, "--branch", "-b", help="Create new branch"),
) -> None:
    """Create a new worktree for parallel development."""
    try:
        worktree_path = wt_add(name, branch)
        console.print(f"[green]Created worktree '{name}' at {worktree_path}[/]")
        if branch:
            console.print(f"[dim]On new branch: {branch}[/]")
    except WorktreeError as e:
        console.print(f"[red]Error: {e}[/]")
        raise typer.Exit(1)


@worktree_app.command(name="list")
def worktree_list() -> None:
    """List worktrees for current project."""
    try:
        worktrees = get_yaas_worktrees()
        if not worktrees:
            console.print("[dim]No YAAS worktrees found for this project[/]")
            console.print("[dim]Use 'yaas worktree add NAME' to create one[/]")
            return

        console.print("[bold]YAAS Worktrees:[/]")
        for wt in worktrees:
            name = wt.get("name", "unknown")
            branch = wt.get("branch", "").replace("refs/heads/", "")
            detached = wt.get("detached") == "true"

            if detached:
                branch_info = f"[dim](detached at {wt.get('head', '')[:7]})[/]"
            elif branch:
                branch_info = f"[cyan]{branch}[/]"
            else:
                branch_info = "[dim](no branch)[/]"

            console.print(f"  {name}: {branch_info}")
    except WorktreeError as e:
        console.print(f"[red]Error: {e}[/]")
        raise typer.Exit(1)


@worktree_app.command(name="path")
def worktree_path(
    name: str = typer.Argument(..., help="Name of the worktree", autocompletion=complete_worktree),
) -> None:
    """Print the filesystem path of a worktree."""
    try:
        path = get_worktree_path(name)
        if path is None:
            console.print(f"[red]Worktree '{name}' not found[/]", highlight=False)
            console.print("[dim]Use 'yaas worktree list' to see available worktrees[/]")
            raise typer.Exit(1)
        print(path)
    except WorktreeError as e:
        console.print(f"[red]Error: {e}[/]")
        raise typer.Exit(1)


@worktree_app.command(name="remove")
def worktree_remove(
    name: str = typer.Argument(
        ..., help="Name of the worktree to remove", autocompletion=complete_worktree
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Force removal even with changes"),
) -> None:
    """Remove a worktree."""
    try:
        wt_remove(name, force)
        console.print(f"[green]Removed worktree '{name}'[/]")
    except WorktreeError as e:
        console.print(f"[red]Error: {e}[/]")
        raise typer.Exit(1)


@worktree_app.command(name="repair")
def worktree_repair() -> None:
    """Fix worktree paths after moving project directory."""
    try:
        messages = wt_repair()
        if messages:
            for msg in messages:
                console.print(f"[green]{msg}[/]")
        else:
            console.print("[dim]No repairs needed[/]")
    except WorktreeError as e:
        console.print(f"[red]Error: {e}[/]")
        raise typer.Exit(1)


def main() -> None:
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
