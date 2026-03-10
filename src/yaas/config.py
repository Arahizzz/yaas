"""Configuration loading and merging."""

from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass, field, replace
from dataclasses import fields as dc_fields
from importlib import resources
from pathlib import Path
from typing import Any, TypeVar

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
    - command: container command (default: ["sleep", "infinity"])
    """

    command: list[str] = field(default_factory=list)


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


_T = TypeVar("_T", ToolConfig, BoxSpec)


def _merge_container_entries(
    entries: dict[str, _T],
    data: dict[str, Any],
    factory: type[_T],
    list_fields: tuple[str, ...],
    replace_fields: frozenset[str],
    extend_fields: frozenset[str],
    label: str,
) -> None:
    """Generic merge for tool/box entries with validation and field-level merge.

    Args:
        entries: Dict to merge into (config.tools or config.boxes).
        data: Raw TOML data for the section.
        factory: ToolConfig or BoxSpec class.
        list_fields: All list[str] fields to validate.
        replace_fields: List fields that replace (e.g. command, yolo_flags, entrypoint, shell).
        extend_fields: List fields that extend (mounts, ports, devices).
        label: "tool" or "box" for warning messages.
    """
    for name, entry_data in data.items():
        if not isinstance(entry_data, dict):
            logger.warning(
                "Skipping %s '%s': expected table, got %s",
                label,
                name,
                type(entry_data).__name__,
            )
            continue

        # Validate list fields
        parsed_lists: dict[str, list[str]] = {}
        valid = True
        for field_name in list_fields:
            if field_name in entry_data:
                val = entry_data[field_name]
                if isinstance(val, list) and all(isinstance(v, str) for v in val):
                    parsed_lists[field_name] = val
                else:
                    logger.warning(
                        "Skipping %s '%s': %s must be a list of strings", label, name, field_name
                    )
                    valid = False
                    break

        # Validate env dict
        parsed_env: dict[str, str | bool] | None = None
        if valid and "env" in entry_data:
            env_val = entry_data["env"]
            if isinstance(env_val, dict) and all(
                isinstance(k, str) and isinstance(v, (str, bool)) for k, v in env_val.items()
            ):
                parsed_env = env_val
            else:
                logger.warning(
                    "Skipping %s '%s': env must be a dict of str -> str | bool", label, name
                )
                valid = False

        if not valid:
            continue

        # Create or get existing entry
        existing = entries.get(name)
        if existing is None:
            existing = factory()
            entries[name] = existing

        # Apply list fields with correct semantics
        for field_name, values in parsed_lists.items():
            if field_name in replace_fields:
                setattr(existing, field_name, values)
            elif field_name in extend_fields:
                getattr(existing, field_name).extend(values)
        if parsed_env is not None:
            existing.env.update(parsed_env)

        # Container setting overrides — generic via ContainerSettings fields
        for field_name in _CONTAINER_FIELDS - _SPECIAL_FIELDS:
            if field_name in entry_data and isinstance(entry_data[field_name], (bool, str)):
                setattr(existing, field_name, entry_data[field_name])

        if "resources" in entry_data and isinstance(entry_data["resources"], dict):
            if existing.resources is None:
                existing.resources = ResourceLimits()
            for rkey, rvalue in entry_data["resources"].items():
                if hasattr(existing.resources, rkey):
                    setattr(existing.resources, rkey, rvalue)

        if "security" in entry_data and isinstance(entry_data["security"], dict):
            if existing.security is None:
                existing.security = SecuritySettings()
            for skey, svalue in entry_data["security"].items():
                if hasattr(existing.security, skey):
                    setattr(existing.security, skey, svalue)

        if "base" in entry_data and isinstance(entry_data["base"], str):
            existing.base = entry_data["base"]


_TOOL_LIST_FIELDS = ("command", "yolo_flags", "mounts", "ports", "devices")
_TOOL_REPLACE_FIELDS = frozenset({"command", "yolo_flags"})
_TOOL_EXTEND_FIELDS = frozenset({"mounts", "ports", "devices"})

_BOX_LIST_FIELDS = ("command", "mounts", "ports", "devices")
_BOX_REPLACE_FIELDS = frozenset({"command"})
_BOX_EXTEND_FIELDS = frozenset({"mounts", "ports", "devices"})


def _merge_tools(tools: dict[str, ToolConfig], data: dict[str, Any]) -> None:
    """Merge tool entries with field-level merge per tool."""
    _merge_container_entries(
        tools,
        data,
        ToolConfig,
        _TOOL_LIST_FIELDS,
        _TOOL_REPLACE_FIELDS,
        _TOOL_EXTEND_FIELDS,
        "tool",
    )


def _merge_boxes(boxes: dict[str, BoxSpec], data: dict[str, Any]) -> None:
    """Merge box entries with field-level merge per box."""
    _merge_container_entries(
        boxes, data, BoxSpec, _BOX_LIST_FIELDS, _BOX_REPLACE_FIELDS, _BOX_EXTEND_FIELDS, "box"
    )


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
