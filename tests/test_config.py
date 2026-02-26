"""Tests for configuration loading."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from yaas.config import (
    Config,
    ContainerSettings,
    ResourceLimits,
    ToolConfig,
    load_config,
    load_tool_commands,
    resolve_effective_config,
)


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


def test_container_settings_defaults() -> None:
    """Test that ContainerSettings defaults are all None/empty (inherit semantics)."""
    settings = ContainerSettings()
    assert settings.ssh_agent is None
    assert settings.git_config is None
    assert settings.container_socket is None
    assert settings.clipboard is None
    assert settings.network_mode is None
    assert settings.readonly_project is None
    assert settings.pid_mode is None
    assert settings.resources is None
    assert settings.mounts == []
    assert settings.env == {}


def test_inheritance_hierarchy() -> None:
    """Test that Config and ToolConfig inherit from ContainerSettings."""
    assert issubclass(Config, ContainerSettings)
    assert issubclass(ToolConfig, ContainerSettings)

    # ToolConfig inherits None defaults
    tc = ToolConfig()
    assert tc.ssh_agent is None
    assert tc.network_mode is None

    # Config overrides with concrete defaults
    cfg = Config()
    assert cfg.ssh_agent is False
    assert cfg.network_mode == "bridge"


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


# --- resolve_effective_config tests ---


def test_resolve_no_active_tool() -> None:
    """Test that resolve_effective_config returns config unchanged when no active tool."""
    config = Config(ssh_agent=False, network_mode="bridge")
    result = resolve_effective_config(config)
    assert result is config  # same object, not a copy


def test_resolve_unknown_active_tool() -> None:
    """Test that resolve_effective_config returns config unchanged for unknown tool."""
    config = Config(active_tool="nonexistent")
    result = resolve_effective_config(config)
    assert result is config


def test_resolve_bool_overrides() -> None:
    """Test that tool bool overrides replace global values."""
    config = Config(
        ssh_agent=False,
        git_config=False,
        clipboard=False,
        active_tool="claude",
        tools={"claude": ToolConfig(ssh_agent=True, git_config=True)},
    )
    result = resolve_effective_config(config)

    assert result.ssh_agent is True
    assert result.git_config is True
    # Not overridden — inherits global
    assert result.clipboard is False


def test_resolve_network_mode_override() -> None:
    """Test that tool network_mode overrides global value."""
    config = Config(
        network_mode="bridge",
        active_tool="claude",
        tools={"claude": ToolConfig(network_mode="none")},
    )
    result = resolve_effective_config(config)
    assert result.network_mode == "none"


def test_resolve_readonly_project_override() -> None:
    """Test that tool readonly_project overrides global value."""
    config = Config(
        readonly_project=False,
        active_tool="claude",
        tools={"claude": ToolConfig(readonly_project=True)},
    )
    result = resolve_effective_config(config)
    assert result.readonly_project is True


def test_resolve_pid_mode_override() -> None:
    """Test that tool pid_mode overrides global value."""
    config = Config(
        pid_mode=None,
        active_tool="claude",
        tools={"claude": ToolConfig(pid_mode="host")},
    )
    result = resolve_effective_config(config)
    assert result.pid_mode == "host"


def test_resolve_resources_partial_override() -> None:
    """Test that tool resources override only specified fields."""
    config = Config(
        resources=ResourceLimits(memory="8g", cpus=2.0, pids_limit=1000),
        active_tool="claude",
        tools={"claude": ToolConfig(resources=ResourceLimits(memory="16g"))},
    )
    result = resolve_effective_config(config)

    assert result.resources.memory == "16g"  # overridden
    assert result.resources.cpus == 2.0  # inherited
    assert result.resources.pids_limit == 1000  # inherited


def test_resolve_env_overlay() -> None:
    """Test that tool env overlays global env (tool wins on conflict)."""
    config = Config(
        env={"GITHUB_TOKEN": True, "SHARED": "global"},
        active_tool="claude",
        tools={"claude": ToolConfig(env={"ANTHROPIC_API_KEY": True, "SHARED": "tool"})},
    )
    result = resolve_effective_config(config)

    assert result.env == {
        "GITHUB_TOKEN": True,
        "ANTHROPIC_API_KEY": True,
        "SHARED": "tool",
    }


def test_resolve_env_empty_tool_env() -> None:
    """Test that empty tool env preserves global env."""
    config = Config(
        env={"GITHUB_TOKEN": True},
        active_tool="claude",
        tools={"claude": ToolConfig()},
    )
    result = resolve_effective_config(config)
    assert result.env == {"GITHUB_TOKEN": True}


def test_resolve_none_fields_skipped() -> None:
    """Test that None override fields are skipped (inherit global)."""
    config = Config(
        ssh_agent=True,
        network_mode="host",
        active_tool="claude",
        tools={"claude": ToolConfig()},  # all overrides are None
    )
    result = resolve_effective_config(config)

    assert result.ssh_agent is True
    assert result.network_mode == "host"


def test_resolve_does_not_mutate_original() -> None:
    """Test that resolve_effective_config returns a new Config without mutating the original."""
    config = Config(
        ssh_agent=False,
        env={"GLOBAL": "yes"},
        resources=ResourceLimits(memory="8g"),
        active_tool="claude",
        tools={"claude": ToolConfig(
            ssh_agent=True,
            env={"TOOL": "yes"},
            resources=ResourceLimits(memory="16g"),
        )},
    )
    result = resolve_effective_config(config)

    # Original unchanged
    assert config.ssh_agent is False
    assert config.env == {"GLOBAL": "yes"}
    assert config.resources.memory == "8g"

    # Resolved has overrides
    assert result.ssh_agent is True
    assert result.env == {"GLOBAL": "yes", "TOOL": "yes"}
    assert result.resources.memory == "16g"


# --- Tool override TOML parsing tests ---


def test_tool_override_bool_fields_from_toml() -> None:
    """Test that tool bool override fields are parsed from TOML."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
