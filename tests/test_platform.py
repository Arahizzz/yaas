"""Tests for platform detection module."""

import os
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import mock_open, patch

import pytest

from yaas.platform import (
    PlatformError,
    check_platform_support,
    get_container_socket_paths,
    get_ssh_agent_socket,
    get_uid_gid,
    is_linux,
    is_macos,
    is_windows,
    is_wsl,
)


class TestPlatformDetection:
    """Tests for platform detection functions."""

    def test_is_linux_on_linux(self) -> None:
        """Test is_linux returns True on Linux."""
        with patch("yaas.platform.sys.platform", "linux"):
            assert is_linux() is True

    def test_is_linux_on_macos(self) -> None:
        """Test is_linux returns False on macOS."""
        with patch("yaas.platform.sys.platform", "darwin"):
            assert is_linux() is False

    def test_is_macos_on_macos(self) -> None:
        """Test is_macos returns True on macOS."""
        with patch("yaas.platform.sys.platform", "darwin"):
            assert is_macos() is True

    def test_is_macos_on_linux(self) -> None:
        """Test is_macos returns False on Linux."""
        with patch("yaas.platform.sys.platform", "linux"):
            assert is_macos() is False

    def test_is_windows_on_windows(self) -> None:
        """Test is_windows returns True on Windows."""
        with patch("yaas.platform.sys.platform", "win32"):
            assert is_windows() is True

    def test_is_windows_on_linux(self) -> None:
        """Test is_windows returns False on Linux."""
        with patch("yaas.platform.sys.platform", "linux"):
            assert is_windows() is False

    def test_is_wsl_on_wsl(self) -> None:
        """Test is_wsl returns True on WSL."""
        wsl_version = "Linux version 5.10.16.3-microsoft-standard-WSL2"
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.platform.sys.platform", "linux"))
            stack.enter_context(patch("builtins.open", mock_open(read_data=wsl_version)))
            assert is_wsl() is True

    def test_is_wsl_on_native_linux(self) -> None:
        """Test is_wsl returns False on native Linux."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.platform.sys.platform", "linux"))
            stack.enter_context(
                patch("builtins.open", mock_open(read_data="Linux version 5.15.0-generic"))
            )
            assert is_wsl() is False

    def test_is_wsl_on_macos(self) -> None:
        """Test is_wsl returns False on macOS."""
        with patch("yaas.platform.sys.platform", "darwin"):
            assert is_wsl() is False

    def test_is_wsl_proc_version_unreadable(self) -> None:
        """Test is_wsl returns False when /proc/version cannot be read."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.platform.sys.platform", "linux"))
            stack.enter_context(patch("builtins.open", side_effect=OSError("Permission denied")))
            assert is_wsl() is False


class TestGetUidGid:
    """Tests for UID/GID retrieval."""

    def test_get_uid_gid_on_linux(self) -> None:
        """Test get_uid_gid returns actual UID/GID on Linux."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.platform.is_linux", return_value=True))
            stack.enter_context(patch("yaas.platform.os.getuid", return_value=1000))
            stack.enter_context(patch("yaas.platform.os.getgid", return_value=1000))
            uid, gid = get_uid_gid()
            assert uid == 1000
            assert gid == 1000

    def test_get_uid_gid_on_macos(self) -> None:
        """Test get_uid_gid returns defaults on macOS."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.platform.is_linux", return_value=False))
            stack.enter_context(patch("yaas.platform.is_macos", return_value=True))
            uid, gid = get_uid_gid()
            assert uid == 1000
            assert gid == 1000

    def test_get_uid_gid_on_windows_fallback(self) -> None:
        """Test get_uid_gid returns defaults on unknown platform (Windows/other)."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.platform.is_linux", return_value=False))
            stack.enter_context(patch("yaas.platform.is_macos", return_value=False))
            uid, gid = get_uid_gid()
            assert uid == 1000
            assert gid == 1000


class TestGetSshAgentSocket:
    """Tests for SSH agent socket detection."""

    def test_get_ssh_agent_socket_from_env(self) -> None:
        """Test get_ssh_agent_socket uses SSH_AUTH_SOCK env var."""
        with TemporaryDirectory() as tmpdir:
            sock_path = Path(tmpdir) / "agent.sock"
            sock_path.touch()

            with patch.dict(os.environ, {"SSH_AUTH_SOCK": str(sock_path)}):
                result = get_ssh_agent_socket()
                assert result == sock_path

    def test_get_ssh_agent_socket_missing_env(self) -> None:
        """Test get_ssh_agent_socket returns None when env not set."""
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {}, clear=True))
            stack.enter_context(patch("yaas.platform.is_macos", return_value=False))
            result = get_ssh_agent_socket()
            assert result is None

    def test_get_ssh_agent_socket_env_path_not_exists(self) -> None:
        """Test get_ssh_agent_socket returns None when env socket doesn't exist."""
        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(os.environ, {"SSH_AUTH_SOCK": "/nonexistent/path/agent.sock"})
            )
            stack.enter_context(patch("yaas.platform.is_macos", return_value=False))
            result = get_ssh_agent_socket()
            assert result is None

    def test_get_ssh_agent_socket_macos_fallback(self) -> None:
        """Test get_ssh_agent_socket checks macOS launchd paths."""
        with TemporaryDirectory() as tmpdir:
            launchd_dir = Path(tmpdir) / "com.apple.launchd.test"
            launchd_dir.mkdir()
            sock_path = launchd_dir / "Listeners"
            sock_path.touch()

            with ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ, {}, clear=True))
                stack.enter_context(patch("yaas.platform.is_macos", return_value=True))
                stack.enter_context(patch("yaas.platform.glob.glob", return_value=[str(sock_path)]))
                result = get_ssh_agent_socket()
                assert result == sock_path


