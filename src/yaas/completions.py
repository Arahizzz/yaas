"""Shell completion callbacks for the YAAS CLI."""

from __future__ import annotations

from enum import Enum


class RuntimeChoice(str, Enum):
    """Available container runtimes."""

    podman = "podman"
    podman_krun = "podman-krun"
    docker = "docker"


class NetworkMode(str, Enum):
    """Valid network modes for container isolation."""

    host = "host"
    bridge = "bridge"
    none = "none"


def complete_worktree(incomplete: str) -> list[tuple[str, str]]:
    """Autocomplete callback for worktree names."""
    try:
        from .worktree import get_yaas_worktrees

        worktrees = get_yaas_worktrees()
    except Exception:
        return []

    results = []
    for wt in worktrees:
        name = wt.get("name", "")
        if not name or not name.startswith(incomplete):
            continue
        branch = wt.get("branch", "").replace("refs/heads/", "")
        help_text = f"branch: {branch}" if branch else ""
        results.append((name, help_text))
    return results