[tools.claude]
yolo_flags = ["--dangerously-skip-permissions"]
ssh_agent = true
git_config = true
container_socket = false
clipboard = true
readonly_project = true
""")
        config = load_config(project_dir)

    tc = config.tools["claude"]
    assert tc.ssh_agent is True
    assert tc.git_config is True
    assert tc.container_socket is False
    assert tc.clipboard is True
    assert tc.readonly_project is True


def test_tool_override_string_fields_from_toml() -> None:
    """Test that tool string override fields are parsed from TOML."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
[tools.claude]
network_mode = "none"
pid_mode = "host"
""")
        config = load_config(project_dir)

    tc = config.tools["claude"]
    assert tc.network_mode == "none"
    assert tc.pid_mode == "host"


def test_tool_override_resources_from_toml() -> None:
    """Test that tool resource overrides are parsed from TOML."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
[tools.claude]
mounts = [".claude"]

[tools.claude.resources]
memory = "16g"
cpus = 4.0
""")
        config = load_config(project_dir)

    tc = config.tools["claude"]
    assert tc.resources is not None
    assert tc.resources.memory == "16g"
    assert tc.resources.cpus == 4.0
    assert tc.resources.pids_limit is None  # not overridden


def test_tool_override_field_level_merge() -> None:
    """Test that tool override fields participate in field-level merge."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        global_config = Path(tmpdir) / "global.toml"
        global_config.write_text("""
[tools.claude]
yolo_flags = ["--dangerously-skip-permissions"]
mounts = [".claude"]
ssh_agent = true
network_mode = "none"
""")
        project_config = project_dir / ".yaas.toml"
        project_config.write_text("""
[tools.claude]
network_mode = "bridge"
""")
        config = Config()
        from yaas.config import _merge_toml

        _merge_toml(config, global_config)
        _merge_toml(config, project_config)

    tc = config.tools["claude"]
    # Overridden by project
    assert tc.network_mode == "bridge"
    # Preserved from global
    assert tc.ssh_agent is True
    assert tc.mounts == [".claude"]
    assert tc.yolo_flags == ["--dangerously-skip-permissions"]


def test_tool_override_backward_compatible() -> None:
    """Test that old-format TOML (only command/yolo_flags/mounts/env) still works."""
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        config_file = project_dir / ".yaas.toml"
        config_file.write_text("""
[tools.claude]
command = ["npx", "claude-code"]
yolo_flags = ["--dangerously-skip-permissions"]
mounts = [".claude", ".claude.json"]
env = { ANTHROPIC_API_KEY = true }
""")
        config = load_config(project_dir)

    tc = config.tools["claude"]
    assert tc.command == ["npx", "claude-code"]
    assert tc.yolo_flags == ["--dangerously-skip-permissions"]
    assert tc.mounts == [".claude", ".claude.json"]
    assert tc.env == {"ANTHROPIC_API_KEY": True}
    # All overrides should be None (not set)
    assert tc.ssh_agent is None
    assert tc.git_config is None
    assert tc.network_mode is None
    assert tc.resources is None