class TestGetContainerSocketPaths:
    """Tests for container socket path detection."""

    def test_linux_socket_paths(self) -> None:
        """Test get_container_socket_paths returns Linux socket paths."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.platform.is_macos", return_value=False))
            stack.enter_context(patch("yaas.platform.is_linux", return_value=True))
            stack.enter_context(patch("yaas.platform.os.getuid", return_value=1000))
            stack.enter_context(patch.dict(os.environ, {}, clear=True))
            paths = get_container_socket_paths()

        path_strs = [str(p) for p in paths]
        assert "/run/user/1000/podman/podman.sock" in path_strs
        assert "/var/run/docker.sock" in path_strs
        assert "/run/docker.sock" in path_strs
        assert "/run/podman/podman.sock" in path_strs

    def test_linux_docker_only(self) -> None:
        """Test get_container_socket_paths with docker_only excludes Podman."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.platform.is_macos", return_value=False))
            stack.enter_context(patch("yaas.platform.is_linux", return_value=True))
            stack.enter_context(patch("yaas.platform.os.getuid", return_value=1000))
            stack.enter_context(patch.dict(os.environ, {}, clear=True))
            paths = get_container_socket_paths(docker_only=True)

        path_strs = [str(p) for p in paths]
        assert "/var/run/docker.sock" in path_strs
        assert "/run/docker.sock" in path_strs
        # Podman sockets should NOT be included
        assert "/run/user/1000/podman/podman.sock" not in path_strs
        assert "/run/podman/podman.sock" not in path_strs

    def test_linux_xdg_runtime_socket(self) -> None:
        """Test get_container_socket_paths includes XDG_RUNTIME_DIR socket."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.platform.is_macos", return_value=False))
            stack.enter_context(patch("yaas.platform.is_linux", return_value=True))
            stack.enter_context(patch("yaas.platform.os.getuid", return_value=1000))
            stack.enter_context(patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}))
            paths = get_container_socket_paths()

        path_strs = [str(p) for p in paths]
        assert "/run/user/1000/docker.sock" in path_strs

    def test_docker_host_env_priority(self) -> None:
        """Test DOCKER_HOST env var takes highest priority."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.platform.is_macos", return_value=False))
            stack.enter_context(patch("yaas.platform.is_linux", return_value=True))
            stack.enter_context(patch("yaas.platform.os.getuid", return_value=1000))
            env = {"DOCKER_HOST": "unix:///custom/docker.sock"}
            stack.enter_context(patch.dict(os.environ, env, clear=True))
            paths = get_container_socket_paths()

        path_strs = [str(p) for p in paths]
        # Custom socket should be first
        assert path_strs[0] == "/custom/docker.sock"

    def test_macos_socket_paths(self) -> None:
        """Test get_container_socket_paths returns macOS Docker Desktop socket paths."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.platform.is_macos", return_value=True))
            stack.enter_context(patch("yaas.platform.Path.home", return_value=Path("/Users/test")))
            stack.enter_context(patch.dict(os.environ, {}, clear=True))
            paths = get_container_socket_paths()

        path_strs = [str(p) for p in paths]
        assert "/Users/test/.docker/run/docker.sock" in path_strs
        assert "/var/run/docker.sock" in path_strs


class TestPlatformSupport:
    """Tests for platform support checking."""

    def test_check_platform_support_linux(self) -> None:
        """Test check_platform_support passes on Linux."""
        with patch("yaas.platform.is_windows", return_value=False):
            # Should not raise
            check_platform_support()

    def test_check_platform_support_macos(self) -> None:
        """Test check_platform_support passes on macOS."""
        with patch("yaas.platform.is_windows", return_value=False):
            # Should not raise
            check_platform_support()

    def test_check_platform_support_windows(self) -> None:
        """Test check_platform_support raises on Windows."""
        with patch("yaas.platform.is_windows", return_value=True):
            with pytest.raises(PlatformError, match="WSL2"):
                check_platform_support()
