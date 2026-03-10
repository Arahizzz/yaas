"""Configuration loading and merging."""

from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass, field, replace
from dataclasses import fields as dc_fields
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
class ResourceLimits:
    """Container resource limits."""

    memory: str | None = None  # e.g., "8g"
    memory_swap: str | None = None  # None = same as memory (no swap)
    cpus: float | None = None  # CPU limit
    pids_limit: int | None = None  # e.g., 1000 to prevent fork bombs


@dataclass
class SecuritySettings:
    """Container security settings.

    For ToolConfig: None means "inherit from global config".
    For Config: concrete defaults are set in the Config dataclass.
    """

    capabilities: list[str] | None = None  # Exact set of caps; None = runtime defaults
    seccomp_profile: str | None = None  # None = runtime default, path = custom JSON


@dataclass
class ContainerSettings:
    """Container runtime settings shared by Config and ToolConfig.

    For ToolConfig: None means "inherit from global config".
    For Config: defaults are concrete values (False, "bridge", etc.).
    """

    base: str | None = None  # "default", "minimal", "none" — controls config inheritance
    ssh_agent: bool | None = None
    git_config: bool | None = None
    podman: bool | None = None
    podman_docker_socket: bool | None = None
    clipboard: bool | None = None
    network_mode: str | None = None
    mount_project: bool | None = None
    readonly_project: bool | None = None
    runtime: str | None = None
    pid_mode: str | None = None
    lxcfs: bool | None = None
    resources: ResourceLimits | None = None
    security: SecuritySettings | None = None
    auto_pull_image: bool | None = None
    auto_upgrade_tools: bool | None = None
    mounts: list[str] = field(default_factory=list)
    ports: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    env: dict[str, str | bool] = field(default_factory=dict)


@dataclass
class ToolConfig(ContainerSettings):
    """Configuration for a tool shortcut (e.g., claude, aider).

    Inherits container setting fields from ContainerSettings.
    None values mean "inherit from the global/project config".
    """

    command: list[str] = field(default_factory=list)  # empty = use tool name
    yolo_flags: list[str] = field(default_factory=list)


@dataclass
class BoxSpec(ContainerSettings):
    """Configuration for a persistent box (e.g., shell, hardened).

    Similar to ToolConfig but for persistent containers.
    Uses Docker entrypoint/command semantics:
    - entrypoint: init process (default: ["sleep", "infinity"])
    - command: args to entrypoint (default: [])
    - shell: default shell for `yaas box enter` (default: ["bash"])
    """

    entrypoint: list[str] | None = None  # Default: ["sleep", "infinity"]
    command: list[str] = field(default_factory=list)
    shell: list[str] | None = None  # Default: ["bash"]


@dataclass
class Config(ContainerSettings):
    """Runtime configuration, merged from global + project files + CLI flags.

    Inherits container setting fields from ContainerSettings with concrete defaults.
    """

    # Override ContainerSettings defaults to concrete values.
    # Type narrowing (bool | None → bool) is valid: Config always has concrete values.
    ssh_agent: bool = False
    git_config: bool = False
    podman: bool = False
    podman_docker_socket: bool = False
    clipboard: bool = False
    network_mode: str = "bridge"
    mount_project: bool = True
    readonly_project: bool = False
    lxcfs: bool = False
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    security: SecuritySettings = field(
        default_factory=lambda: SecuritySettings(
            capabilities=[
                "CHOWN",
                "DAC_OVERRIDE",
                "FOWNER",
                "FSETID",
                "KILL",
                "NET_BIND_SERVICE",
                "SETGID",
                "SETUID",
            ],
        )
    )

    # Override ContainerSettings auto-update defaults to concrete values
    auto_pull_image: bool = True  # Pull image on every start
    preamble: bool = True  # Set YAAS_PREAMBLE env var with sandbox info
    auto_upgrade_tools: bool = True  # Run mise upgrade on every start

    # Tool shortcuts (yaas claude, yaas aider, etc.)
    tools: dict[str, ToolConfig] = field(default_factory=dict)

    # Box specs (yaas box create <name> <spec>)
    boxes: dict[str, BoxSpec] = field(default_factory=dict)

    # Active tool (set by CLI tool commands, None for run)
    active_tool: str | None = None


# Precompute ContainerSettings field names for generic merge/resolve
_CONTAINER_FIELDS = frozenset(f.name for f in dc_fields(ContainerSettings))
_SPECIAL_FIELDS = frozenset({"base", "mounts", "ports", "devices", "env", "resources", "security"})


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


