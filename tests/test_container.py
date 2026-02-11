"""Tests for container spec building."""

import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from yaas.cli import _apply_cli_overrides
from yaas.config import Config
from yaas.constants import CLONE_WORKSPACE, RUNTIME_IMAGE
from yaas.container import (
    _parse_mount_spec,
    build_clone_spec,
    build_clone_work_spec,
    build_container_spec,
    extract_repo_name,
)


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
            "COLORTERM": "truecolor",
            "ANTHROPIC_API_KEY": "test-key-123",
        }
        with patch.dict(os.environ, env):
            spec = build_container_spec(config, project_dir, ["bash"])

        assert spec.environment["TERM"] == "xterm-256color"
        assert spec.environment["ANTHROPIC_API_KEY"] == "test-key-123"
        assert spec.environment["YAAS"] == "1"
        assert spec.environment["COLORTERM"] == "truecolor"

    def test_network_isolation(self, mock_linux, project_dir, clean_env) -> None:
        """Test network isolation setting."""
        config = Config()
        config.network_mode = "none"

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

        # Check wayland socket is mounted read-only
        wayland_mount = next(
            (m for m in spec.mounts if m.target == str(wayland_socket)), None
        )
        assert wayland_mount is not None
        assert wayland_mount.read_only is True

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


class TestDisplaySupport:
    """Tests for full display passthrough (read-write sockets)."""

    def test_wayland_read_write(self, mock_linux, project_dir) -> None:
        """Test display mounts Wayland socket read-write."""
        config = Config()
        config.display = True

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

        # Check wayland socket is mounted read-write
        wayland_mount = next(
            (m for m in spec.mounts if m.target == str(wayland_socket)), None
        )
        assert wayland_mount is not None
        assert wayland_mount.read_only is False

        # Check environment variables are forwarded
        assert spec.environment.get("WAYLAND_DISPLAY") == "wayland-0"
        assert spec.environment.get("XDG_RUNTIME_DIR") == str(runtime_dir)

    def test_x11_read_write(self, mock_linux, project_dir) -> None:
        """Test display mounts X11 socket read-write."""
        config = Config()
        config.display = True

        x11_socket = project_dir / ".X11-unix"
        x11_socket.mkdir()

        env = {"USER": "testuser", "DISPLAY": ":0"}
        real_path = Path

        def mock_path_side_effect(arg: str) -> Path:
            return x11_socket if arg == "/tmp/.X11-unix" else real_path(arg)

        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env, clear=True))
            mock_path = stack.enter_context(patch("yaas.container.Path"))
            mock_path.side_effect = mock_path_side_effect
            mock_path.home = real_path.home
            spec = build_container_spec(config, project_dir, ["bash"])

        # X11 socket should be mounted read-write
        x11_mount = next(
            (m for m in spec.mounts if m.target == str(x11_socket)), None
        )
        assert x11_mount is not None
        assert x11_mount.read_only is False

    def test_display_supersedes_clipboard(self, mock_linux, project_dir) -> None:
        """Test that display supersedes clipboard (read-write wins)."""
        config = Config()
        config.display = True
        config.clipboard = True

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

        # Should only have one wayland mount (from display, not clipboard)
        wayland_mounts = [m for m in spec.mounts if m.target == str(wayland_socket)]
        assert len(wayland_mounts) == 1
        assert wayland_mounts[0].read_only is False  # read-write from display

    def test_non_linux_skipped(self, mock_macos, project_dir) -> None:
        """Test that display is skipped on non-Linux."""
        config = Config()
        config.display = True

        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir()

        env = {
            "USER": "testuser",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_RUNTIME_DIR": str(runtime_dir),
        }
        with patch.dict(os.environ, env):
            spec = build_container_spec(config, project_dir, ["bash"])

        assert "WAYLAND_DISPLAY" not in spec.environment


