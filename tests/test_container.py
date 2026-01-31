"""Tests for container spec building."""

import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from yaas.config import Config
from yaas.constants import RUNTIME_IMAGE
from yaas.container import _parse_mount_spec, build_container_spec


class TestBuildContainerSpec:
    """Tests for build_container_spec function."""

    def test_basic(self, mock_linux, project_dir, clean_env) -> None:
        """Test basic container spec building."""
        clean_env["TERM"] = "xterm-256color"
        config = Config()

        spec = build_container_spec(config, project_dir, ["bash"])

        assert spec.image == RUNTIME_IMAGE
        assert spec.command == ["bash"]
        assert spec.working_dir == str(project_dir)
        assert spec.tty is True
        assert spec.stdin_open is True

    def test_environment_variables(self, mock_linux, project_dir) -> None:
        """Test environment variables in container spec."""
        config = Config()
        env = {
            "USER": "testuser",
            "TERM": "xterm-256color",
            "ANTHROPIC_API_KEY": "test-key-123",
        }
        with patch.dict(os.environ, env):
            spec = build_container_spec(config, project_dir, ["bash"])

        assert spec.environment["TERM"] == "xterm-256color"
        assert spec.environment["ANTHROPIC_API_KEY"] == "test-key-123"
        assert spec.environment["YAAS"] == "1"

    def test_network_isolation(self, mock_linux, project_dir, clean_env) -> None:
        """Test network isolation setting."""
        config = Config()
        config.no_network = True

        spec = build_container_spec(config, project_dir, ["bash"])

        assert spec.network_mode == "none"

    def test_resource_limits(self, mock_linux, project_dir, clean_env) -> None:
        """Test resource limits are passed through."""
        config = Config()
        config.resources.memory = "16g"
        config.resources.cpus = 4.0
        config.resources.pids_limit = 500

        spec = build_container_spec(config, project_dir, ["bash"])

        assert spec.memory == "16g"
        assert spec.cpus == 4.0
        assert spec.pids_limit == 500

    def test_macos_no_passwd_mount(self, mock_macos, project_dir, clean_env) -> None:
        """Test that /etc/passwd is not mounted on non-Linux (macOS)."""
        config = Config()

        spec = build_container_spec(config, project_dir, ["bash"])

        mount_sources = [m.source for m in spec.mounts]
        assert "/etc/passwd" not in mount_sources
        assert "/etc/group" not in mount_sources


class TestParseMountSpec:
    """Tests for _parse_mount_spec function."""

    def test_simple(self) -> None:
        """Test parsing simple mount spec."""
        mount = _parse_mount_spec("/host/path:/container/path", Path("/project"))

        assert mount.source == "/host/path"
        assert mount.target == "/container/path"
        assert mount.read_only is False

    def test_readonly(self) -> None:
        """Test parsing mount spec with readonly flag."""
        mount = _parse_mount_spec("/host:/container:ro", Path("/project"))

        assert mount.source == "/host"
        assert mount.target == "/container"
        assert mount.read_only is True

    def test_relative_path(self) -> None:
        """Test parsing mount spec with relative path."""
        mount = _parse_mount_spec("./data:/data", Path("/project"))

        assert mount.source == "/project/data"
        assert mount.target == "/data"

    def test_home_expansion(self) -> None:
        """Test parsing mount spec with home directory expansion."""
        mount = _parse_mount_spec("~/data:/data", Path("/project"))

        assert mount.source.startswith(str(Path.home()))
        assert mount.target == "/data"


class TestClipboardSupport:
    """Tests for clipboard support functionality.

    Note: These tests use mock_linux/mock_macos fixtures to mock platform detection,
    so they run on any CI platform regardless of the host OS.
    """

    def test_wayland_support(self, mock_linux, project_dir) -> None:
        """Test clipboard support with Wayland display."""
        config = Config()
        config.clipboard = True

        # Create wayland socket in temp directory
        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir()
        wayland_socket = runtime_dir / "wayland-0"
        wayland_socket.touch()

        env = {
            "USER": "testuser",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_RUNTIME_DIR": str(runtime_dir),
        }
        with patch.dict(os.environ, env, clear=True):
            spec = build_container_spec(config, project_dir, ["bash"])

        # Check environment variables are forwarded
        assert spec.environment.get("WAYLAND_DISPLAY") == "wayland-0"
        assert spec.environment.get("XDG_RUNTIME_DIR") == str(runtime_dir)

        # Check wayland socket is mounted
        mount_targets = [m.target for m in spec.mounts]
        assert str(wayland_socket) in mount_targets

    def test_x11_fallback(self, mock_linux, project_dir) -> None:
        """Test clipboard support with X11 display (fallback when no Wayland)."""
        config = Config()
        config.clipboard = True

        x11_socket = project_dir / ".X11-unix"
        x11_socket.mkdir()

        env = {"USER": "testuser", "DISPLAY": ":0"}

        # Mock the X11 socket path check
        real_path = Path

        def mock_path_side_effect(arg: str) -> Path:
            return x11_socket if arg == "/tmp/.X11-unix" else real_path(arg)

        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env, clear=True))
            mock_path = stack.enter_context(patch("yaas.container.Path"))
            mock_path.side_effect = mock_path_side_effect
            mock_path.home = real_path.home
            spec = build_container_spec(config, project_dir, ["bash"])

        assert spec.environment.get("DISPLAY") == ":0"

    def test_no_display_available(self, mock_linux, project_dir, clean_env) -> None:
        """Test clipboard warning when no display server is detected."""
        config = Config()
        config.clipboard = True

        spec = build_container_spec(config, project_dir, ["bash"])

        assert "WAYLAND_DISPLAY" not in spec.environment
        assert "DISPLAY" not in spec.environment

    def test_disabled_no_mounts(self, mock_linux, project_dir) -> None:
        """Test that display mounts are not added when clipboard is disabled."""
        config = Config()
        config.clipboard = False

        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir()

        env = {
            "USER": "testuser",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_RUNTIME_DIR": str(runtime_dir),
            "DISPLAY": ":0",
        }
        with patch.dict(os.environ, env):
            spec = build_container_spec(config, project_dir, ["bash"])

        # Display env vars should NOT be forwarded when clipboard is disabled
        assert "WAYLAND_DISPLAY" not in spec.environment
        assert "DISPLAY" not in spec.environment

        # Runtime dir should NOT be mounted
        mount_targets = [m.target for m in spec.mounts]
        assert str(runtime_dir) not in mount_targets

    def test_non_linux_silently_skipped(self, mock_macos, project_dir) -> None:
        """Test that clipboard is silently skipped on non-Linux."""
        config = Config()
        config.clipboard = True

        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir()

        env = {
            "USER": "testuser",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_RUNTIME_DIR": str(runtime_dir),
            "DISPLAY": ":0",
        }
        with patch.dict(os.environ, env):
            spec = build_container_spec(config, project_dir, ["bash"])

        # Display env vars should NOT be forwarded on non-Linux
        assert "WAYLAND_DISPLAY" not in spec.environment
        assert "DISPLAY" not in spec.environment

        # No display sockets should be mounted
        mount_targets = [m.target for m in spec.mounts]
        assert str(runtime_dir) not in mount_targets