def _apply_overrides(resolved: Config, overrides: ContainerSettings) -> None:
    """Apply ContainerSettings overrides onto a resolved Config (in-place).

    Used by both resolve_effective_config (tools) and resolve_box_config (boxes).
    """
    # Scalar overrides — generic via ContainerSettings field introspection
    for field_name in _CONTAINER_FIELDS - _SPECIAL_FIELDS:
        value = getattr(overrides, field_name)
        if value is not None:
            setattr(resolved, field_name, value)

    # Resource overrides (field-level merge)
    if overrides.resources is not None:
        resolved.resources = replace(resolved.resources) if resolved.resources else ResourceLimits()
        for rf in dc_fields(ResourceLimits):
            rv = getattr(overrides.resources, rf.name)
            if rv is not None:
                setattr(resolved.resources, rf.name, rv)

    # Security overrides (field-level merge)
    if overrides.security is not None:
        resolved.security = replace(resolved.security) if resolved.security else SecuritySettings()
        for sf in dc_fields(SecuritySettings):
            sv = getattr(overrides.security, sf.name)
            if sv is not None:
                setattr(resolved.security, sf.name, sv)

    # Env overlay
    if overrides.env:
        resolved.env = {**resolved.env, **overrides.env}


def _get_base_config(base: str) -> Config:
    """Create a starting Config for a given base level.

    - "minimal": hardcoded Config() defaults (no global/project merge)
    - "none": absolute zero — no caps, no network, no shared volumes
    """
    if base == "none":
        return Config(
            network_mode="none",
            security=SecuritySettings(capabilities=[]),
        )
    # "minimal" = plain defaults
    return Config()


def resolve_effective_config(config: Config) -> Config:
    """Apply active tool overrides to produce effective config.

    Returns a new Config with tool overrides applied on top of global/project
    settings. Mounts are NOT merged here (they use different parsing logic
    and are handled in container.py).

    Priority: global → project → tool overrides (this function) → CLI flags (caller).
    """
    if not config.active_tool:
        return config

    tool = config.tools.get(config.active_tool)
    if not tool:
        return config

    base = tool.base
    if base is not None and base != "default":
        resolved = _get_base_config(base)
        resolved.tools = config.tools
        resolved.boxes = config.boxes
        resolved.base = base
    else:
        resolved = replace(config)

    _apply_overrides(resolved, tool)
    return resolved


def resolve_box_config(config: Config, box_name: str) -> Config:
    """Apply box spec overrides to produce effective config for a box.

    Similar to resolve_effective_config but for boxes.
    Default mount_project=False for boxes (unless explicitly set).
    """
    box = config.boxes.get(box_name)

    base = box.base if box else None
    if base is not None and base != "default":
        resolved = _get_base_config(base)
        resolved.tools = config.tools
        resolved.boxes = config.boxes
        resolved.base = base
    else:
        resolved = replace(config)

    # Boxes default to no project mount
    resolved.mount_project = False

    if box:
        _apply_overrides(resolved, box)

    return resolved