class TestDbusSupport:
    """Tests for D-Bus session bus support."""

    def test_dbus_socket_mounted(self, mock_linux, project_dir) -> None:
        """Test D-Bus socket is mounted when available."""
        config = Config()
        config.dbus = True

        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir()
        bus_socket = runtime_dir / "bus"
        bus_socket.touch()

        env = {
            "USER": "testuser",
            "XDG_RUNTIME_DIR": str(runtime_dir),
        }
        with patch.dict(os.environ, env, clear=True):
            spec = build_container_spec(config, project_dir, ["bash"])

        # Check bus socket is mounted
        bus_mount = next(
            (m for m in spec.mounts if m.target == str(bus_socket)), None
        )
        assert bus_mount is not None
        assert bus_mount.read_only is False

        # Check environment
        assert spec.environment.get("DBUS_SESSION_BUS_ADDRESS") == f"unix:path={runtime_dir}/bus"
        assert spec.environment.get("XDG_RUNTIME_DIR") == str(runtime_dir)

    def test_no_socket_warning(self, mock_linux, project_dir) -> None:
        """Test warning when D-Bus socket doesn't exist."""
        config = Config()
        config.dbus = True

        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir()
        # No bus socket created

        env = {
            "USER": "testuser",
            "XDG_RUNTIME_DIR": str(runtime_dir),
        }
        with patch.dict(os.environ, env, clear=True):
            spec = build_container_spec(config, project_dir, ["bash"])

        # No bus mount should exist
        mount_targets = [m.target for m in spec.mounts]
        assert str(runtime_dir / "bus") not in mount_targets

    def test_disabled_skip(self, mock_linux, project_dir) -> None:
        """Test D-Bus is not mounted when disabled."""
        config = Config()
        config.dbus = False

        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir()
        bus_socket = runtime_dir / "bus"
        bus_socket.touch()

        env = {
            "USER": "testuser",
            "XDG_RUNTIME_DIR": str(runtime_dir),
        }
        with patch.dict(os.environ, env, clear=True):
            spec = build_container_spec(config, project_dir, ["bash"])

        assert "DBUS_SESSION_BUS_ADDRESS" not in spec.environment

    def test_non_linux_skipped(self, mock_macos, project_dir) -> None:
        """Test D-Bus is skipped on non-Linux."""
        config = Config()
        config.dbus = True

        env = {
            "USER": "testuser",
            "XDG_RUNTIME_DIR": "/run/user/1000",
        }
        with patch.dict(os.environ, env):
            spec = build_container_spec(config, project_dir, ["bash"])

        assert "DBUS_SESSION_BUS_ADDRESS" not in spec.environment


class TestGpuSupport:
    """Tests for GPU device passthrough."""

    def test_device_added(self, mock_linux, project_dir, clean_env) -> None:
        """Test GPU device is added when /dev/dri exists."""
        config = Config()
        config.gpu = True

        dri_path = project_dir / "dri"
        dri_path.mkdir()
        render_node = dri_path / "renderD128"
        render_node.touch()

        real_path = Path

        def mock_path_side_effect(arg: str) -> Path:
            if arg == "/dev/dri":
                return dri_path
            if arg == "/dev/dri/renderD128":
                return render_node
            return real_path(arg)

        with patch("yaas.container.Path") as mock_path:
            mock_path.side_effect = mock_path_side_effect
            mock_path.home = real_path.home
            spec = build_container_spec(config, project_dir, ["bash"])

        assert spec.devices is not None
        assert "/dev/dri" in spec.devices

    def test_no_device_warning(self, mock_linux, project_dir, clean_env) -> None:
        """Test warning when /dev/dri doesn't exist."""
        config = Config()
        config.gpu = True

        # Don't create /dev/dri — it won't exist in temp dir
        spec = build_container_spec(config, project_dir, ["bash"])

        assert spec.devices is None

    def test_render_group_detected(self, mock_linux, project_dir, clean_env) -> None:
        """Test render node GID is added to groups."""
        config = Config()
        config.gpu = True

        dri_path = project_dir / "dri"
        dri_path.mkdir()
        render_node = dri_path / "renderD128"
        render_node.touch()

        real_path = Path

        def mock_path_side_effect(arg: str) -> Path:
            if arg == "/dev/dri":
                return dri_path
            if arg == "/dev/dri/renderD128":
                return render_node
            return real_path(arg)

        with patch("yaas.container.Path") as mock_path:
            mock_path.side_effect = mock_path_side_effect
            mock_path.home = real_path.home
            spec = build_container_spec(config, project_dir, ["bash"])

        # Groups should contain the render node's GID
        assert spec.groups is not None
        render_gid = render_node.stat().st_gid
        assert render_gid in spec.groups

    def test_disabled_skip(self, mock_linux, project_dir, clean_env) -> None:
        """Test GPU is not added when disabled."""
        config = Config()
        config.gpu = False

        spec = build_container_spec(config, project_dir, ["bash"])

        assert spec.devices is None

    def test_non_linux_skipped(self, mock_macos, project_dir, clean_env) -> None:
        """Test GPU is skipped on non-Linux."""
        config = Config()
        config.gpu = True

        spec = build_container_spec(config, project_dir, ["bash"])

        assert spec.devices is None


