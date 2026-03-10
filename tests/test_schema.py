"""Tests for CLI schema generation."""

from __future__ import annotations

import json

import click
from toon_format import decode as toon_decode

from yaas.schema import (
    _click_type_enum,
    _click_type_name,
    command_to_schema,
    dump_cli_schema,
    dump_command_schema,
    generate_cli_schema,
    param_to_schema,
)


class TestClickTypeName:
    def test_string(self) -> None:
        assert _click_type_name(click.STRING) == "string"

    def test_int(self) -> None:
        assert _click_type_name(click.INT) == "integer"

    def test_float(self) -> None:
        assert _click_type_name(click.FLOAT) == "number"

    def test_bool(self) -> None:
        assert _click_type_name(click.BOOL) == "boolean"

    def test_choice(self) -> None:
        assert _click_type_name(click.Choice(["a", "b"])) == "string"

    def test_unknown_type_falls_back_to_string(self) -> None:
        assert _click_type_name(click.Path()) == "string"


class TestClickTypeEnum:
    def test_choice(self) -> None:
        assert _click_type_enum(click.Choice(["a", "b", "c"])) == "a|b|c"

    def test_non_choice_returns_none(self) -> None:
        assert _click_type_enum(click.STRING) is None


class TestParamToSchema:
    def test_option_flat_structure(self) -> None:
        param = click.Option(["--name", "-n"], type=click.STRING, help="A name")
        schema = param_to_schema(param)
        assert schema["name"] == "name"
        assert schema["kind"] == "option"
        assert schema["type"] == "string"
        assert schema["enum"] is None
        assert schema["is_flag"] is False
        assert schema["opts"] == "--name|-n"
        assert schema["help"] == "A name"

    def test_flag(self) -> None:
        param = click.Option(["--verbose"], is_flag=True, default=False)
        schema = param_to_schema(param)
        assert schema["is_flag"] is True
        assert schema["default"] is False

    def test_argument_has_all_keys(self) -> None:
        param = click.Argument(["filename"], type=click.STRING, required=True)
        schema = param_to_schema(param)
        assert schema["name"] == "filename"
        assert schema["kind"] == "argument"
        assert schema["required"] is True
        assert schema["opts"] is None
        assert schema["is_flag"] is None
        # All keys present for tabular uniformity
        assert "type" in schema
        assert "enum" in schema
        assert "default" in schema
        assert "multiple" in schema
        assert "help" in schema

    def test_multiple(self) -> None:
        param = click.Option(["--env"], multiple=True)
        schema = param_to_schema(param)
        assert schema["multiple"] is True

    def test_choice_enum(self) -> None:
        param = click.Option(["--mode"], type=click.Choice(["a", "b", "c"]))
        schema = param_to_schema(param)
        assert schema["type"] == "string"
        assert schema["enum"] == "a|b|c"

    def test_uniform_keys(self) -> None:
        """All params produce the same set of keys for TOON tabular encoding."""
        opt = click.Option(["--foo"], is_flag=True, default=False, help="Help")
        arg = click.Argument(["bar"])
        assert set(param_to_schema(opt).keys()) == set(param_to_schema(arg).keys())


class TestCommandToSchema:
    def test_simple_command(self) -> None:
        cmd = click.Command("greet", help="Say hello", params=[
            click.Argument(["name"]),
            click.Option(["--loud"], is_flag=True, help="Shout"),
        ])
        schema = command_to_schema(cmd, "greet")
        assert schema["name"] == "greet"
        assert schema["help"] == "Say hello"
        assert len(schema["arguments"]) == 1
        assert schema["arguments"][0]["name"] == "name"
        assert len(schema["options"]) == 1
        assert schema["options"][0]["name"] == "loud"

    def test_group_with_subcommands(self) -> None:
        sub = click.Command("sub", help="A sub")

        @click.group()
        def grp() -> None:
            """A group."""

        grp.add_command(sub)
        schema = command_to_schema(grp, "grp")
        assert "subcommands" in schema
        assert "sub" in schema["subcommands"]

    def test_extra_args(self) -> None:
        cmd = click.Command("run", context_settings={"allow_extra_args": True})
        schema = command_to_schema(cmd, "run")
        assert schema["allow_extra_args"] is True

    def test_help_option_excluded(self) -> None:
        cmd = click.Command("cmd", params=[
            click.Option(["--help"], is_flag=True, is_eager=True, expose_value=False),
            click.Option(["--foo"]),
        ])
        schema = command_to_schema(cmd, "cmd")
        option_names = [o["name"] for o in schema.get("options", [])]
        assert "help" not in option_names
        assert "foo" in option_names