def _merge_toml(config: Config, path: Path) -> None:
    """Merge TOML file values into config."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    _merge_dict(config, data)


def _merge_dict(config: Config, data: dict[str, Any]) -> None:
    """Merge dictionary values into config."""
    # Backward compat: translate deprecated container_socket to ignored warning
    if "container_socket" in data:
        logger.warning(
            "container_socket is deprecated and ignored. "
            'Use mounts = ["/var/run/docker.sock"] for DoD, '
            "or podman = true for DinD."
        )
        data = {k: v for k, v in data.items() if k != "container_socket"}

    for key, value in data.items():
        if key == "resources" and isinstance(value, dict) and config.resources is not None:
            # Handle nested resources
            for rkey, rvalue in value.items():
                if hasattr(config.resources, rkey):
                    setattr(config.resources, rkey, rvalue)
        elif key == "security" and isinstance(value, dict) and config.security is not None:
            # Handle nested security
            for skey, svalue in value.items():
                if hasattr(config.security, skey):
                    setattr(config.security, skey, svalue)
        elif key == "tools" and isinstance(value, dict):
            _merge_tools(config.tools, value)
        elif key == "box" and isinstance(value, dict):
            _merge_boxes(config.boxes, value)
        elif key in ("mounts", "ports", "devices") and isinstance(value, list):
            getattr(config, key).extend(value)
        elif key == "env" and isinstance(value, dict):
            config.env.update(value)
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
        for field_name in ("command", "yolo_flags", "mounts", "ports", "devices"):
            if field_name in tool_data:
                val = tool_data[field_name]
                if isinstance(val, list) and all(isinstance(v, str) for v in val):
                    parsed_lists[field_name] = val
                else:
                    logger.warning(
                        "Skipping tool '%s': %s must be a list of strings",
                        name,
                        field_name,
                    )
                    valid = False
                    break

        # Validate env dict
        parsed_env: dict[str, str | bool] | None = None
        if valid and "env" in tool_data:
            env_val = tool_data["env"]
            if isinstance(env_val, dict) and all(
                isinstance(k, str) and isinstance(v, (str, bool)) for k, v in env_val.items()
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

        # Backward compat: warn on deprecated container_socket in tool config
        if "container_socket" in tool_data:
            logger.warning(
                "container_socket in tool '%s' is deprecated and ignored. "
                "Use mounts for DoD or podman = true for DinD.",
                name,
            )

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
            existing.mounts.extend(parsed_lists["mounts"])
        if "ports" in parsed_lists:
            existing.ports.extend(parsed_lists["ports"])
        if "devices" in parsed_lists:
            existing.devices.extend(parsed_lists["devices"])
        if parsed_env is not None:
            existing.env.update(parsed_env)

        # Container setting overrides — generic via ContainerSettings fields
        for field_name in _CONTAINER_FIELDS - _SPECIAL_FIELDS:
            if field_name in tool_data and isinstance(tool_data[field_name], (bool, str)):
                setattr(existing, field_name, tool_data[field_name])

        if "resources" in tool_data and isinstance(tool_data["resources"], dict):
            if existing.resources is None:
                existing.resources = ResourceLimits()
            for rkey, rvalue in tool_data["resources"].items():
                if hasattr(existing.resources, rkey):
                    setattr(existing.resources, rkey, rvalue)

        if "security" in tool_data and isinstance(tool_data["security"], dict):
            if existing.security is None:
                existing.security = SecuritySettings()
            for skey, svalue in tool_data["security"].items():
                if hasattr(existing.security, skey):
                    setattr(existing.security, skey, svalue)

        # Base field
        if "base" in tool_data and isinstance(tool_data["base"], str):
            existing.base = tool_data["base"]


def _merge_boxes(boxes: dict[str, BoxSpec], data: dict[str, Any]) -> None:
    """Merge box entries with field-level merge per box."""
    for name, box_data in data.items():
        if not isinstance(box_data, dict):
            logger.warning(
                "Skipping box '%s': expected table, got %s", name, type(box_data).__name__
            )
            continue

        # Validate list fields
        parsed_lists: dict[str, list[str]] = {}
        valid = True
        for field_name in ("entrypoint", "command", "shell", "mounts", "ports", "devices"):
            if field_name in box_data:
                val = box_data[field_name]
                if isinstance(val, list) and all(isinstance(v, str) for v in val):
                    parsed_lists[field_name] = val
                else:
                    logger.warning(
                        "Skipping box '%s': %s must be a list of strings",
                        name,
                        field_name,
                    )
                    valid = False
                    break

        # Validate env dict
        parsed_env: dict[str, str | bool] | None = None
        if valid and "env" in box_data:
            env_val = box_data["env"]
            if isinstance(env_val, dict) and all(
                isinstance(k, str) and isinstance(v, (str, bool)) for k, v in env_val.items()
            ):
                parsed_env = env_val
            else:
                logger.warning(
                    "Skipping box '%s': env must be a dict of str -> str | bool",
                    name,
                )
                valid = False

        if not valid:
            continue

        existing = boxes.get(name)
        if existing is None:
            existing = BoxSpec()
            boxes[name] = existing

        # Box-specific fields
        if "entrypoint" in parsed_lists:
            existing.entrypoint = parsed_lists["entrypoint"]
        if "command" in parsed_lists:
            existing.command = parsed_lists["command"]
        if "shell" in parsed_lists:
            existing.shell = parsed_lists["shell"]
        if "mounts" in parsed_lists:
            existing.mounts.extend(parsed_lists["mounts"])
        if "ports" in parsed_lists:
            existing.ports.extend(parsed_lists["ports"])
        if "devices" in parsed_lists:
            existing.devices.extend(parsed_lists["devices"])
        if parsed_env is not None:
            existing.env.update(parsed_env)

        # Container setting overrides
        for field_name in _CONTAINER_FIELDS - _SPECIAL_FIELDS:
            if field_name in box_data and isinstance(box_data[field_name], (bool, str)):
                setattr(existing, field_name, box_data[field_name])

        if "resources" in box_data and isinstance(box_data["resources"], dict):
            if existing.resources is None:
                existing.resources = ResourceLimits()
            for rkey, rvalue in box_data["resources"].items():
                if hasattr(existing.resources, rkey):
                    setattr(existing.resources, rkey, rvalue)

        if "security" in box_data and isinstance(box_data["security"], dict):
            if existing.security is None:
                existing.security = SecuritySettings()
            for skey, svalue in box_data["security"].items():
                if hasattr(existing.security, skey):
                    setattr(existing.security, skey, svalue)

        if "base" in box_data and isinstance(box_data["base"], str):
            existing.base = box_data["base"]


def load_box_specs() -> dict[str, BoxSpec]:
    """Load box specs for CLI command registration.

    Reads global + project config from CWD. Called at import time by cli.py.
    Never raises — falls back to empty dict on any error.
    """
    try:
        return load_config(Path.cwd()).boxes
    except Exception:
        logger.warning("Failed to load box config, falling back to empty", exc_info=True)
        return {}
