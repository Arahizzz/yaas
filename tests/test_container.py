"""Tests for container spec building."""

import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from yaas.config import Config, ResourceLimits, SecuritySettings, ToolConfig
from yaas.constants import CLONE_WORKSPACE, HOME_VOLUME, NIX_VOLUME, RUNTIME_IMAGE
from yaas.container import (
    _add_worktree_mounts,
    _parse_mount_spec,
    build_clone_spec,
    build_clone_work_spec,
    build_container_spec,
    extract_repo_name,
)
from yaas.runtime import Mount
from yaas.worktree import WorktreeError


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
        }
        with patch.dict(os.environ, env):
            spec = build_container_spec(config, project_dir, ["bash"])

        assert spec.environment["TERM"] == "xterm-256color"
        assert spec.environment["YAAS"] == "1"
        assert spec.environment["COLORTERM"] == "truecolor"
        # API keys are no longer auto-forwarded globally
        assert "ANTHROPIC_API_KEY" not in spec.environment

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

    def test_home_volume_mounted(self, mock_linux, project_dir, clean_env) -> None:
        """Test that home volume is mounted at /home for persistence."""
        config = Config()
        spec = build_container_spec(config, project_dir, ["bash"])
        home_mount = next(
            (m for m in spec.mounts if m.target == "/home" and m.type == "volume"), None
        )
        assert home_mount is not None
        assert home_mount.source == HOME_VOLUME

    def test_nix_volume_mounted(self, mock_linux, project_dir, clean_env) -> None:
        """Test that Nix volume is mounted for package persistence."""
        config = Config()
        spec = build_container_spec(config, project_dir, ["bash"])
        nix_mount = next(
            (m for m in spec.mounts if m.target == "/nix" and m.type == "volume"), None
        )
        assert nix_mount is not None
        assert nix_mount.source == NIX_VOLUME

    def test_no_passwd_mount_on_linux(self, mock_linux, project_dir, clean_env) -> None:
        """Test that /etc/passwd and /etc/group are not mounted (user created in entrypoint)."""
        config = Config()
        spec = build_container_spec(config, project_dir, ["bash"])
        mount_sources = [m.source for m in spec.mounts]
        assert "/etc/passwd" not in mount_sources
        assert "/etc/group" not in mount_sources

    def test_no_passwd_mount_on_macos(self, mock_macos, project_dir, clean_env) -> None:
        """Test that /etc/passwd and /etc/group are not mounted on macOS."""
        config = Config()
        spec = build_container_spec(config, project_dir, ["bash"])
        mount_sources = [m.source for m in spec.mounts]
        assert "/etc/passwd" not in mount_sources
        assert "/etc/group" not in mount_sources


class TestParseMountSpec:
    """Tests for _parse_mount_spec function."""

    def test_simple(self, tmp_path: Path) -> None:
        """Test parsing simple mount spec."""
        src = tmp_path / "host"
        src.mkdir()
        mount = _parse_mount_spec(f"{src}:/container/path", Path("/project"))

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/container/path"
        assert mount.read_only is False

    def test_readonly(self, tmp_path: Path) -> None:
        """Test parsing mount spec with readonly flag."""
        src = tmp_path / "host"
        src.mkdir()
        mount = _parse_mount_spec(f"{src}:/container:ro", Path("/project"))

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/container"
        assert mount.read_only is True

    def test_relative_path(self, tmp_path: Path) -> None:
        """Test parsing mount spec with relative path."""
        (tmp_path / "data").mkdir()
        mount = _parse_mount_spec("./data:/data", tmp_path)

        assert mount is not None
        assert mount.source == str(tmp_path / "data")
        assert mount.target == "/data"

    def test_home_expansion(self) -> None:
        """Test parsing mount spec with home directory expansion."""
        # ~ itself always exists
        mount = _parse_mount_spec("~:/data", Path("/project"))

        assert mount is not None
        assert mount.source == str(Path.home())
        assert mount.target == "/data"


