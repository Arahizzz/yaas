"""Tests for CLI schema generation."""

from __future__ import annotations

import json

import click

from yaas.schema import (
    click_type_to_json,
    command_to_schema,
    generate_cli_schema,
    param_to_schema,
)


class TestClickTypeToJson:
    def test_string(self) -> None:
        assert click_type_to_json(click.STRING) == {"type": "string"}

    def test_int(self) -> None:
        assert click_type_to_json(click.INT) == {"type": "integer"}

    def test_float(self) -> None:
        assert click_type_to_json(click.FLOAT) == {"type": "number"}

    def test_bool(self) -> None:
        assert click_type_to_json(click.BOOL) == {"type": "boolean"}

    def test_choice(self) -> None:
        result = click_type_to_json(click.Choice(["a", "b", "c"]))
        assert result == {"type": "string", "enum": ["a", "b", "c"]}

    def test_unknown_type_falls_back_to_string(self) -> None:
        assert click_type_to_json(click.Path()) == {"type": "string"}


class TestParamToSchema:
    def test_option(self) -> None:
        param = click.Option(["--name", "-n"], type=click.STRING, help="A name")
        schema = param_to_schema(param)
        assert schema["name"] == "name"
        assert schema["kind"] == "option"
        assert schema["type"] == {"type": "string"}
        assert schema["is_flag"] is False
        assert "--name" in schema["opts"]
        assert "-n" in schema["opts"]
        assert schema["help"] == "A name"

    def test_flag(self) -> None:
        param = click.Option(["--verbose"], is_flag=True, default=False)
        schema = param_to_schema(param)
        assert schema["is_flag"] is True
        assert schema["default"] is False

    def test_argument(self) -> None:
        param = click.Argument(["filename"], type=click.STRING, required=True)
        schema = param_to_schema(param)
        assert schema["name"] == "filename"
        assert schema["kind"] == "argument"
        assert schema["required"] is True
        assert "opts" not in schema
        assert "is_flag" not in schema

    def test_multiple(self) -> None:
        param = click.Option(["--env"], multiple=True)
        schema = param_to_schema(param)
        assert schema["multiple"] is True

    def test_none_default_omitted(self) -> None:
        param = click.Option(["--foo"], default=None)
        schema = param_to_schema(param)
        assert "default" not in schema


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

    def test_enum_options(self) -> None:
        import yaas.cli

        schema = generate_cli_schema(yaas.cli.app)
        run_opts = {o["name"]: o for o in schema["commands"]["run"]["options"]}
        net = run_opts["network"]
        assert net["type"]["enum"] == ["host", "bridge", "none"]

    def test_output_is_valid_json(self) -> None:
        import yaas.cli
        from yaas.schema import dump_cli_schema

        output = dump_cli_schema(yaas.cli.app)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_per_command_schema(self) -> None:
        import click
        import typer.main

        import yaas.cli
        from yaas.schema import command_to_schema

        root: click.Group = typer.main.get_command(yaas.cli.app)  # type: ignore[assignment]
        run_cmd = root.get_command(click.Context(root), "run")
        assert run_cmd is not None
        schema = command_to_schema(run_cmd, "run")
        assert schema["name"] == "run"
        assert schema["allow_extra_args"] is True
        assert "subcommands" not in schema
