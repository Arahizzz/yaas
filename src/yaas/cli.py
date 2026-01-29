"""Typer CLI application with commands."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import typer
from rich.console import Console

from .config import Config, load_config
from .constants import (
    CACHE_VOLUME,
    LAST_PULL_FILE,
    LAST_UPGRADE_FILE,
    MISE_DATA_VOLUME,
    RUNTIME_IMAGE,
    TOOL_SHORTCUTS,
    TOOL_YOLO_FLAGS,
)
from .container import build_container_spec
from .runtime import get_runtime

app = typer.Typer(
    name="yaas",
    help="Run AI coding agents in sandboxed containers",
    no_args_is_help=True,
)
console = Console()


@app.command(
    context_settings={
        "allow_extra_args": True,
        "allow_interspersed_args": False,
        "ignore_unknown_options": True,
    },
)
def run(
    ctx: typer.Context,
    ssh_agent: bool = typer.Option(False, "--ssh-agent", help="Forward SSH agent"),
    git_config: bool = typer.Option(False, "--git-config", help="Mount git config"),
    ai_config: bool = typer.Option(False, "--ai-config", help="Mount AI tool configs"),
    container_socket: bool = typer.Option(
        False, "--container-socket", help="Mount docker/podman socket"
    ),
    clipboard: bool = typer.Option(
        False, "--clipboard", help="Enable clipboard access for image pasting"
    ),
    no_network: bool = typer.Option(False, "--no-network", help="Disable network"),
    memory: str | None = typer.Option(None, "--memory", "-m", help="Memory limit (e.g., 8g)"),
    cpus: float | None = typer.Option(None, "--cpus", help="CPU limit (e.g., 2.0)"),
) -> None:
    """Run a command in the sandbox."""
    if not ctx.args:
        raise typer.BadParameter("Missing command to run")

    project_dir = Path.cwd()
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
    if no_network:
        config.no_network = True
    if memory:
        config.resources.memory = memory
    if cpus:
        config.resources.cpus = cpus

    _run_container(config, project_dir, ctx.args)


@app.command()
def shell(
    ssh_agent: bool = typer.Option(False, "--ssh-agent", help="Forward SSH agent"),
    git_config: bool = typer.Option(False, "--git-config", help="Mount git config"),
    ai_config: bool = typer.Option(False, "--ai-config", help="Mount AI tool configs"),
    container_socket: bool = typer.Option(
        False, "--container-socket", help="Mount docker/podman socket"
    ),
    clipboard: bool = typer.Option(
        False, "--clipboard", help="Enable clipboard access for image pasting"
    ),
    no_network: bool = typer.Option(False, "--no-network", help="Disable network"),
    memory: str | None = typer.Option(None, "--memory", "-m", help="Memory limit (e.g., 8g)"),
    cpus: float | None = typer.Option(None, "--cpus", help="CPU limit (e.g., 2.0)"),
) -> None:
    """Start interactive shell in sandbox."""
    project_dir = Path.cwd()
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
    if no_network:
        config.no_network = True
    if memory:
        config.resources.memory = memory
    if cpus:
        config.resources.cpus = cpus

    _run_container(config, project_dir, ["bash"])


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
        ssh_agent: bool = typer.Option(False, "--ssh-agent", help="Forward SSH agent"),
        git_config: bool = typer.Option(False, "--git-config", help="Mount git config"),
        ai_config: bool = typer.Option(False, "--ai-config", help="Mount AI tool configs"),
        container_socket: bool = typer.Option(
            False, "--container-socket", help="Mount docker/podman socket"
        ),
        clipboard: bool = typer.Option(
            False, "--clipboard", help="Enable clipboard access for image pasting"
        ),
        no_network: bool = typer.Option(False, "--no-network", help="Disable network"),
        no_yolo: bool = typer.Option(False, "--no-yolo", help="Disable auto-confirm mode"),
        memory: str | None = typer.Option(
            None, "--memory", "-m", help="Memory limit (e.g., 8g)"
        ),
        cpus: float | None = typer.Option(None, "--cpus", help="CPU limit (e.g., 2.0)"),
    ) -> None:
        """Run AI tool in sandbox with YOLO mode (auto-confirm)."""
        project_dir = Path.cwd()
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
        if no_network:
            config.no_network = True
        if memory:
            config.resources.memory = memory
        if cpus:
            config.resources.cpus = cpus

        # Build command with YOLO flags (unless --no-yolo)
        command = [tool]
        if not no_yolo:
            command.extend(TOOL_YOLO_FLAGS.get(tool, []))
        command.extend(ctx.args)

        _run_container(config, project_dir, command)

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
    console.print(f"[bold]no_network:[/] {cfg.no_network}")
    console.print(f"[bold]readonly_project:[/] {cfg.readonly_project}")
    console.print("\n[bold]Auto-update:[/]")
    console.print(f"  auto_pull_image: {cfg.auto_pull_image}")
    console.print(f"  auto_upgrade_tools: {cfg.auto_upgrade_tools}")
    console.print(f"  image_pull_interval: {cfg.image_pull_interval}s")
    console.print(f"  tool_upgrade_interval: {cfg.tool_upgrade_interval}s")
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
    """Reset yaas volumes (removes installed tools and cache)."""
    volumes = [MISE_DATA_VOLUME, CACHE_VOLUME]

    if not force:
        console.print("[yellow]This will delete all installed tools and cache.[/]")
        console.print(f"Volumes: {', '.join(volumes)}")
        confirm = typer.confirm("Continue?")
        if not confirm:
            raise typer.Abort()

    runtime = get_runtime()
    for volume in volumes:
        result = subprocess.run(
            [runtime.name, "volume", "rm", "-f", volume],
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


def _pull_image(runtime) -> bool:
    """Pull container image and update timestamp. Returns True on success."""
    console.print(f"[dim]Pulling {RUNTIME_IMAGE}...[/]")
    result = subprocess.run([runtime.name, "pull", RUNTIME_IMAGE])
    if result.returncode == 0:
        LAST_PULL_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_PULL_FILE.write_text(str(time.time()))
        return True
    return False


def _ensure_fresh_image(config: Config, runtime) -> None:
    """Pull image if older than config.image_pull_interval."""
    if not config.auto_pull_image:
        return

    if LAST_PULL_FILE.exists():
        try:
            last_pull = float(LAST_PULL_FILE.read_text().strip())
            if time.time() - last_pull < config.image_pull_interval:
                return  # Still fresh
        except (ValueError, OSError):
            pass  # Corrupted file, proceed with pull

    _pull_image(runtime)


def _upgrade_tools(config: Config, project_dir: Path, runtime) -> bool:
    """Run mise upgrade in container. Returns True on success."""
    console.print("[dim]Upgrading mise tools...[/]")
    spec = build_container_spec(config, project_dir, ["mise", "upgrade", "--yes"], interactive=False)
    exit_code = runtime.run(spec)
    if exit_code == 0:
        LAST_UPGRADE_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_UPGRADE_FILE.write_text(str(time.time()))
        return True
    return False


def _ensure_fresh_tools(config: Config, project_dir: Path, runtime) -> None:
    """Upgrade tools if older than config.tool_upgrade_interval."""
    if not config.auto_upgrade_tools:
        return

    if LAST_UPGRADE_FILE.exists():
        try:
            last_upgrade = float(LAST_UPGRADE_FILE.read_text().strip())
            if time.time() - last_upgrade < config.tool_upgrade_interval:
                return  # Still fresh
        except (ValueError, OSError):
            pass  # Corrupted file, proceed with upgrade

    _upgrade_tools(config, project_dir, runtime)


def _run_container(config: Config, project_dir: Path, command: list[str]) -> None:
    """Build spec and run container."""
    runtime = get_runtime(config.runtime)
    _ensure_fresh_image(config, runtime)
    _ensure_fresh_tools(config, project_dir, runtime)
    spec = build_container_spec(config, project_dir, command)

    console.print("[dim]Launching sandbox container...[/]")
    exit_code = runtime.run(spec)
    raise typer.Exit(exit_code)


def main() -> None:
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