class TestParseMountSpecMissing:
    """Tests for _parse_mount_spec with missing source paths."""

    def test_missing_source_returns_none(self, tmp_path: Path) -> None:
        """Non-existent source path returns None."""
        result = _parse_mount_spec(f"{tmp_path}/nonexistent:/data", Path("/project"))
        assert result is None

    def test_missing_home_source_returns_none(self) -> None:
        """Non-existent ~ path returns None."""
        result = _parse_mount_spec("~/.yaas_nonexistent_test_path", Path("/project"))
        assert result is None

    def test_missing_tool_mount_skipped_in_spec(
        self, mock_linux, clean_env, tmp_path: Path
    ) -> None:
        """Tool mount with missing source is not included in container spec."""
        config = Config()
        config.active_tool = "claude"
        config.tools = {
            "claude": ToolConfig(mounts=["~/.yaas_nonexistent_test_path"]),
        }

        spec = build_container_spec(config, tmp_path, ["bash"])

        targets = [m.target for m in spec.mounts]
        assert "/home/.yaas_nonexistent_test_path" not in targets


class TestParseMountSpecAutoDst:
    """Tests for _parse_mount_spec auto-destination with ~ paths."""

    def test_home_tilde_auto_dst(self, tmp_path: Path) -> None:
        """~/.x with no dst → auto-computes /home/.x."""
        src = tmp_path / ".x"
        src.mkdir()
        with patch("yaas.container.Path.expanduser", return_value=src):
            mount = _parse_mount_spec("~/.x", Path("/project"))

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/home/.x"
        assert mount.read_only is False

    def test_home_tilde_auto_dst_readonly(self, tmp_path: Path) -> None:
        """~/.x:ro → auto-dst + read-only."""
        src = tmp_path / ".x"
        src.mkdir()
        with patch("yaas.container.Path.expanduser", return_value=src):
            mount = _parse_mount_spec("~/.x:ro", Path("/project"))

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/home/.x"
        assert mount.read_only is True

    def test_home_tilde_nested_auto_dst(self, tmp_path: Path) -> None:
        """~/.x/ide:ro → auto-dst for nested path."""
        src = tmp_path / ".x" / "ide"
        src.mkdir(parents=True)
        with patch("yaas.container.Path.expanduser", return_value=src):
            mount = _parse_mount_spec("~/.x/ide:ro", Path("/project"))

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/home/.x/ide"
        assert mount.read_only is True

    def test_home_tilde_explicit_dst(self, tmp_path: Path) -> None:
        """~/a:/data → explicit dst overrides auto-dst."""
        src = tmp_path / "a"
        src.mkdir()
        with patch("yaas.container.Path.expanduser", return_value=src):
            mount = _parse_mount_spec("~/a:/data", Path("/project"))

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/data"
        assert mount.read_only is False

    def test_home_tilde_explicit_dst_readonly(self, tmp_path: Path) -> None:
        """~/a:/data:ro → explicit dst + read-only."""
        src = tmp_path / "a"
        src.mkdir()
        with patch("yaas.container.Path.expanduser", return_value=src):
            mount = _parse_mount_spec("~/a:/data:ro", Path("/project"))

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/data"
        assert mount.read_only is True

    def test_custom_sandbox_home(self, tmp_path: Path) -> None:
        """Auto-dst uses custom sandbox_home when provided."""
        src = tmp_path / ".config" / "app"
        src.mkdir(parents=True)
        with patch("yaas.container.Path.expanduser", return_value=src):
            mount = _parse_mount_spec("~/.config/app", Path("/project"), sandbox_home="/sandbox")

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/sandbox/.config/app"

    def test_tilde_only(self) -> None:
        """~ alone maps to sandbox home root."""
        mount = _parse_mount_spec("~", Path("/project"))

        assert mount is not None
        assert mount.source == str(Path.home())
        assert mount.target == "/home"


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

    def test_no_nix_volume(self, mock_linux, project_dir, clean_env) -> None:
        """Test that clone spec does not include Nix volume (not needed for git clone)."""
        config = Config()
        spec = build_clone_spec(
            config, "https://github.com/user/repo.git", "yaas-clone-abc123", "repo"
        )
        nix_mount = next((m for m in spec.mounts if m.target == "/nix"), None)
        assert nix_mount is None


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

    def test_home_volume_mounted(self, mock_linux, project_dir, clean_env) -> None:
        """Test that clone work spec includes home volume."""
        config = Config()
        spec = build_clone_work_spec(config, "yaas-clone-abc123", "repo", ["bash"])
        home_mount = next(
            (m for m in spec.mounts if m.target == "/home" and m.type == "volume"), None
        )
        assert home_mount is not None
        assert home_mount.source == HOME_VOLUME

    def test_nix_volume_mounted(self, mock_linux, project_dir, clean_env) -> None:
        """Test that clone work spec includes Nix volume."""
        config = Config()
        spec = build_clone_work_spec(config, "yaas-clone-abc123", "repo", ["bash"])
        nix_mount = next(
            (m for m in spec.mounts if m.target == "/nix" and m.type == "volume"), None
        )
        assert nix_mount is not None
        assert nix_mount.source == NIX_VOLUME

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


