"""Startup UI with simple dividers for visual separation."""

from __future__ import annotations

import sys

from rich.console import Console
from rich.rule import Rule

_console = Console()

STARTUP_TITLE = "Preparing YAAS Environment"


def is_interactive() -> bool:
    """Check if stdout is a terminal (for UI output)."""
    return sys.stdout.isatty()


def stdin_is_tty() -> bool:
    """Check if stdin is a terminal (for container TTY allocation)."""
    return sys.stdin.isatty()


def print_startup_header() -> None:
    """Print startup header divider."""
    if is_interactive():
        _console.print(Rule(f"[bold cyan]{STARTUP_TITLE}[/]", style="cyan"))
    else:
        # Print to stderr so it doesn't mix with piped stdout
        print(f"=== {STARTUP_TITLE} ===", file=sys.stderr)


def print_step(name: str) -> None:
    """Print a step indicator."""
    if is_interactive():
        _console.print(f"[dim]▸ {name}...[/]")
    else:
        print(f"- {name}...", file=sys.stderr)


def print_startup_footer() -> None:
    """Print startup footer divider."""
    if is_interactive():
        _console.print(Rule(style="cyan"))
    else:
        print("=" * 40, file=sys.stderr)
