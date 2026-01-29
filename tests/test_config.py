"""Tests for configuration loading."""

from pathlib import Path
from tempfile import TemporaryDirectory

from yaas.config import Config, ResourceLimits, load_config


def test_default_config() -> None:
    """Test that Config dataclass defaults are set correctly."""
    config = Config()

    assert config.runtime is None
    assert config.ssh_agent is False
    assert config.git_config is False
    assert config.ai_config is False
    assert config.container_socket is False
    assert config.no_network is False
    assert config.readonly_project is False


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
no_network = true

[resources]
memory = "16g"
cpus = 4.0
""")
        config = load_config(project_dir)

    assert config.ssh_agent is True
    assert config.no_network is True
    assert config.resources.memory == "16g"
    assert config.resources.cpus == 4.0
