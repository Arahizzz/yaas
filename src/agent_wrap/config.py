"""Configuration loading and merging."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from .constants import GLOBAL_CONFIG_PATH, PROJECT_CONFIG_NAME


@dataclass
class ResourceLimits:
    """Container resource limits."""

    memory: str | None = None  # e.g., "8g"
    memory_swap: str | None = None  # None = same as memory (no swap)
    cpus: float | None = None  # CPU limit
    pids_limit: int | None = None  # e.g., 1000 to prevent fork bombs


@dataclass
class Config:
    """Runtime configuration, merged from global + project files + CLI flags."""

    # Container
    runtime: str | None = None  # None = auto-detect

    # Features (what to mount/forward)
    ssh_agent: bool = False
    git_config: bool = False
    ai_config: bool = False  # Mount all AI tool configs (.claude, .codex, .gemini, .opencode)
    container_socket: bool = False  # Docker/Podman socket passthrough
    clipboard: bool = False  # Enable clipboard access for image pasting

    # Isolation
    no_network: bool = False
    readonly_project: bool = False

    # Resource limits
    resources: ResourceLimits = field(default_factory=ResourceLimits)

    # Custom
    mounts: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


def load_config(project_dir: Path) -> Config:
    """Load and merge global config → project config."""
    config = Config()

    # Global config
    if GLOBAL_CONFIG_PATH.exists():
        _merge_toml(config, GLOBAL_CONFIG_PATH)

    # Project config (overrides global)
    project_config = project_dir / PROJECT_CONFIG_NAME
    if project_config.exists():
        _merge_toml(config, project_config)

    return config


def _merge_toml(config: Config, path: Path) -> None:
    """Merge TOML file values into config."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    _merge_dict(config, data)


def _merge_dict(config: Config, data: dict[str, Any]) -> None:
    """Merge dictionary values into config."""
    for key, value in data.items():
        if key == "resources" and isinstance(value, dict):
            # Handle nested resources
            for rkey, rvalue in value.items():
                if hasattr(config.resources, rkey):
                    setattr(config.resources, rkey, rvalue)
        elif hasattr(config, key):
            setattr(config, key, value)