class TestAudioSupport:
    """Tests for audio passthrough (PipeWire/PulseAudio)."""

    def test_pipewire_mount(self, mock_linux, project_dir) -> None:
        """Test PipeWire socket is mounted when available."""
        config = Config()
        config.audio = True

        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir()
        pipewire_socket = runtime_dir / "pipewire-0"
        pipewire_socket.touch()

        env = {
            "USER": "testuser",
            "XDG_RUNTIME_DIR": str(runtime_dir),
        }
        with patch.dict(os.environ, env, clear=True):
            spec = build_container_spec(config, project_dir, ["bash"])

        # Check PipeWire socket is mounted
        pw_mount = next(
            (m for m in spec.mounts if m.target == str(pipewire_socket)), None
        )
        assert pw_mount is not None
        assert pw_mount.read_only is False

    def test_pipewire_with_pulse_compat(self, mock_linux, project_dir) -> None:
        """Test both PipeWire and PulseAudio sockets are mounted on PipeWire systems."""
        config = Config()
        config.audio = True

        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir()
        pipewire_socket = runtime_dir / "pipewire-0"
        pipewire_socket.touch()
        pulse_dir = runtime_dir / "pulse"
        pulse_dir.mkdir()
        pulse_socket = pulse_dir / "native"
        pulse_socket.touch()

        env = {
            "USER": "testuser",
            "XDG_RUNTIME_DIR": str(runtime_dir),
        }
        with patch.dict(os.environ, env, clear=True):
            spec = build_container_spec(config, project_dir, ["bash"])

        # Both sockets should be mounted
        mount_targets = [m.target for m in spec.mounts]
        assert str(pipewire_socket) in mount_targets
        assert str(pulse_socket) in mount_targets

        # PULSE_SERVER should be set for PulseAudio clients
        assert spec.environment.get("PULSE_SERVER") == str(pulse_socket)

    def test_pulseaudio_standalone(self, mock_linux, project_dir) -> None:
        """Test PulseAudio socket is mounted when PipeWire is absent."""
        config = Config()
        config.audio = True

        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir()
        pulse_dir = runtime_dir / "pulse"
        pulse_dir.mkdir()
        pulse_socket = pulse_dir / "native"
        pulse_socket.touch()

        env = {
            "USER": "testuser",
            "XDG_RUNTIME_DIR": str(runtime_dir),
        }
        with patch.dict(os.environ, env, clear=True):
            spec = build_container_spec(config, project_dir, ["bash"])

        # Check PulseAudio socket is mounted
        pulse_mount = next(
            (m for m in spec.mounts if m.target == str(pulse_socket)), None
        )
        assert pulse_mount is not None

        # Check PULSE_SERVER is set
        assert spec.environment.get("PULSE_SERVER") == str(pulse_socket)

    def test_no_socket_warning(self, mock_linux, project_dir) -> None:
        """Test warning when no audio socket exists."""
        config = Config()
        config.audio = True

        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir()

        env = {
            "USER": "testuser",
            "XDG_RUNTIME_DIR": str(runtime_dir),
        }
        with patch.dict(os.environ, env, clear=True):
            spec = build_container_spec(config, project_dir, ["bash"])

        # No audio mount should exist
        mount_targets = [m.target for m in spec.mounts]
        assert str(runtime_dir / "pipewire-0") not in mount_targets
        assert str(runtime_dir / "pulse" / "native") not in mount_targets

    def test_disabled_skip(self, mock_linux, project_dir) -> None:
        """Test audio is not mounted when disabled."""
        config = Config()
        config.audio = False

        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir()
        pipewire_socket = runtime_dir / "pipewire-0"
        pipewire_socket.touch()

        env = {
            "USER": "testuser",
            "XDG_RUNTIME_DIR": str(runtime_dir),
        }
        with patch.dict(os.environ, env, clear=True):
            spec = build_container_spec(config, project_dir, ["bash"])

        mount_targets = [m.target for m in spec.mounts]
        assert str(pipewire_socket) not in mount_targets

    def test_non_linux_skipped(self, mock_macos, project_dir) -> None:
        """Test audio is skipped on non-Linux."""
        config = Config()
        config.audio = True

        env = {
            "USER": "testuser",
            "XDG_RUNTIME_DIR": "/run/user/1000",
        }
        with patch.dict(os.environ, env):
            spec = build_container_spec(config, project_dir, ["bash"])

        assert "PULSE_SERVER" not in spec.environment


