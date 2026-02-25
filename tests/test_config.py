"""Tests for configuration loading."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from yaas.config import Config, ResourceLimits, load_config, load_tool_commands


def test_default_config() -> None:
    """Test that Config dataclass defaults are set correctly."""
    config = Config()

    assert config.runtime is None
    assert config.ssh_agent is False
    assert config.git_config is False
    assert config.container_socket is False
    assert config.network_mode == "bridge"
    assert config.readonly_project is False
    assert config.active_tool is None
    assert config.tools == {}
    assert config.env == {}
    assert config.mounts == []


def test_resource_limits_defaults() -> None:
    """Test that resource limit defaults are set correctly."""
    limits = ResourceLimits()

    assert limits.memory is None
    assert limits.memory_swap is None
    assert limits.cpus is None
    assert limits.pids_limit is None


def test_project_config_overrides() -> None:
    """Test that project config overrides defaults."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
ssh_agent = true
network_mode = "none"

[resources]
memory = "16g"
cpus = 4.0
""")
        config = load_config(project_dir)

    assert config.ssh_agent is True
    assert config.network_mode == "none"
    assert config.resources.memory == "16g"
    assert config.resources.cpus == 4.0


def test_tools_from_project_config() -> None:
    """Test that tools are loaded from project config."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
[tools.claude]
yolo_flags = ["--dangerously-skip-permissions"]
mounts = [".claude", ".claude.json"]
env = { ANTHROPIC_API_KEY = true }

[tools.aider]
yolo_flags = ["--yes"]
mounts = [".aider"]
""")
        config = load_config(project_dir)

    assert "claude" in config.tools
    assert config.tools["claude"].yolo_flags == ["--dangerously-skip-permissions"]
    assert config.tools["claude"].mounts == [".claude", ".claude.json"]
    assert config.tools["claude"].env == {"ANTHROPIC_API_KEY": True}
    assert "aider" in config.tools
    assert config.tools["aider"].yolo_flags == ["--yes"]
    assert config.tools["aider"].mounts == [".aider"]


def test_tools_field_level_merge() -> None:
    """Test that project config merges tool fields, not replaces entire tool."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        # Simulate global config with full tool definition
        global_config = Path(tmpdir) / "global.toml"
        global_config.write_text("""
[tools.claude]
yolo_flags = ["--dangerously-skip-permissions"]
mounts = [".claude", ".claude.json"]
env = { ANTHROPIC_API_KEY = true }
""")
        # Project config only overrides yolo_flags
        project_config = project_dir / ".yaas.toml"
        project_config.write_text("""
[tools.claude]
yolo_flags = []
""")
        config = Config()
        from yaas.config import _merge_toml

        _merge_toml(config, global_config)
        _merge_toml(config, project_config)

    assert config.tools["claude"].yolo_flags == []
    assert config.tools["claude"].mounts == [".claude", ".claude.json"]
    assert config.tools["claude"].env == {"ANTHROPIC_API_KEY": True}


def test_tools_invalid_yolo_flags_skipped() -> None:
    """Test that tools with invalid yolo_flags are skipped."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
[tools.bad]
yolo_flags = "not-a-list"

[tools.good]
yolo_flags = ["--yes"]
""")
        config = load_config(project_dir)

    assert "bad" not in config.tools
    assert "good" in config.tools


def test_tools_invalid_type_skipped() -> None:
    """Test that non-table tool entries are skipped."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
[tools]
bad = "not-a-table"

[tools.good]
yolo_flags = ["--yes"]
""")
        config = load_config(project_dir)

    assert "bad" not in config.tools
    assert "good" in config.tools


def test_load_tool_commands_fallback_on_error() -> None:
    """Test that load_tool_commands returns empty dict on error."""
    with patch("yaas.config.load_config", side_effect=Exception("boom")):
        tools = load_tool_commands()

    assert tools == {}


