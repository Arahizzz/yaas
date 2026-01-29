"""Tests for container spec building."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from yaas.config import Config
from yaas.constants import DEFAULT_IMAGE
from yaas.container import build_container_spec, _parse_mount_spec
from yaas.runtime import Mount


def test_build_container_spec_basic() -> None:
    """Test basic container spec building."""
    config = Config()
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        with patch.dict(os.environ, {"USER": "testuser", "TERM": "xterm-256color"}):
            spec = build_container_spec(config, project_dir, ["bash"])

    assert spec.image == DEFAULT_IMAGE
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
    assert spec.environment["YAAS"] == "1"


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


def test_clipboard_wayland_support() -> None:
    """Test clipboard support with Wayland display."""
    config = Config()
    config.clipboard = True

    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        runtime_dir = Path(tmpdir) / "runtime"
        runtime_dir.mkdir()
        # Create wayland socket file
        wayland_socket = runtime_dir / "wayland-0"
        wayland_socket.touch()

        with patch.dict(
            os.environ,
            {
                "USER": "testuser",
                "WAYLAND_DISPLAY": "wayland-0",
                "XDG_RUNTIME_DIR": str(runtime_dir),
            },
            clear=True,
        ):
            spec = build_container_spec(config, project_dir, ["bash"])

    # Check environment variables are forwarded
    assert spec.environment.get("WAYLAND_DISPLAY") == "wayland-0"
    assert spec.environment.get("XDG_RUNTIME_DIR") == str(runtime_dir)

    # Check wayland socket is mounted (not whole runtime dir, to leave room for GPG)
    mount_targets = [m.target for m in spec.mounts]
    assert str(wayland_socket) in mount_targets


def test_clipboard_x11_support() -> None:
    """Test clipboard support with X11 display (fallback)."""
    config = Config()
    config.clipboard = True

    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        x11_socket = Path(tmpdir) / ".X11-unix"
        x11_socket.mkdir()

        with patch.dict(
            os.environ,
            {
                "USER": "testuser",
                "DISPLAY": ":0",
            },
            clear=True,
        ):
            # Mock the X11 socket path
            with patch("yaas.container.Path") as mock_path:
                # Set up mock to return the temp X11 socket for the specific check
                real_path = Path

                def path_side_effect(arg):
                    if arg == "/tmp/.X11-unix":
                        return x11_socket
                    return real_path(arg)

                mock_path.side_effect = path_side_effect
                mock_path.home = real_path.home

                spec = build_container_spec(config, project_dir, ["bash"])

    # Check DISPLAY environment variable is forwarded
    assert spec.environment.get("DISPLAY") == ":0"


def test_clipboard_no_display_available(capsys) -> None:
    """Test clipboard warning when no display server is detected."""
    config = Config()
    config.clipboard = True

    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        # No display env vars set
        with patch.dict(os.environ, {"USER": "testuser"}, clear=True):
            spec = build_container_spec(config, project_dir, ["bash"])

    # No display env vars should be set
    assert "WAYLAND_DISPLAY" not in spec.environment
    assert "DISPLAY" not in spec.environment


def test_clipboard_disabled_no_display_mounts() -> None:
    """Test that display mounts are not added when clipboard is disabled."""
    config = Config()
    config.clipboard = False

    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        runtime_dir = Path(tmpdir) / "runtime"
        runtime_dir.mkdir()

        with patch.dict(
            os.environ,
            {
                "USER": "testuser",
                "WAYLAND_DISPLAY": "wayland-0",
                "XDG_RUNTIME_DIR": str(runtime_dir),
                "DISPLAY": ":0",
            },
        ):
            spec = build_container_spec(config, project_dir, ["bash"])

    # Display env vars should NOT be forwarded when clipboard is disabled
    assert "WAYLAND_DISPLAY" not in spec.environment
    assert "DISPLAY" not in spec.environment

    # Runtime dir should NOT be mounted
    mount_targets = [m.target for m in spec.mounts]
    assert str(runtime_dir) not in mount_targets
