"""Tests for container spec building."""

import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

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
