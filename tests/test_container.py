"""Tests for container spec building."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent_wrap.config import Config
from agent_wrap.container import build_container_spec, _parse_mount_spec
from agent_wrap.runtime import Mount


def test_build_container_spec_basic() -> None:
    """Test basic container spec building."""
    config = Config()
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        with patch.dict(os.environ, {"USER": "testuser", "TERM": "xterm-256color"}):
            spec = build_container_spec(config, project_dir, ["bash"])

    assert spec.image == config.image
    assert spec.command == ["bash"]
    assert spec.working_dir == str(project_dir)
    assert spec.tty is True
    assert spec.stdin_open is True


def test_build_container_spec_environment() -> None:
    """Test environment variables in container spec."""
    config = Config()
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        with patch.dict(
            os.environ,
            {
                "USER": "testuser",
                "TERM": "xterm-256color",
                "ANTHROPIC_API_KEY": "test-key-123",
            },
        ):
            spec = build_container_spec(config, project_dir, ["bash"])

    assert spec.environment["TERM"] == "xterm-256color"
    assert spec.environment["ANTHROPIC_API_KEY"] == "test-key-123"
    assert spec.environment["AGENT_WRAP"] == "1"


def test_build_container_spec_network_isolation() -> None:
    """Test network isolation setting."""
    config = Config()
    config.no_network = True

    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        with patch.dict(os.environ, {"USER": "testuser"}):
            spec = build_container_spec(config, project_dir, ["bash"])

    assert spec.network_mode == "none"


def test_build_container_spec_resource_limits() -> None:
    """Test resource limits are passed through."""
    config = Config()
    config.resources.memory = "16g"
    config.resources.cpus = 4.0
    config.resources.pids_limit = 500

    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        with patch.dict(os.environ, {"USER": "testuser"}):
            spec = build_container_spec(config, project_dir, ["bash"])

    assert spec.memory == "16g"
    assert spec.cpus == 4.0
    assert spec.pids_limit == 500


def test_parse_mount_spec_simple() -> None:
    """Test parsing simple mount spec."""
    mount = _parse_mount_spec("/host/path:/container/path", Path("/project"))

    assert mount.source == "/host/path"
    assert mount.target == "/container/path"
    assert mount.read_only is False


def test_parse_mount_spec_readonly() -> None:
    """Test parsing mount spec with readonly flag."""
    mount = _parse_mount_spec("/host:/container:ro", Path("/project"))

    assert mount.source == "/host"
    assert mount.target == "/container"
    assert mount.read_only is True


def test_parse_mount_spec_relative() -> None:
    """Test parsing mount spec with relative path."""
    mount = _parse_mount_spec("./data:/data", Path("/project"))

    assert mount.source == "/project/data"
    assert mount.target == "/data"


def test_parse_mount_spec_home_expansion() -> None:
    """Test parsing mount spec with home directory expansion."""
    mount = _parse_mount_spec("~/data:/data", Path("/project"))

    assert mount.source.startswith(str(Path.home()))
    assert mount.target == "/data"
