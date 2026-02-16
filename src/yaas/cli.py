"""Typer CLI application with commands."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import typer
from rich.console import Console

from .config import Config, load_config
from .constants import (
    CLONE_VOLUME_PREFIX,
    HOME_VOLUME,
    NIX_VOLUME,
    RUNTIME_IMAGE,
    TOOL_SHORTCUTS,
    TOOL_YOLO_FLAGS,
)
from .container import (
    build_clone_spec,
    build_clone_work_spec,
    build_container_spec,
    extract_repo_name,
)
from .logging import get_logger, setup_logging
from .platform import PlatformError, check_platform_support
from .runtime import ContainerRuntime, get_runtime
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
worktree_app = typer.Typer(
    name="worktree",
    help="Manage git worktrees for parallel development",
    no_args_is_help=True,
)
app.add_typer(worktree_app, name="worktree")
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
    worktree: str | None = typer.Option(None, "--worktree", "-w", help="Run in worktree"),
    clone: str | None = typer.Option(
        None, "--clone", help="Clone git repo into ephemeral volume"
    ),
    ref: str | None = typer.Option(
        None, "--ref", "-r", help="Git ref (tag/branch) to checkout when cloning"
    ),
    ssh_agent: bool = typer.Option(False, "--ssh-agent", help="Forward SSH agent"),
    git_config: bool = typer.Option(False, "--git-config", help="Mount git config"),
    ai_config: bool = typer.Option(False, "--ai-config", help="Mount AI tool configs"),
    container_socket: bool = typer.Option(
        False, "--container-socket", help="Mount docker/podman socket"
    ),
    clipboard: bool = typer.Option(
        False, "--clipboard", help="Enable clipboard access for image pasting"
    ),
    network: str | None = typer.Option(
        None, "--network", help="Network mode: host, bridge, none"
    ),
    memory: str | None = typer.Option(None, "--memory", "-m", help="Memory limit (e.g., 8g)"),
    cpus: float | None = typer.Option(None, "--cpus", help="CPU limit (e.g., 2.0)"),
) -> None:
    """Run a command in the sandbox."""
    if not ctx.args:
        raise typer.BadParameter("Missing command to run")

    # Validate mutual exclusion
    if clone and worktree:
        raise typer.BadParameter("--clone and --worktree are mutually exclusive")

    project_dir, worktree_name = _resolve_worktree(worktree)
    config = load_config(project_dir)

    # CLI flags override config
    if ssh_agent:
        config.ssh_agent = True
    if git_config:
        config.git_config = True
    if ai_config:
        config.ai_config = True
    if container_socket:
        config.container_socket = True
    if clipboard:
        config.clipboard = True
    if network:
        config.network_mode = network
    if memory:
        config.resources.memory = memory
    if cpus:
        config.resources.cpus = cpus

    _run_container(config, project_dir, ctx.args, worktree_name, clone_url=clone, clone_ref=ref)


@app.command()
def shell(
    worktree: str | None = typer.Option(None, "--worktree", "-w", help="Run in worktree"),
    clone: str | None = typer.Option(
        None, "--clone", help="Clone git repo into ephemeral volume"
    ),
    ref: str | None = typer.Option(
        None, "--ref", "-r", help="Git ref (tag/branch) to checkout when cloning"
    ),
    ssh_agent: bool = typer.Option(False, "--ssh-agent", help="Forward SSH agent"),
    git_config: bool = typer.Option(False, "--git-config", help="Mount git config"),
    ai_config: bool = typer.Option(False, "--ai-config", help="Mount AI tool configs"),
    container_socket: bool = typer.Option(
        False, "--container-socket", help="Mount docker/podman socket"
    ),
    clipboard: bool = typer.Option(
        False, "--clipboard", help="Enable clipboard access for image pasting"
    ),
    network: str | None = typer.Option(
        None, "--network", help="Network mode: host, bridge, none"
    ),
    memory: str | None = typer.Option(None, "--memory", "-m", help="Memory limit (e.g., 8g)"),
    cpus: float | None = typer.Option(None, "--cpus", help="CPU limit (e.g., 2.0)"),
) -> None:
    """Start interactive shell in sandbox."""
    # Validate mutual exclusion
    if clone and worktree:
        raise typer.BadParameter("--clone and --worktree are mutually exclusive")

    project_dir, worktree_name = _resolve_worktree(worktree)
    config = load_config(project_dir)

    if ssh_agent:
        config.ssh_agent = True
    if git_config:
        config.git_config = True
    if ai_config:
        config.ai_config = True
    if container_socket:
        config.container_socket = True
    if clipboard:
        config.clipboard = True
    if network:
        config.network_mode = network
    if memory:
        config.resources.memory = memory
    if cpus:
        config.resources.cpus = cpus

    _run_container(config, project_dir, ["bash"], worktree_name, clone_url=clone, clone_ref=ref)


def _create_tool_command(tool: str) -> None:
    """Create a tool-specific command (claude, codex, etc.)."""

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
        worktree: str | None = typer.Option(None, "--worktree", "-w", help="Run in worktree"),
        clone: str | None = typer.Option(
            None, "--clone", help="Clone git repo into ephemeral volume"
        ),
        ref: str | None = typer.Option(
            None, "--ref", "-r", help="Git ref (tag/branch) to checkout when cloning"
        ),
        ssh_agent: bool = typer.Option(False, "--ssh-agent", help="Forward SSH agent"),
        git_config: bool = typer.Option(False, "--git-config", help="Mount git config"),
        ai_config: bool = typer.Option(False, "--ai-config", help="Mount AI tool configs"),
        container_socket: bool = typer.Option(
            False, "--container-socket", help="Mount docker/podman socket"
        ),
        clipboard: bool = typer.Option(
            False, "--clipboard", help="Enable clipboard access for image pasting"
        ),
        network: str | None = typer.Option(
            None, "--network", help="Network mode: host, bridge, none"
        ),
        no_yolo: bool = typer.Option(False, "--no-yolo", help="Disable auto-confirm mode"),
        memory: str | None = typer.Option(
            None, "--memory", "-m", help="Memory limit (e.g., 8g)"
        ),
        cpus: float | None = typer.Option(None, "--cpus", help="CPU limit (e.g., 2.0)"),
    ) -> None:
        """Run AI tool in sandbox with YOLO mode (auto-confirm)."""
        # Validate mutual exclusion
        if clone and worktree:
            raise typer.BadParameter("--clone and --worktree are mutually exclusive")

        project_dir, worktree_name = _resolve_worktree(worktree)
        config = load_config(project_dir)

        if ssh_agent:
            config.ssh_agent = True
        if git_config:
            config.git_config = True
        if ai_config:
            config.ai_config = True
        if container_socket:
            config.container_socket = True
        if clipboard:
            config.clipboard = True
        if network:
            config.network_mode = network
        if memory:
            config.resources.memory = memory
        if cpus:
            config.resources.cpus = cpus

        # Build command with YOLO flags (unless --no-yolo)
        command = [tool]
        if not no_yolo:
            command.extend(TOOL_YOLO_FLAGS.get(tool, []))
        command.extend(ctx.args)

        _run_container(config, project_dir, command, worktree_name, clone_url=clone, clone_ref=ref)

    # Update docstring
    tool_command.__doc__ = (
        f"Run {tool} in sandbox with YOLO mode (auto-confirm). Extra args passed to {tool}."
    )


# Register tool shortcuts
for _tool in TOOL_SHORTCUTS:
    _create_tool_command(_tool)


@app.command()
def config_cmd() -> None:
    """Show current configuration."""
    project_dir = Path.cwd()
    cfg = load_config(project_dir)

    console.print(f"[bold]runtime:[/] {cfg.runtime or 'auto'}")
    console.print(f"[bold]ssh_agent:[/] {cfg.ssh_agent}")
    console.print(f"[bold]git_config:[/] {cfg.git_config}")
    console.print(f"[bold]ai_config:[/] {cfg.ai_config}")
    console.print(f"[bold]container_socket:[/] {cfg.container_socket}")
    console.print(f"[bold]clipboard:[/] {cfg.clipboard}")
    console.print(f"[bold]network_mode:[/] {cfg.network_mode}")
    console.print(f"[bold]readonly_project:[/] {cfg.readonly_project}")
    console.print("\n[bold]Auto-update:[/]")
    console.print(f"  auto_pull_image: {cfg.auto_pull_image}")
    console.print(f"  auto_upgrade_tools: {cfg.auto_upgrade_tools}")
    console.print("\n[bold]Security:[/]")
    console.print(f"  forward_api_keys: {cfg.forward_api_keys}")
    console.print("\n[bold]Resource limits:[/]")
    console.print(f"  memory: {cfg.resources.memory}")
    console.print(f"  cpus: {cfg.resources.cpus or 'unlimited'}")
    console.print(f"  pids_limit: {cfg.resources.pids_limit}")
    if cfg.mounts:
        console.print(f"\n[bold]Custom mounts:[/] {cfg.mounts}")
    if cfg.env:
        console.print(f"[bold]Custom env:[/] {cfg.env}")


# Add alias for config command
app.command(name="config")(config_cmd)


@app.command(name="reset-volumes")
def reset_volumes(
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


@app.command(name="cleanup-clones")
def cleanup_clones(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove orphaned clone volumes from interrupted sessions."""
    runtime = get_runtime()

    # List all volumes and filter for clone volumes
    result = subprocess.run(
        [*runtime.command_prefix, "volume", "ls", "--format", "{{.Name}}"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        console.print(f"[red]Failed to list volumes: {result.stderr}[/]")
        raise typer.Exit(1)

    clone_volumes = [
        name.strip()
        for name in result.stdout.splitlines()
        if name.strip().startswith(CLONE_VOLUME_PREFIX)
    ]

    if not clone_volumes:
        console.print("[dim]No orphaned clone volumes found.[/]")
        return

    console.print(f"[yellow]Found {len(clone_volumes)} orphaned clone volume(s):[/]")
    for vol in clone_volumes:
        console.print(f"  - {vol}")

    if not force:
        confirm = typer.confirm("Remove these volumes?")
        if not confirm:
            raise typer.Abort()

    removed = 0
    for volume in clone_volumes:
        if runtime.remove_volume(volume):
            console.print(f"[green]Removed: {volume}[/]")
            removed += 1
        else:
            console.print(f"[red]Failed to remove: {volume}[/]")

    console.print(f"[green]Cleaned up {removed}/{len(clone_volumes)} volumes.[/]")


@app.command(name="pull-image")
def pull_image() -> None:
    """Pull the latest container image."""
    runtime = get_runtime()
    if _pull_image(runtime):
        console.print("[green]Image updated successfully.[/]")
    else:
        console.print("[red]Failed to pull image.[/]")
        raise typer.Exit(1)


@app.command(name="upgrade-tools")
def upgrade_tools() -> None:
    """Upgrade mise-managed tools in the container."""
    project_dir = Path.cwd()
    config = load_config(project_dir)
    runtime = get_runtime(config.runtime)
    if _upgrade_tools(config, project_dir, runtime):
        console.print("[green]Tools upgraded successfully.[/]")
    else:
        console.print("[red]Failed to upgrade tools.[/]")
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


def _run_clone_workflow(
    config: Config,
    runtime: ContainerRuntime,
    clone_url: str,
    command: list[str],
    ref: str | None = None,
) -> None:
    """Handle the complete clone workflow: create volume, clone, run, cleanup.

    Args:
        config: Configuration object
        runtime: Container runtime to use
        clone_url: Git repository URL to clone
        command: Command to run in the cloned repository
        ref: Optional git ref (tag or branch) to checkout
    """
    repo_name = extract_repo_name(clone_url)
    clone_volume = f"{CLONE_VOLUME_PREFIX}{uuid.uuid4().hex[:12]}"

    # Create ephemeral volume
    print_step("Creating ephemeral volume")
    if not runtime.create_volume(clone_volume):
        console.print("[red]Failed to create ephemeral volume[/]")
        raise typer.Exit(1)

    try:
        # Clone repository
        step_msg = f"Cloning {repo_name}"
        if ref:
            step_msg += f" @ {ref}"
        print_step(step_msg)
        clone_spec = build_clone_spec(config, clone_url, clone_volume, repo_name, ref=ref)
        if runtime.run(clone_spec) != 0:
            console.print("[red]Failed to clone repository[/]")
            raise typer.Exit(1)

        # Upgrade tools if enabled
        if config.auto_upgrade_tools:
            print_step("Upgrading tools")
            cmd = ["mise", "upgrade", "--yes"]
            spec = build_clone_work_spec(
                config, clone_volume, repo_name, cmd, tty=is_interactive(), stdin_open=False
            )
            runtime.run(spec)

        # Build and run work container
        spec = build_clone_work_spec(config, clone_volume, repo_name, command, tty=stdin_is_tty())

        print_step("Launching sandbox")
        print_startup_footer()

        exit_code = runtime.run(spec)
    finally:
        # Always clean up the ephemeral volume
        print_step("Removing ephemeral volume")
        if not runtime.remove_volume(clone_volume):
            logger.warning(f"Failed to remove ephemeral volume: {clone_volume}")
            console.print(f"[yellow]Warning: Failed to remove volume {clone_volume}[/]")
            console.print("[dim]Run 'podman/docker volume rm' to clean up manually[/]")

    raise typer.Exit(exit_code)


def _run_container(
    config: Config,
    project_dir: Path,
    command: list[str],
    worktree_name: str | None = None,
    clone_url: str | None = None,
    clone_ref: str | None = None,
) -> None:
    """Build spec and run container."""
    runtime = get_runtime(config.runtime)

    # Show startup header
    print_startup_header()

    # Pull image if enabled
    if config.auto_pull_image:
        print_step("Pulling image")
        _pull_image(runtime)

    # Clone mode: delegate to separate workflow
    if clone_url:
        _run_clone_workflow(config, runtime, clone_url, command, ref=clone_ref)
        return  # _run_clone_workflow raises typer.Exit

    # Normal mode
    if config.auto_upgrade_tools:
        print_step("Upgrading tools")
        _upgrade_tools(config, project_dir, runtime)

    # Build container spec - TTY only if stdin is a terminal, but always allow stdin
    spec = build_container_spec(config, project_dir, command, tty=stdin_is_tty())

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
    name: str = typer.Argument(..., help="Name of the worktree"),
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
    name: str = typer.Argument(..., help="Name of the worktree to remove"),
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
