"""Startup UI with simple dividers for visual separation."""

from __future__ import annotations

from rich.console import Console
from rich.rule import Rule

_console = Console()

STARTUP_TITLE = "Preparing YAAS Environment"


def print_startup_header() -> None:
    """Print startup header divider."""
    _console.print(Rule(f"[bold cyan]{STARTUP_TITLE}[/]", style="cyan"))


def print_step(name: str) -> None:
    """Print a step indicator."""
    _console.print(f"[dim]▸ {name}...[/]")


def print_startup_footer() -> None:
    """Print startup footer divider."""
    _console.print(Rule(style="cyan"))