class TestExtractRepoName:
    """Tests for extract_repo_name function."""

    def test_https_with_git_suffix(self) -> None:
        """Test extracting repo name from HTTPS URL with .git suffix."""
        name = extract_repo_name("https://github.com/user/repo.git")
        assert name == "repo"

    def test_https_without_git_suffix(self) -> None:
        """Test extracting repo name from HTTPS URL without .git suffix."""
        name = extract_repo_name("https://github.com/user/repo")
        assert name == "repo"

    def test_ssh_format(self) -> None:
        """Test extracting repo name from SSH URL."""
        name = extract_repo_name("git@github.com:user/repo.git")
        assert name == "repo"

    def test_ssh_without_git_suffix(self) -> None:
        """Test extracting repo name from SSH URL without .git suffix."""
        name = extract_repo_name("git@github.com:user/repo")
        assert name == "repo"

    def test_nested_path(self) -> None:
        """Test extracting repo name from nested path URL."""
        name = extract_repo_name("https://gitlab.com/group/subgroup/repo.git")
        assert name == "repo"

    def test_trailing_slash(self) -> None:
        """Test extracting repo name from URL with trailing slash."""
        name = extract_repo_name("https://github.com/user/repo/")
        assert name == "repo"

    def test_query_params_stripped(self) -> None:
        """Test query parameters are stripped from URL."""
        name = extract_repo_name("https://github.com/user/repo?ref=main")
        assert name == "repo"

    def test_fragment_stripped(self) -> None:
        """Test URL fragments are stripped."""
        name = extract_repo_name("https://github.com/user/repo#readme")
        assert name == "repo"

    def test_whitespace_stripped(self) -> None:
        """Test whitespace is stripped from URL."""
        name = extract_repo_name("  https://github.com/user/repo  ")
        assert name == "repo"

    def test_empty_url_raises(self) -> None:
        """Test empty URL raises ValueError."""
        with pytest.raises(ValueError, match="Empty repository URL"):
            extract_repo_name("")

    def test_whitespace_only_raises(self) -> None:
        """Test whitespace-only URL raises ValueError."""
        with pytest.raises(ValueError, match="Empty repository URL"):
            extract_repo_name("   ")


class TestBuildCloneSpec:
    """Tests for build_clone_spec function."""

    def test_basic(self, mock_linux, project_dir, clean_env) -> None:
        """Test basic clone spec building."""
        config = Config()
        clone_url = "https://github.com/user/repo.git"
        clone_volume = "yaas-clone-abc123"
        repo_name = "repo"

        spec = build_clone_spec(config, clone_url, clone_volume, repo_name)

        assert spec.image == RUNTIME_IMAGE
        assert spec.command == [
            "git",
            "clone",
            "--depth",
            "1",
            clone_url,
            f"{CLONE_WORKSPACE}/{repo_name}",
        ]
        assert spec.working_dir == CLONE_WORKSPACE
        assert spec.network_mode is None  # Always needs network
        assert spec.tty is False
        assert spec.stdin_open is False

    def test_clone_volume_mounted(self, mock_linux, project_dir, clean_env) -> None:
        """Test that clone volume is mounted at workspace."""
        config = Config()
        clone_volume = "yaas-clone-abc123"

        spec = build_clone_spec(config, "https://github.com/user/repo.git", clone_volume, "repo")

        # Find the clone volume mount
        volume_mount = next(
            (m for m in spec.mounts if m.source == clone_volume and m.type == "volume"), None
        )
        assert volume_mount is not None
        assert volume_mount.target == CLONE_WORKSPACE

    def test_ssh_agent_forwarded(self, mock_linux, project_dir, clean_env) -> None:
        """Test SSH agent is forwarded for private repos."""
        config = Config()
        config.ssh_agent = True

        # Mock SSH agent socket
        ssh_socket = project_dir / "ssh-agent"
        ssh_socket.touch()

        with patch("yaas.container.get_ssh_agent_socket", return_value=ssh_socket):
            spec = build_clone_spec(
                config, "git@github.com:user/repo.git", "yaas-clone-abc123", "repo"
            )

        assert spec.environment.get("SSH_AUTH_SOCK") == "/ssh-agent"

    def test_ref_adds_branch_flag(self, mock_linux, project_dir, clean_env) -> None:
        """Test that ref parameter adds --branch flag to git clone."""
        config = Config()
        clone_url = "https://github.com/user/repo.git"
        clone_volume = "yaas-clone-abc123"
        repo_name = "repo"

        spec = build_clone_spec(config, clone_url, clone_volume, repo_name, ref="v2.0.0")

        assert spec.command == [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            "v2.0.0",
            clone_url,
            f"{CLONE_WORKSPACE}/{repo_name}",
        ]

    def test_ref_none_no_branch_flag(self, mock_linux, project_dir, clean_env) -> None:
        """Test that ref=None does not add --branch flag."""
        config = Config()
        clone_url = "https://github.com/user/repo.git"
        clone_volume = "yaas-clone-abc123"
        repo_name = "repo"

        spec = build_clone_spec(config, clone_url, clone_volume, repo_name, ref=None)

        assert "--branch" not in spec.command