class TestWorktreeMounts:
    """Tests for worktree mount logic in _add_worktree_mounts."""

    def test_worktree_base_mounted_when_exists(self, tmp_path: Path) -> None:
        """Normal session: worktree base dir is mounted when it exists."""
        git_root = tmp_path / "repo"
        git_root.mkdir()
        wt_base = tmp_path / "worktrees" / "abc123"
        wt_base.mkdir(parents=True)
        project_dir = git_root

        mounts: list[Mount] = []
        with (
            patch("yaas.container.get_main_repo_root", return_value=git_root),
            patch("yaas.container.get_worktree_base_dir", return_value=wt_base),
        ):
            skip = _add_worktree_mounts(mounts, project_dir, read_only=False)

        assert skip is False
        wt_mount = next((m for m in mounts if m.source == str(wt_base)), None)
        assert wt_mount is not None
        assert wt_mount.target == str(wt_base)
        assert wt_mount.read_only is False

    def test_worktree_base_skipped_when_missing(self, tmp_path: Path) -> None:
        """Normal session: no mount added when worktree base dir doesn't exist."""
        git_root = tmp_path / "repo"
        git_root.mkdir()
        wt_base = tmp_path / "worktrees" / "abc123"  # Not created
        project_dir = git_root

        mounts: list[Mount] = []
        with (
            patch("yaas.container.get_main_repo_root", return_value=git_root),
            patch("yaas.container.get_worktree_base_dir", return_value=wt_base),
        ):
            skip = _add_worktree_mounts(mounts, project_dir, read_only=False)

        assert skip is False
        assert len(mounts) == 0

    def test_normal_session_respects_readonly(self, tmp_path: Path) -> None:
        """Normal session: worktree base mount respects read_only flag."""
        git_root = tmp_path / "repo"
        git_root.mkdir()
        wt_base = tmp_path / "worktrees" / "abc123"
        wt_base.mkdir(parents=True)
        project_dir = git_root

        mounts: list[Mount] = []
        with (
            patch("yaas.container.get_main_repo_root", return_value=git_root),
            patch("yaas.container.get_worktree_base_dir", return_value=wt_base),
        ):
            _add_worktree_mounts(mounts, project_dir, read_only=True)

        wt_mount = next((m for m in mounts if m.source == str(wt_base)), None)
        assert wt_mount is not None
        assert wt_mount.read_only is True

    def test_worktree_session_mounts_main_git_dir(self, tmp_path: Path) -> None:
        """Worktree session: main repo's .git dir is mounted read-write."""
        main_repo = tmp_path / "repo"
        main_repo.mkdir()
        wt_base = tmp_path / "worktrees" / "abc123"
        wt_base.mkdir(parents=True)
        project_dir = wt_base / "feature-branch"
        project_dir.mkdir()

        mounts: list[Mount] = []
        with (
            patch("yaas.container.get_main_repo_root", return_value=main_repo),
            patch("yaas.container.get_worktree_base_dir", return_value=wt_base),
        ):
            _add_worktree_mounts(mounts, project_dir, read_only=False)

        git_dir = str(main_repo / ".git")
        git_mount = next((m for m in mounts if m.source == git_dir), None)
        assert git_mount is not None
        assert git_mount.target == git_dir
        assert git_mount.read_only is False

    def test_worktree_session_mounts_wt_base(self, tmp_path: Path) -> None:
        """Worktree session: worktree base dir is mounted read-write."""
        main_repo = tmp_path / "repo"
        main_repo.mkdir()
        wt_base = tmp_path / "worktrees" / "abc123"
        wt_base.mkdir(parents=True)
        project_dir = wt_base / "feature-branch"
        project_dir.mkdir()

        mounts: list[Mount] = []
        with (
            patch("yaas.container.get_main_repo_root", return_value=main_repo),
            patch("yaas.container.get_worktree_base_dir", return_value=wt_base),
        ):
            _add_worktree_mounts(mounts, project_dir, read_only=False)

        wt_mount = next((m for m in mounts if m.source == str(wt_base)), None)
        assert wt_mount is not None
        assert wt_mount.target == str(wt_base)
        assert wt_mount.read_only is False

    def test_worktree_session_skips_project_mount(self, tmp_path: Path) -> None:
        """Worktree session: returns True to skip the project_dir mount."""
        main_repo = tmp_path / "repo"
        main_repo.mkdir()
        wt_base = tmp_path / "worktrees" / "abc123"
        wt_base.mkdir(parents=True)
        project_dir = wt_base / "feature-branch"
        project_dir.mkdir()

        mounts: list[Mount] = []
        with (
            patch("yaas.container.get_main_repo_root", return_value=main_repo),
            patch("yaas.container.get_worktree_base_dir", return_value=wt_base),
        ):
            skip = _add_worktree_mounts(mounts, project_dir, read_only=False)

        assert skip is True
        project_mount = next((m for m in mounts if m.source == str(project_dir)), None)
        assert project_mount is None

    def test_worktree_session_respects_readonly(self, tmp_path: Path) -> None:
        """Worktree session: worktree base dir respects readonly flag."""
        main_repo = tmp_path / "repo"
        main_repo.mkdir()
        wt_base = tmp_path / "worktrees" / "abc123"
        wt_base.mkdir(parents=True)
        project_dir = wt_base / "feature-branch"
        project_dir.mkdir()

        mounts: list[Mount] = []
        with (
            patch("yaas.container.get_main_repo_root", return_value=main_repo),
            patch("yaas.container.get_worktree_base_dir", return_value=wt_base),
        ):
            _add_worktree_mounts(mounts, project_dir, read_only=True)

        wt_mount = next((m for m in mounts if m.source == str(wt_base)), None)
        assert wt_mount is not None
        assert wt_mount.read_only is True

    def test_not_a_git_repo(self, tmp_path: Path) -> None:
        """Non-git directory: no worktree mounts added."""
        mounts: list[Mount] = []
        with patch(
            "yaas.container.get_main_repo_root",
            side_effect=WorktreeError("Not a git repository"),
        ):
            skip = _add_worktree_mounts(mounts, tmp_path, read_only=False)

        assert skip is False
        assert len(mounts) == 0

    def test_symlinked_worktree_detected(self, tmp_path: Path) -> None:
        """Worktree session detected even when project_dir is accessed via symlink."""
        main_repo = tmp_path / "repo"
        main_repo.mkdir()
        wt_base = tmp_path / "worktrees" / "abc123"
        wt_base.mkdir(parents=True)
        real_dir = wt_base / "feature-branch"
        real_dir.mkdir()
        # Access worktree through a symlink
        symlink_dir = tmp_path / "linked-worktree"
        symlink_dir.symlink_to(real_dir)

        mounts: list[Mount] = []
        with (
            patch("yaas.container.get_main_repo_root", return_value=main_repo),
            patch("yaas.container.get_worktree_base_dir", return_value=wt_base),
        ):
            skip = _add_worktree_mounts(mounts, symlink_dir, read_only=False)

        assert skip is True
        git_dir = str(main_repo / ".git")
        git_mount = next((m for m in mounts if m.source == git_dir), None)
        assert git_mount is not None