def test_tool_with_empty_fields() -> None:
    """Test that a tool with no fields is valid and uses defaults."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
[tools.mytool]
""")
        config = load_config(project_dir)

    assert "mytool" in config.tools
    assert config.tools["mytool"].command == []
    assert config.tools["mytool"].yolo_flags == []
    assert config.tools["mytool"].mounts == []
    assert config.tools["mytool"].env == {}


def test_tool_command_field() -> None:
    """Test that command field is parsed from config."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
[tools.cc]
command = ["npx", "claude-code"]
yolo_flags = ["--dangerously-skip-permissions"]
mounts = [".claude"]
""")
        config = load_config(project_dir)

    assert config.tools["cc"].command == ["npx", "claude-code"]
    assert config.tools["cc"].yolo_flags == ["--dangerously-skip-permissions"]
    assert config.tools["cc"].mounts == [".claude"]


def test_tool_command_invalid_skipped() -> None:
    """Test that tool with invalid command field is skipped."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
[tools.bad]
command = "not-a-list"

[tools.good]
command = ["claude"]
""")
        config = load_config(project_dir)

    assert "bad" not in config.tools
    assert "good" in config.tools


def test_tool_command_field_level_merge() -> None:
    """Test that command field participates in field-level merge."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        global_config = Path(tmpdir) / "global.toml"
        global_config.write_text("""
[tools.cc]
command = ["npx", "claude-code"]
yolo_flags = ["--dangerously-skip-permissions"]
mounts = [".claude"]
""")
        project_config = project_dir / ".yaas.toml"
        project_config.write_text("""
[tools.cc]
command = ["claude"]
""")
        config = Config()
        from yaas.config import _merge_toml

        _merge_toml(config, global_config)
        _merge_toml(config, project_config)

    assert config.tools["cc"].command == ["claude"]
    # Other fields preserved from global
    assert config.tools["cc"].yolo_flags == ["--dangerously-skip-permissions"]
    assert config.tools["cc"].mounts == [".claude"]


def test_tool_env_passthrough_and_hardcoded() -> None:
    """Test that tool env supports both pass-through (true) and hardcoded (string) values."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
[tools.claude]
mounts = [".claude"]

[tools.claude.env]
ANTHROPIC_API_KEY = true
CUSTOM_VAR = "hello"
""")
        config = load_config(project_dir)

    assert config.tools["claude"].env == {"ANTHROPIC_API_KEY": True, "CUSTOM_VAR": "hello"}


def test_tool_env_invalid_value_skips_tool() -> None:
    """Test that tool with invalid env value type is skipped."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
[tools.bad]
env = { KEY = 42 }

[tools.good]
env = { KEY = true }
""")
        config = load_config(project_dir)

    assert "bad" not in config.tools
    assert "good" in config.tools
    assert config.tools["good"].env == {"KEY": True}


def test_global_env_bool_and_string() -> None:
    """Test that global env supports both bool and string values."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
[env]
GITHUB_TOKEN = true
CUSTOM = "value"
""")
        config = load_config(project_dir)

    assert config.env == {"GITHUB_TOKEN": True, "CUSTOM": "value"}


def test_tool_env_field_level_merge() -> None:
    """Test that env field participates in field-level merge (replaced, not deep-merged)."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        global_config = Path(tmpdir) / "global.toml"
        global_config.write_text("""
[tools.claude]
mounts = [".claude"]
env = { ANTHROPIC_API_KEY = true }
""")
        project_config = project_dir / ".yaas.toml"
        project_config.write_text("""
[tools.claude.env]
CUSTOM_KEY = "override"
""")
        config = Config()
        from yaas.config import _merge_toml

        _merge_toml(config, global_config)
        _merge_toml(config, project_config)

    # env is replaced entirely by project config
    assert config.tools["claude"].env == {"CUSTOM_KEY": "override"}
    # mounts preserved from global
    assert config.tools["claude"].mounts == [".claude"]