class TestBuildCloneWorkSpec:
    """Tests for build_clone_work_spec function."""

    def test_working_dir(self, mock_linux, project_dir, clean_env) -> None:
        """Test working directory is set to repo in clone mode."""
        config = Config()
        clone_volume = "yaas-clone-abc123"
        repo_name = "myrepo"

        spec = build_clone_work_spec(config, clone_volume, repo_name, ["bash"])

        assert spec.working_dir == f"{CLONE_WORKSPACE}/{repo_name}"

    def test_clone_volume_mounted(self, mock_linux, project_dir, clean_env) -> None:
        """Test clone volume is mounted at /workspace."""
        config = Config()
        clone_volume = "yaas-clone-abc123"
        repo_name = "myrepo"

        spec = build_clone_work_spec(config, clone_volume, repo_name, ["bash"])

        # Clone volume should be mounted
        volume_mount = next(
            (m for m in spec.mounts if m.source == clone_volume and m.type == "volume"), None
        )
        assert volume_mount is not None
        assert volume_mount.target == CLONE_WORKSPACE

    def test_network_mode_respected(self, mock_linux, project_dir, clean_env) -> None:
        """Test network_mode is respected in clone work container."""
        config = Config()
        config.network_mode = "none"

        spec = build_clone_work_spec(config, "yaas-clone-abc123", "repo", ["bash"])

        assert spec.network_mode == "none"

    def test_ssh_agent_forwarded(self, mock_linux, project_dir, clean_env) -> None:
        """Test SSH agent is forwarded in work container."""
        config = Config()
        config.ssh_agent = True

        ssh_socket = project_dir / "ssh-agent"
        ssh_socket.touch()

        with patch("yaas.container.get_ssh_agent_socket", return_value=ssh_socket):
            spec = build_clone_work_spec(config, "yaas-clone-abc123", "repo", ["bash"])

        assert spec.environment.get("SSH_AUTH_SOCK") == "/ssh-agent"


class TestGuiUmbrella:
    """Tests for --gui umbrella flag."""

    def _no_flags(self) -> dict[str, object]:
        """Return kwargs with all flags disabled."""
        return {
            "ssh_agent": False,
            "git_config": False,
            "ai_config": False,
            "container_socket": False,
            "clipboard": False,
            "display": False,
            "dbus": False,
            "gpu": False,
            "audio": False,
            "gui": False,
            "network": None,
            "memory": None,
            "cpus": None,
        }

    def test_gui_enables_all(self) -> None:
        """Test that --gui sets display, dbus, gpu, audio to True."""
        config = Config()
        flags = self._no_flags()
        flags["gui"] = True
        _apply_cli_overrides(config, **flags)  # type: ignore[arg-type]

        assert config.display is True
        assert config.dbus is True
        assert config.gpu is True
        assert config.audio is True

    def test_gui_does_not_set_clipboard(self) -> None:
        """Test that --gui does not enable clipboard (display supersedes it)."""
        config = Config()
        flags = self._no_flags()
        flags["gui"] = True
        _apply_cli_overrides(config, **flags)  # type: ignore[arg-type]

        assert config.clipboard is False

    def test_individual_flags_work(self) -> None:
        """Test individual flags set their respective config fields."""
        config = Config()
        flags = self._no_flags()
        flags["dbus"] = True
        flags["audio"] = True
        _apply_cli_overrides(config, **flags)  # type: ignore[arg-type]

        assert config.dbus is True
        assert config.audio is True
        assert config.display is False
        assert config.gpu is False


