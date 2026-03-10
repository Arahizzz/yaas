"""Generate a JSON-serializable schema of the CLI command tree."""

from __future__ import annotations

import json
from typing import Any

import click
import typer
import typer.main
from toon_format import encode as toon_encode


def _click_type_name(param_type: click.ParamType) -> str:
    """Convert a Click parameter type to a simple type string."""
    if isinstance(param_type, click.types.BoolParamType):
        return "boolean"
    if isinstance(param_type, click.types.IntParamType):
        return "integer"
    if isinstance(param_type, click.types.FloatParamType):
        return "number"
    if isinstance(param_type, click.Choice):
        return "string"
    return "string"


def _click_type_enum(param_type: click.ParamType) -> str | None:
    """Extract enum values from a Click Choice type as pipe-delimited string."""
    if isinstance(param_type, click.Choice):
        return "|".join(param_type.choices)
    return None


def param_to_schema(param: click.Parameter) -> dict[str, Any]:
    """Convert a Click Parameter to a flat schema dict.

    All params produce the same set of keys (with None for absent values)
    to enable TOON tabular encoding.
    """
    is_option = isinstance(param, click.Option)
    return {
        "name": param.name,
        "kind": "option" if is_option else "argument",
        "type": _click_type_name(param.type),
        "enum": _click_type_enum(param.type),
        "required": param.required,
        "default": param.default,
        "opts": "|".join(param.opts + param.secondary_opts) if is_option else None,
        "is_flag": param.is_flag if isinstance(param, click.Option) else None,
        "multiple": param.multiple or None,
        "help": getattr(param, "help", None),
    }


_EXCLUDED_OPTIONS = frozenset({"help", "cli_introspection"})


def command_to_schema(cmd: click.Command | click.Group, name: str) -> dict[str, Any]:
    """Recursively convert a Click command/group to a schema dict."""
    schema: dict[str, Any] = {"name": name}

    if cmd.help:
        schema["help"] = cmd.help

    ctx_settings = getattr(cmd, "context_settings", {})
    if ctx_settings.get("allow_extra_args", False):
        schema["allow_extra_args"] = True

    arguments: list[dict[str, Any]] = []
    options: list[dict[str, Any]] = []

    for param in getattr(cmd, "params", []):
        if isinstance(param, click.Option) and param.name in _EXCLUDED_OPTIONS:
            continue
        entry = param_to_schema(param)
        if isinstance(param, click.Argument):
            arguments.append(entry)
        else:
            options.append(entry)

    if arguments:
        schema["arguments"] = arguments
    if options:
        schema["options"] = options

    if isinstance(cmd, click.Group):
        subcommands: dict[str, Any] = {}
        for sub_name in cmd.list_commands(click.Context(cmd)):
            sub_cmd = cmd.get_command(click.Context(cmd), sub_name)
            if sub_cmd is not None:
                subcommands[sub_name] = command_to_schema(sub_cmd, sub_name)
        if subcommands:
            schema["subcommands"] = subcommands

    return schema


def generate_cli_schema(app: typer.Typer) -> dict[str, Any]:
    """Generate the full CLI schema from a Typer app."""
    root: click.Group = typer.main.get_command(app)  # type: ignore[assignment]

    commands: dict[str, Any] = {}
    for name in root.list_commands(click.Context(root)):
        cmd = root.get_command(click.Context(root), name)
        if cmd is not None:
            commands[name] = command_to_schema(cmd, name)

    return {
        "schema_version": "1.0",
        "program": root.name or "yaas",
        "description": root.help or "",
        "commands": commands,
    }


def dump_cli_schema(app: typer.Typer, fmt: str = "toon") -> str:
    """Generate and serialize the CLI schema."""
    schema = generate_cli_schema(app)
    if fmt == "json":
        return json.dumps(schema, indent=2)
    return toon_encode(schema)


def dump_command_schema(cmd: click.Command | click.Group, name: str, fmt: str = "toon") -> str:
    """Generate and serialize a single command's schema."""
    schema = command_to_schema(cmd, name)
    if fmt == "json":
        return json.dumps(schema, indent=2)
    return toon_encode(schema)
