"""Typer CLI application with commands."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from .config import Config, load_config
from .constants import TOOL_SHORTCUTS, TOOL_YOLO_FLAGS
from .container import build_container_spec
from .runtime import get_runtime

app = typer.Typer(
    name="agent-wrap",
    help="Run AI coding agents in sandboxed containers",
    no_args_is_help=True,
)
console = Console()


@app.command()
def run(
    command: list[str] = typer.Argument(..., help="Command to run"),
    ssh_agent: bool = typer.Option(False, "--ssh-agent", help="Forward SSH agent"),
    git_config: bool = typer.Option(False, "--git-config", help="Mount git config"),
    ai_config: bool = typer.Option(False, "--ai-config", help="Mount AI tool configs"),
    container_socket: bool = typer.Option(
        False, "--container-socket", help="Mount docker/podman socket"
    ),
    no_network: bool = typer.Option(False, "--no-network", help="Disable network"),
    image: str | None = typer.Option(None, "--image", help="Container image"),
    memory: str | None = typer.Option(None, "--memory", "-m", help="Memory limit (e.g., 8g)"),
    cpus: float | None = typer.Option(None, "--cpus", help="CPU limit (e.g., 2.0)"),
) -> None:
    """Run a command in the sandbox."""
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
    if no_network:
        config.no_network = True
    if image:
        config.image = image
    if memory:
        config.resources.memory = memory
    if cpus:
        config.resources.cpus = cpus

    _run_container(config, project_dir, list(command))


@app.command()
def shell(
    ssh_agent: bool = typer.Option(False, "--ssh-agent", help="Forward SSH agent"),
    git_config: bool = typer.Option(False, "--git-config", help="Mount git config"),
    ai_config: bool = typer.Option(False, "--ai-config", help="Mount AI tool configs"),
    container_socket: bool = typer.Option(
        False, "--container-socket", help="Mount docker/podman socket"
    ),
    no_network: bool = typer.Option(False, "--no-network", help="Disable network"),
    image: str | None = typer.Option(None, "--image", help="Container image"),
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
    if no_network:
        config.no_network = True
    if image:
        config.image = image
    if memory:
        config.resources.memory = memory
    if cpus:
        config.resources.cpus = cpus

    _run_container(config, project_dir, ["bash"])


def _create_tool_command(tool: str) -> None:
    """Create a tool-specific command (claude, codex, etc.)."""

    @app.command(
        name=tool,
        context_settings={"allow_extra_args": True, "allow_interspersed_args": False},
    )
    def tool_command(
        ctx: typer.Context,
        ssh_agent: bool = typer.Option(False, "--ssh-agent", help="Forward SSH agent"),
        git_config: bool = typer.Option(False, "--git-config", help="Mount git config"),
        ai_config: bool = typer.Option(False, "--ai-config", help="Mount AI tool configs"),
        container_socket: bool = typer.Option(
            False, "--container-socket", help="Mount docker/podman socket"
        ),
        no_network: bool = typer.Option(False, "--no-network", help="Disable network"),
        no_yolo: bool = typer.Option(False, "--no-yolo", help="Disable auto-confirm mode"),
        image: str | None = typer.Option(None, "--image", help="Container image"),
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
        if no_network:
            config.no_network = True
        if image:
            config.image = image
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

    console.print(f"[bold]image:[/] {cfg.image}")
    console.print(f"[bold]runtime:[/] {cfg.runtime or 'auto'}")
    console.print(f"[bold]ssh_agent:[/] {cfg.ssh_agent}")
    console.print(f"[bold]git_config:[/] {cfg.git_config}")
    console.print(f"[bold]ai_config:[/] {cfg.ai_config}")
    console.print(f"[bold]container_socket:[/] {cfg.container_socket}")
    console.print(f"[bold]no_network:[/] {cfg.no_network}")
    console.print(f"[bold]readonly_project:[/] {cfg.readonly_project}")
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


def _run_container(config: Config, project_dir: Path, command: list[str]) -> None:
    """Build spec and run container."""
    runtime = get_runtime(config.runtime)
    spec = build_container_spec(config, project_dir, command)

    console.print(f"[dim]Running in {config.image}...[/]")
    exit_code = runtime.run(spec)
    raise typer.Exit(exit_code)


def main() -> None:
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