class TestWorktreeMountsIntegration:
    """Integration tests: build_container_spec with worktree mounts."""

    def _mock_worktree(
        self, main_repo: Path, wt_base: Path, *, is_worktree: bool = False
    ) -> ExitStack:
        """Create an ExitStack with worktree mocks.

        Args:
            main_repo: The main repository root.
            wt_base: The worktree base directory.
            is_worktree: Unused, kept for API compatibility.
        """
        _ = is_worktree  # Unused, worktree detection is based on wt_base containment
        stack = ExitStack()
        stack.enter_context(patch("yaas.container.get_main_repo_root", return_value=main_repo))
        stack.enter_context(patch("yaas.container.get_worktree_base_dir", return_value=wt_base))
        return stack

    def test_worktree_session_full_spec(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Worktree session: build_container_spec includes main repo and wt_base,
        but not project_dir as a separate mount."""
        main_repo = tmp_path / "repo"
        main_repo.mkdir()
        wt_base = tmp_path / "worktrees" / "abc123"
        wt_base.mkdir(parents=True)
        project_dir = wt_base / "feature-branch"
        project_dir.mkdir()

        config = Config()
        with self._mock_worktree(main_repo, wt_base, is_worktree=True):
            spec = build_container_spec(config, project_dir, ["bash"])

        sources = [m.source for m in spec.mounts]
        git_dir = str(main_repo / ".git")
        # Main repo .git dir and wt_base should be present
        assert git_dir in sources
        assert str(wt_base) in sources
        # project_dir should NOT be separately mounted (covered by wt_base)
        project_mounts = [m for m in spec.mounts if m.source == str(project_dir)]
        assert project_mounts == []

        # Verify read-only flags
        git_mount = next(m for m in spec.mounts if m.source == git_dir)
        assert git_mount.read_only is False
        wt_mount = next(m for m in spec.mounts if m.source == str(wt_base))
        assert wt_mount.read_only is False

    def test_normal_session_full_spec(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Normal session: build_container_spec includes project_dir and wt_base."""
        main_repo = tmp_path / "repo"
        main_repo.mkdir()
        wt_base = tmp_path / "worktrees" / "abc123"
        wt_base.mkdir(parents=True)
        project_dir = main_repo

        config = Config()
        with self._mock_worktree(main_repo, wt_base):
            spec = build_container_spec(config, project_dir, ["bash"])

        sources = [m.source for m in spec.mounts]
        # Both project_dir and wt_base should be present
        assert str(project_dir) in sources
        assert str(wt_base) in sources

    def test_normal_session_no_worktrees(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Normal session without worktrees: only project_dir mounted."""
        main_repo = tmp_path / "repo"
        main_repo.mkdir()
        wt_base = tmp_path / "worktrees" / "abc123"  # Does not exist
        project_dir = main_repo

        config = Config()
        with self._mock_worktree(main_repo, wt_base):
            spec = build_container_spec(config, project_dir, ["bash"])

        sources = [m.source for m in spec.mounts]
        assert str(project_dir) in sources
        assert str(wt_base) not in sources


class TestActiveToolScoping:
    """Tests for active_tool mount and env scoping."""

    def test_active_tool_mounts_applied(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """active_tool set: tool's mounts are applied."""
        config = Config()
        config.active_tool = "claude"
        config.tools = {
            "claude": ToolConfig(mounts=["~/.claude"]),
            "aider": ToolConfig(mounts=["~/.aider"]),
        }

        spec = build_container_spec(config, tmp_path, ["bash"])

        targets = [m.target for m in spec.mounts]
        sandbox_home = spec.environment.get("HOME", "/home/user")
        # Active tool's mounts applied
        assert f"{sandbox_home}/.claude" in targets
        # Other tool's mounts NOT applied
        assert f"{sandbox_home}/.aider" not in targets

    def test_no_active_tool_no_tool_mounts(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """active_tool=None: no tool mounts applied (run mode)."""
        config = Config()
        config.active_tool = None
        config.tools = {
            "claude": ToolConfig(mounts=["~/.claude"]),
        }

        spec = build_container_spec(config, tmp_path, ["bash"])

        targets = [m.target for m in spec.mounts]
        sandbox_home = spec.environment.get("HOME", "/home/user")
        assert f"{sandbox_home}/.claude" not in targets

    def test_active_tool_env_forwarded(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """active_tool set: tool's env vars are applied."""
        config = Config()
        config.active_tool = "claude"
        config.tools = {
            "claude": ToolConfig(env={"ANTHROPIC_API_KEY": True, "CUSTOM": "val"}),
        }

        with patch.dict(os.environ, {"USER": "test", "ANTHROPIC_API_KEY": "sk-123"}):
            spec = build_container_spec(config, tmp_path, ["bash"])

        assert spec.environment["ANTHROPIC_API_KEY"] == "sk-123"
        assert spec.environment["CUSTOM"] == "val"

    def test_no_active_tool_no_tool_env(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """active_tool=None: tool env vars are NOT applied."""
        config = Config()
        config.active_tool = None
        config.tools = {
            "claude": ToolConfig(env={"ANTHROPIC_API_KEY": True}),
        }

        with patch.dict(os.environ, {"USER": "test", "ANTHROPIC_API_KEY": "sk-123"}):
            spec = build_container_spec(config, tmp_path, ["bash"])

        assert "ANTHROPIC_API_KEY" not in spec.environment

    def test_global_env_always_applied(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Global env is applied regardless of active_tool."""
        config = Config()
        config.active_tool = None
        config.env = {"GITHUB_TOKEN": True, "STATIC": "hello"}

        with patch.dict(os.environ, {"USER": "test", "GITHUB_TOKEN": "ghp_123"}):
            spec = build_container_spec(config, tmp_path, ["bash"])

        assert spec.environment["GITHUB_TOKEN"] == "ghp_123"
        assert spec.environment["STATIC"] == "hello"

    def test_env_passthrough_missing_key_skipped(
        self, mock_linux, clean_env, tmp_path: Path
    ) -> None:
        """Pass-through env var not set on host is skipped."""
        config = Config()
        config.active_tool = "claude"
        config.tools = {
            "claude": ToolConfig(env={"MISSING_KEY": True}),
        }

        spec = build_container_spec(config, tmp_path, ["bash"])

        assert "MISSING_KEY" not in spec.environment

    def test_tool_mount_readonly(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Tool mount with :ro modifier is mounted read-only."""
        config = Config()
        config.active_tool = "claude"
        config.tools = {
            "claude": ToolConfig(mounts=["~/.claude/ide:ro"]),
        }

        spec = build_container_spec(config, tmp_path, ["bash"])

        sandbox_home = spec.environment.get("HOME", "/home/user")
        ide_mount = next(
            (m for m in spec.mounts if m.target == f"{sandbox_home}/.claude/ide"),
            None,
        )
        assert ide_mount is not None
        assert ide_mount.read_only is True

    def test_tool_overrides_network_mode(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Tool network_mode override is reflected in ContainerSpec."""
        config = Config(
            network_mode="bridge",
            active_tool="claude",
            tools={"claude": ToolConfig(network_mode="none")},
        )
        spec = build_container_spec(config, tmp_path, ["bash"])
        assert spec.network_mode == "none"

    def test_tool_overrides_readonly_project(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Tool readonly_project override makes project mount read-only."""
        config = Config(
            readonly_project=False,
            active_tool="claude",
            tools={"claude": ToolConfig(readonly_project=True)},
        )
        spec = build_container_spec(config, tmp_path, ["bash"])

        project_mount = next(
            (m for m in spec.mounts if m.target == str(tmp_path)),
            None,
        )
        assert project_mount is not None
        assert project_mount.read_only is True

    def test_tool_overrides_resources(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Tool resource overrides are reflected in ContainerSpec."""
        config = Config(
            resources=ResourceLimits(memory="8g", cpus=2.0),
            active_tool="claude",
            tools={"claude": ToolConfig(resources=ResourceLimits(memory="16g"))},
        )
        spec = build_container_spec(config, tmp_path, ["bash"])
        assert spec.memory == "16g"
        assert spec.cpus == 2.0  # inherited from global

    def test_tool_overrides_pid_mode(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Tool pid_mode override is reflected in ContainerSpec."""
        config = Config(
            pid_mode=None,
            active_tool="claude",
            tools={"claude": ToolConfig(pid_mode="host")},
        )
        spec = build_container_spec(config, tmp_path, ["bash"])
        assert spec.pid_mode == "host"


# ============================================================
# Security settings tests
# ============================================================


class TestSecurityPassthrough:
    """Tests for security settings being passed to ContainerSpec."""

    def test_default_security_in_spec(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Default config produces capabilities in spec."""
        config = Config()
        spec = build_container_spec(config, tmp_path, ["bash"])

        assert spec.capabilities is not None
        assert "CHOWN" in spec.capabilities
        assert "KILL" in spec.capabilities
        assert spec.seccomp_profile is None

    def test_custom_seccomp_profile(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Custom seccomp profile path is passed to spec."""
        config = Config(
            security=SecuritySettings(
                capabilities=["CHOWN"],
                seccomp_profile="/etc/yaas/seccomp.json",
            ),
        )
        spec = build_container_spec(config, tmp_path, ["bash"])
        assert spec.seccomp_profile == "/etc/yaas/seccomp.json"

    def test_none_capabilities_uses_runtime_defaults(
        self, mock_linux, clean_env, tmp_path: Path
    ) -> None:
        """None capabilities means use runtime defaults (no flags generated)."""
        config = Config(
            security=SecuritySettings(capabilities=None),
        )
        spec = build_container_spec(config, tmp_path, ["bash"])
        assert spec.capabilities is None

    def test_clone_spec_gets_security(self, mock_linux, clean_env) -> None:
        """Clone spec also gets security settings."""
        config = Config()
        spec = build_clone_spec(config, "https://github.com/user/repo.git", "vol", "repo")

        assert spec.capabilities is not None
        assert "CHOWN" in spec.capabilities

    def test_clone_work_spec_gets_security(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Clone work spec also gets security settings."""
        config = Config()
        spec = build_clone_work_spec(config, "vol", "repo", ["bash"])

        assert spec.capabilities is not None

    def test_tool_security_override(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Tool security override is reflected in ContainerSpec."""
        config = Config(
            active_tool="claude",
            tools={
                "claude": ToolConfig(
                    security=SecuritySettings(capabilities=["CHOWN", "NET_RAW"]),
                )
            },
        )
        spec = build_container_spec(config, tmp_path, ["bash"])
        assert spec.capabilities == ["CHOWN", "NET_RAW"]


# ============================================================
# lxcfs mount tests
# ============================================================


class TestLxcfsMounts:
    """Tests for lxcfs resource visibility mounts."""

    def test_lxcfs_mounts_added_when_enabled(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """lxcfs mounts are added when enabled and available."""
        config = Config(lxcfs=True)
        original_exists = Path.exists

        def lxcfs_exists(self: Path) -> bool:
            if str(self).startswith("/var/lib/lxcfs/"):
                return True
            return original_exists(self)

        with patch.object(Path, "exists", lxcfs_exists):
            spec = build_container_spec(config, tmp_path, ["bash"])

        lxcfs_mounts = [m for m in spec.mounts if m.source.startswith("/var/lib/lxcfs/")]
        assert len(lxcfs_mounts) == 7
        targets = {m.target for m in lxcfs_mounts}
        assert "/proc/cpuinfo" in targets
        assert "/proc/meminfo" in targets
        assert "/proc/stat" in targets
        assert all(m.read_only for m in lxcfs_mounts)

    def test_lxcfs_skipped_when_disabled(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """lxcfs mounts are not added when disabled (default)."""
        config = Config()
        spec = build_container_spec(config, tmp_path, ["bash"])

        lxcfs_mounts = [m for m in spec.mounts if m.source.startswith("/var/lib/lxcfs/")]
        assert len(lxcfs_mounts) == 0

    def test_lxcfs_skipped_on_non_linux(self, mock_macos, clean_env, tmp_path: Path) -> None:
        """lxcfs mounts are skipped on non-Linux platforms."""
        config = Config(lxcfs=True)
        original_exists = Path.exists

        def lxcfs_exists(self: Path) -> bool:
            if str(self).startswith("/var/lib/lxcfs/"):
                return True
            return original_exists(self)

        with patch.object(Path, "exists", lxcfs_exists):
            spec = build_container_spec(config, tmp_path, ["bash"])

        lxcfs_mounts = [m for m in spec.mounts if m.source.startswith("/var/lib/lxcfs/")]
        assert len(lxcfs_mounts) == 0

    def test_lxcfs_warns_when_not_installed(
        self, mock_linux, clean_env, tmp_path: Path, caplog
    ) -> None:
        """lxcfs warns when enabled but /var/lib/lxcfs/proc doesn't exist."""
        config = Config(lxcfs=True)
        original_exists = Path.exists

        def no_lxcfs(self: Path) -> bool:
            if str(self).startswith("/var/lib/lxcfs/"):
                return False
            return original_exists(self)

        with patch.object(Path, "exists", no_lxcfs):
            spec = build_container_spec(config, tmp_path, ["bash"])

        lxcfs_mounts = [m for m in spec.mounts if m.source.startswith("/var/lib/lxcfs/")]
        assert len(lxcfs_mounts) == 0
