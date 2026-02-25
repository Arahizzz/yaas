"""Configuration loading and merging."""

from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]  # not installed on 3.11+

from .constants import GLOBAL_CONFIG_PATH, PROJECT_CONFIG_NAME

logger = logging.getLogger(__name__)


@dataclass
class ToolConfig:
    """Configuration for a tool shortcut (e.g., claude, aider)."""

    command: list[str] = field(default_factory=list)  # empty = use tool name
    yolo_flags: list[str] = field(default_factory=list)
    mounts: list[str] = field(default_factory=list)
    env: dict[str, str | bool] = field(default_factory=dict)


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
    container_socket: bool = False  # Docker/Podman socket passthrough
    clipboard: bool = False  # Enable clipboard access for image pasting

    # Isolation
    network_mode: str = "bridge"  # "host", "bridge", or "none"
    readonly_project: bool = False
    pid_mode: str | None = None

    # Resource limits
    resources: ResourceLimits = field(default_factory=ResourceLimits)

    # Custom
    mounts: list[str] = field(default_factory=list)
    env: dict[str, str | bool] = field(default_factory=dict)

    # Auto-update settings
    auto_pull_image: bool = True  # Pull image on every start
    auto_upgrade_tools: bool = True  # Run mise upgrade on every start

    # Tool shortcuts (yaas claude, yaas aider, etc.)
    tools: dict[str, ToolConfig] = field(default_factory=dict)

    # Active tool (set by CLI tool commands, None for run/shell)
    active_tool: str | None = None


def _ensure_global_config() -> None:
    """Auto-create default config.toml if missing (copy from vendored file)."""
    if GLOBAL_CONFIG_PATH.exists():
        return

    GLOBAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with resources.files("yaas.data").joinpath("config.toml").open("rb") as src:
        with open(GLOBAL_CONFIG_PATH, "wb") as dst:
            shutil.copyfileobj(src, dst)
    logger.info(f"Created default config at {GLOBAL_CONFIG_PATH}")


def load_config(project_dir: Path) -> Config:
    """Load and merge global config → project config."""
    _ensure_global_config()
    config = Config()

    # Global config
    if GLOBAL_CONFIG_PATH.exists():
        _merge_toml(config, GLOBAL_CONFIG_PATH)

    # Project config (overrides global)
    project_config = project_dir / PROJECT_CONFIG_NAME
    if project_config.exists():
        _merge_toml(config, project_config)

    return config


def load_tool_commands() -> dict[str, ToolConfig]:
    """Load tools for CLI command registration.

    Reads global + project config from CWD. Called at import time by cli.py.
    Never raises — falls back to empty dict on any error.
    """
    try:
        return load_config(Path.cwd()).tools
    except Exception:
        logger.warning("Failed to load tool config, falling back to empty", exc_info=True)
        return {}


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
        elif key == "tools" and isinstance(value, dict):
            _merge_tools(config.tools, value)
        elif hasattr(config, key):
            setattr(config, key, value)


def _merge_tools(tools: dict[str, ToolConfig], data: dict[str, Any]) -> None:
    """Merge tool entries with field-level merge per tool."""
    for name, tool_data in data.items():
        if not isinstance(tool_data, dict):
            logger.warning(
                "Skipping tool '%s': expected table, got %s", name, type(tool_data).__name__
            )
            continue

        # Validate list fields before modifying the dict
        parsed_lists: dict[str, list[str]] = {}
        valid = True
        for field_name in ("command", "yolo_flags", "mounts"):
            if field_name in tool_data:
                val = tool_data[field_name]
                if isinstance(val, list) and all(isinstance(v, str) for v in val):
                    parsed_lists[field_name] = val
                else:
                    logger.warning(
                        "Skipping tool '%s': %s must be a list of strings",
                        name, field_name,
                    )
                    valid = False
                    break

        # Validate env dict
        parsed_env: dict[str, str | bool] | None = None
        if valid and "env" in tool_data:
            env_val = tool_data["env"]
            if isinstance(env_val, dict) and all(
                isinstance(k, str) and isinstance(v, (str, bool))
                for k, v in env_val.items()
            ):
                parsed_env = env_val
            else:
                logger.warning(
                    "Skipping tool '%s': env must be a dict of str -> str | bool",
                    name,
                )
                valid = False

        if not valid:
            continue

        # Now safe to create/update the entry
        existing = tools.get(name)
        if existing is None:
            existing = ToolConfig()
            tools[name] = existing

        if "command" in parsed_lists:
            existing.command = parsed_lists["command"]
        if "yolo_flags" in parsed_lists:
            existing.yolo_flags = parsed_lists["yolo_flags"]
        if "mounts" in parsed_lists:
            existing.mounts = parsed_lists["mounts"]
        if parsed_env is not None:
            existing.env = parsed_env