class TestGenerateCliSchema:
    def test_full_schema_structure(self) -> None:
        import yaas.cli

        schema = generate_cli_schema(yaas.cli.app)

        assert schema["schema_version"] == "1.0"
        assert schema["program"] == "yaas"
        assert "commands" in schema

        commands = schema["commands"]
        assert "run" in commands
        assert "box" in commands
        assert "worktree" in commands
        assert "cleanup" in commands
        assert "pull-image" in commands

    def test_run_has_extra_args(self) -> None:
        import yaas.cli

        schema = generate_cli_schema(yaas.cli.app)
        assert schema["commands"]["run"]["allow_extra_args"] is True

    def test_box_has_subcommands(self) -> None:
        import yaas.cli

        schema = generate_cli_schema(yaas.cli.app)
        box = schema["commands"]["box"]
        subs = box["subcommands"]
        assert "create" in subs
        assert "enter" in subs
        assert "list" in subs
        assert "remove" in subs

    def test_enum_options_flat(self) -> None:
        import yaas.cli

        schema = generate_cli_schema(yaas.cli.app)
        run_opts = {o["name"]: o for o in schema["commands"]["run"]["options"]}
        net = run_opts["network"]
        assert net["type"] == "string"
        assert net["enum"] == "host|bridge|none"

    def test_per_command_schema(self) -> None:
        import click
        import typer.main

        import yaas.cli

        root: click.Group = typer.main.get_command(yaas.cli.app)  # type: ignore[assignment]
        run_cmd = root.get_command(click.Context(root), "run")
        assert run_cmd is not None
        schema = command_to_schema(run_cmd, "run")
        assert schema["name"] == "run"
        assert schema["allow_extra_args"] is True
        assert "subcommands" not in schema


class TestOutputFormats:
    def test_json_output(self) -> None:
        import yaas.cli

        output = dump_cli_schema(yaas.cli.app, fmt="json")
        parsed = json.loads(output)
        assert isinstance(parsed, dict)
        assert "commands" in parsed

    def test_toon_output_roundtrips(self) -> None:
        import yaas.cli

        schema = generate_cli_schema(yaas.cli.app)
        toon_str = dump_cli_schema(yaas.cli.app, fmt="toon")
        roundtrip = toon_decode(toon_str)
        assert roundtrip["schema_version"] == schema["schema_version"]
        assert roundtrip["program"] == schema["program"]
        assert set(roundtrip["commands"].keys()) == set(schema["commands"].keys())

    def test_toon_command_output(self) -> None:
        import click
        import typer.main

        import yaas.cli

        root: click.Group = typer.main.get_command(yaas.cli.app)  # type: ignore[assignment]
        run_cmd = root.get_command(click.Context(root), "run")
        assert run_cmd is not None
        toon_str = dump_command_schema(run_cmd, "run", fmt="toon")
        roundtrip = toon_decode(toon_str)
        assert roundtrip["name"] == "run"
        assert roundtrip["allow_extra_args"] is True

    def test_toon_default_format(self) -> None:
        import yaas.cli

        toon_output = dump_cli_schema(yaas.cli.app)
        json_output = dump_cli_schema(yaas.cli.app, fmt="json")
        # Default is TOON, which should be shorter
        assert len(toon_output) < len(json_output)