class TestWslgSupport:
    """Tests for WSL2/WSLg GUI integration."""

    def test_wayland_wslg_fallback(self, mock_wsl, project_dir) -> None:
        """Test Wayland socket discovered via WSLg path when XDG_RUNTIME_DIR is unset."""
        config = Config()
        config.display = True

        # Create WSLg runtime dir with wayland socket
        wslg_runtime = project_dir / "wslg-runtime"
        wslg_runtime.mkdir()
        wayland_socket = wslg_runtime / "wayland-0"
        wayland_socket.touch()

        env = {
            "USER": "testuser",
            "WAYLAND_DISPLAY": "wayland-0",
        }
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env, clear=True))
            stack.enter_context(
                patch("yaas.container._WSLG_RUNTIME_DIR", wslg_runtime)
            )
            spec = build_container_spec(config, project_dir, ["bash"])

        # WSLg wayland socket should be mounted read-write
        wayland_mount = next(
            (m for m in spec.mounts if m.target == str(wayland_socket)), None
        )
        assert wayland_mount is not None
        assert wayland_mount.read_only is False

    def test_audio_wslg_fallback(self, mock_wsl, project_dir) -> None:
        """Test WSLg PulseAudio socket used when standard paths unavailable."""
        config = Config()
        config.audio = True

        # Create WSLg PulseServer socket
        wslg_pulse = project_dir / "PulseServer"
        wslg_pulse.touch()

        env = {
            "USER": "testuser",
            "XDG_RUNTIME_DIR": str(project_dir / "empty-runtime"),
        }
        (project_dir / "empty-runtime").mkdir()

        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env, clear=True))
            stack.enter_context(
                patch("yaas.container._WSLG_PULSE_SERVER", wslg_pulse)
            )
            spec = build_container_spec(config, project_dir, ["bash"])

        # WSLg PulseServer should be mounted
        pulse_mount = next(
            (m for m in spec.mounts if m.target == str(wslg_pulse)), None
        )
        assert pulse_mount is not None

        # PULSE_SERVER should point to it
        assert spec.environment.get("PULSE_SERVER") == str(wslg_pulse)

    def test_dbus_skipped_on_wsl(self, mock_wsl, project_dir) -> None:
        """Test D-Bus is skipped on WSL2 (not available in WSLg)."""
        config = Config()
        config.dbus = True

        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir()
        bus_socket = runtime_dir / "bus"
        bus_socket.touch()

        env = {
            "USER": "testuser",
            "XDG_RUNTIME_DIR": str(runtime_dir),
        }
        with patch.dict(os.environ, env, clear=True):
            spec = build_container_spec(config, project_dir, ["bash"])

        # D-Bus should NOT be mounted or set in environment
        assert "DBUS_SESSION_BUS_ADDRESS" not in spec.environment
        mount_targets = [m.target for m in spec.mounts]
        assert str(bus_socket) not in mount_targets

    def test_gpu_works_on_wsl(self, mock_wsl, project_dir, clean_env) -> None:
        """Test GPU passthrough works on WSL2 (same /dev/dri path)."""
        config = Config()
        config.gpu = True

        dri_path = project_dir / "dri"
        dri_path.mkdir()
        render_node = dri_path / "renderD128"
        render_node.touch()

        real_path = Path

        def mock_path_side_effect(arg: str) -> Path:
            if arg == "/dev/dri":
                return dri_path
            if arg == "/dev/dri/renderD128":
                return render_node
            return real_path(arg)

        with patch("yaas.container.Path") as mock_path:
            mock_path.side_effect = mock_path_side_effect
            mock_path.home = real_path.home
            spec = build_container_spec(config, project_dir, ["bash"])

        assert spec.devices is not None
        assert "/dev/dri" in spec.devices
