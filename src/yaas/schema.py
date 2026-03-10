"""Generate a JSON-serializable schema of the CLI command tree."""

from __future__ import annotations

import json
from typing import Any

import click
import typer
import typer.main


def click_type_to_json(param_type: click.ParamType) -> dict[str, Any]:
    """Convert a Click parameter type to a JSON Schema type descriptor."""
    if isinstance(param_type, click.types.BoolParamType):
        return {"type": "boolean"}
    if isinstance(param_type, click.types.IntParamType):
        return {"type": "integer"}
    if isinstance(param_type, click.types.FloatParamType):
        return {"type": "number"}
    if isinstance(param_type, click.Choice):
        return {"type": "string", "enum": list(param_type.choices)}
    return {"type": "string"}


def param_to_schema(param: click.Parameter) -> dict[str, Any]:
    """Convert a Click Parameter to a schema dict."""
    is_option = isinstance(param, click.Option)
    schema: dict[str, Any] = {
        "name": param.name,
        "kind": "option" if is_option else "argument",
        "type": click_type_to_json(param.type),
        "required": param.required,
    }

    if is_option:
        assert isinstance(param, click.Option)
        schema["opts"] = list(param.opts) + list(param.secondary_opts)
        schema["is_flag"] = param.is_flag

    if param.multiple:
        schema["multiple"] = True

    if param.default is not None:
        schema["default"] = param.default

    if help_text := getattr(param, "help", None):
        schema["help"] = help_text

    return schema


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


def dump_cli_schema(app: typer.Typer) -> str:
    """Generate and serialize the CLI schema to a JSON string."""
    return json.dumps(generate_cli_schema(app), indent=2)
