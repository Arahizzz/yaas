"""Tests for container spec building."""

import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from yaas.config import Config, ResourceLimits, SecuritySettings, ToolConfig
from yaas.constants import HOME_VOLUME, NIX_VOLUME, RUNTIME_IMAGE
from yaas.container import (
    _add_worktree_mounts,
    _build_preamble,
    _parse_mount_spec,
    build_container_spec,
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

    def test_run_tmpfs_mounted(self, mock_linux, project_dir, clean_env) -> None:
        """Test that /run is mounted as tmpfs for fresh runtime state on every start."""
        config = Config()
        spec = build_container_spec(config, project_dir, ["bash"])
        run_mount = next(
            (m for m in spec.mounts if m.target == "/run" and m.type == "tmpfs"), None
        )
        assert run_mount is not None

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

    def test_home_expansion(self, tmp_path: Path) -> None:
        """Test parsing mount spec with home directory expansion."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        with patch("yaas.container.Path.expanduser", return_value=fake_home):
            mount = _parse_mount_spec("~:/data", Path("/project"))

        assert mount is not None
        assert mount.source == str(fake_home)
        assert mount.target == "/data"


class TestParseMountSpecMissing:
    """Tests for _parse_mount_spec with missing source paths."""

    def test_missing_source_returns_none(self, tmp_path: Path) -> None:
        """Non-existent source path returns None."""
        result = _parse_mount_spec(f"{tmp_path}/nonexistent:/data", Path("/project"))
        assert result is None

    def test_missing_home_source_returns_none(self, tmp_path: Path) -> None:
        """Non-existent ~ path returns None."""
        nonexistent = tmp_path / "fakehome" / ".yaas_nonexistent"
        with patch("yaas.container.Path.expanduser", return_value=nonexistent):
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
        nonexistent = tmp_path / "fakehome" / ".yaas_nonexistent_test_path"

        with patch("yaas.container.Path.expanduser", return_value=nonexistent):
            spec = build_container_spec(config, tmp_path, ["bash"])

        targets = [m.target for m in spec.mounts]
        assert "/home/.yaas_nonexistent_test_path" not in targets


class TestParseMountSpecAutoDst:
    """Tests for _parse_mount_spec auto-destination with ~ paths."""

    @staticmethod
    def _fake_home(tmp_path: Path, rel: str) -> tuple[Path, object]:
        """Create a fake home subdir and return (path, expanduser_patch)."""
        src = tmp_path / rel
        src.mkdir(parents=True, exist_ok=True)
        return src, patch("yaas.container.Path.expanduser", return_value=src)

    def test_home_tilde_auto_dst(self, tmp_path: Path) -> None:
        """~/.x with no dst → auto-computes /home/.x."""
        src, mock_eu = self._fake_home(tmp_path, ".x")
        with mock_eu:
            mount = _parse_mount_spec("~/.x", Path("/project"))

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/home/.x"
        assert mount.read_only is False

    def test_home_tilde_auto_dst_readonly(self, tmp_path: Path) -> None:
        """~/.x:ro → auto-dst + read-only."""
        src, mock_eu = self._fake_home(tmp_path, ".x")
        with mock_eu:
            mount = _parse_mount_spec("~/.x:ro", Path("/project"))

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/home/.x"
        assert mount.read_only is True

    def test_home_tilde_nested_auto_dst(self, tmp_path: Path) -> None:
        """~/.x/ide:ro → auto-dst for nested path."""
        src, mock_eu = self._fake_home(tmp_path, ".x/ide")
        with mock_eu:
            mount = _parse_mount_spec("~/.x/ide:ro", Path("/project"))

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/home/.x/ide"
        assert mount.read_only is True

    def test_home_tilde_explicit_dst(self, tmp_path: Path) -> None:
        """~/a:/data → explicit dst overrides auto-dst."""
        src, mock_eu = self._fake_home(tmp_path, "a")
        with mock_eu:
            mount = _parse_mount_spec("~/a:/data", Path("/project"))

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/data"
        assert mount.read_only is False

    def test_home_tilde_explicit_dst_readonly(self, tmp_path: Path) -> None:
        """~/a:/data:ro → explicit dst + read-only."""
        src, mock_eu = self._fake_home(tmp_path, "a")
        with mock_eu:
            mount = _parse_mount_spec("~/a:/data:ro", Path("/project"))

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/data"
        assert mount.read_only is True

    def test_custom_sandbox_home(self, tmp_path: Path) -> None:
        """Auto-dst uses custom sandbox_home when provided."""
        src, mock_eu = self._fake_home(tmp_path, ".config/app")
        with mock_eu:
            mount = _parse_mount_spec("~/.config/app", Path("/project"), sandbox_home="/sandbox")

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/sandbox/.config/app"

    def test_tilde_only(self, tmp_path: Path) -> None:
        """~ alone maps to sandbox home root."""
        src, mock_eu = self._fake_home(tmp_path, "fakehome")
        with mock_eu:
            mount = _parse_mount_spec("~", Path("/project"))

        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/home"


class TestParseMountSpecExtended:
    """Tests for _parse_mount_spec with extended type=key,value syntax."""

    def test_volume_mount(self) -> None:
        """type=volume,src=name,dst=/path → volume mount."""
        mount = _parse_mount_spec("type=volume,src=yaas-home,dst=/home", Path("/project"))
        assert mount is not None
        assert mount.source == "yaas-home"
        assert mount.target == "/home"
        assert mount.type == "volume"
        assert mount.read_only is False

    def test_volume_mount_readonly(self) -> None:
        """type=volume with readonly=true."""
        mount = _parse_mount_spec(
            "type=volume,src=data,dst=/data,readonly=true", Path("/project")
        )
        assert mount is not None
        assert mount.type == "volume"
        assert mount.read_only is True

    def test_tmpfs_mount(self) -> None:
        """type=tmpfs,dst=/tmp → tmpfs mount."""
        mount = _parse_mount_spec("type=tmpfs,dst=/tmp", Path("/project"))
        assert mount is not None
        assert mount.source == ""
        assert mount.target == "/tmp"
        assert mount.type == "tmpfs"

    def test_bind_mount(self, tmp_path: Path) -> None:
        """type=bind,src=/abs,dst=/data → bind mount."""
        src = tmp_path / "data"
        src.mkdir()
        mount = _parse_mount_spec(f"type=bind,src={src},dst=/data", Path("/project"))
        assert mount is not None
        assert mount.source == str(src)
        assert mount.target == "/data"
        assert mount.type == "bind"

    def test_bind_mount_readonly(self, tmp_path: Path) -> None:
        """type=bind with ro=true."""
        src = tmp_path / "data"
        src.mkdir()
        mount = _parse_mount_spec(f"type=bind,src={src},dst=/data,ro=true", Path("/project"))
        assert mount is not None
        assert mount.read_only is True

    def test_bind_mount_missing_source(self, tmp_path: Path) -> None:
        """type=bind with non-existent source returns None."""
        result = _parse_mount_spec(
            f"type=bind,src={tmp_path}/nonexistent,dst=/data", Path("/project")
        )
        assert result is None

    def test_aliases_source_destination(self) -> None:
        """source/destination aliases work."""
        mount = _parse_mount_spec(
            "type=volume,source=my-vol,destination=/mnt", Path("/project")
        )
        assert mount is not None
        assert mount.source == "my-vol"
        assert mount.target == "/mnt"

    def test_alias_target(self) -> None:
        """target alias works for destination."""
        mount = _parse_mount_spec("type=volume,src=vol,target=/mnt", Path("/project"))
        assert mount is not None
        assert mount.target == "/mnt"

    def test_missing_dst_returns_none(self) -> None:
        """Missing destination returns None."""
        result = _parse_mount_spec("type=volume,src=name", Path("/project"))
        assert result is None

    def test_missing_type_returns_none(self) -> None:
        """Missing type returns None."""
        result = _parse_mount_spec("src=name,dst=/data", Path("/project"))
        assert result is None

    def test_unknown_type_returns_none(self) -> None:
        """Unknown mount type returns None."""
        result = _parse_mount_spec("type=nfs,src=name,dst=/data", Path("/project"))
        assert result is None


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

        # Check WAYLAND_DISPLAY is forwarded but XDG_RUNTIME_DIR is not
        # (entrypoint sets XDG_RUNTIME_DIR for the container's SHELL_UID)
        assert spec.environment.get("WAYLAND_DISPLAY") == "wayland-0"
        assert "XDG_RUNTIME_DIR" not in spec.environment

        # Check wayland socket is mounted into /run/host/ staging area
        mount_targets = [m.target for m in spec.mounts]
        assert "/run/host/wayland-0" in mount_targets

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

        # No host sockets should be mounted
        mount_targets = [m.target for m in spec.mounts]
        assert "/run/host/wayland-0" not in mount_targets

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

        # No host sockets should be mounted
        mount_targets = [m.target for m in spec.mounts]
        assert "/run/host/wayland-0" not in mount_targets


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

    def test_worktree_base_created_when_missing(self, tmp_path: Path) -> None:
        """Normal session: worktree base dir is created and mounted when missing."""
        git_root = tmp_path / "repo"
        git_root.mkdir()
        wt_base = tmp_path / "worktrees" / "abc123"  # Not created yet
        project_dir = git_root

        mounts: list[Mount] = []
        with (
            patch("yaas.container.get_main_repo_root", return_value=git_root),
            patch("yaas.container.get_worktree_base_dir", return_value=wt_base),
        ):
            skip = _add_worktree_mounts(mounts, project_dir, read_only=False)

        assert skip is False
        assert wt_base.exists()
        wt_mount = next((m for m in mounts if m.source == str(wt_base)), None)
        assert wt_mount is not None

    def test_normal_session_wt_base_always_rw(self, tmp_path: Path) -> None:
        """Normal session: worktree base is always RW regardless of read_only flag."""
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
        assert wt_mount.read_only is False

    def test_worktree_session_mounts_main_repo(self, tmp_path: Path) -> None:
        """Worktree session: main repo is mounted."""
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

        repo_mount = next((m for m in mounts if m.source == str(main_repo)), None)
        assert repo_mount is not None
        assert repo_mount.target == str(main_repo)
        assert repo_mount.read_only is False

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

    def test_worktree_session_readonly_applies_to_main_repo(self, tmp_path: Path) -> None:
        """Worktree session: read_only applies to main repo, wt_base is always RW."""
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

        # wt_base is always RW (agents need to create worktrees)
        wt_mount = next((m for m in mounts if m.source == str(wt_base)), None)
        assert wt_mount is not None
        assert wt_mount.read_only is False

        # main_repo respects the read_only flag
        repo_mount = next((m for m in mounts if m.source == str(main_repo)), None)
        assert repo_mount is not None
        assert repo_mount.read_only is True

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
        repo_mount = next((m for m in mounts if m.source == str(main_repo)), None)
        assert repo_mount is not None


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
        # Main repo and wt_base should be present
        assert str(main_repo) in sources
        assert str(wt_base) in sources
        # project_dir should NOT be separately mounted (covered by wt_base)
        project_mounts = [m for m in spec.mounts if m.source == str(project_dir)]
        assert project_mounts == []

        # main_repo respects readonly, wt_base is always RW
        repo_mount = next(m for m in spec.mounts if m.source == str(main_repo))
        assert repo_mount.read_only is False
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
        """Normal session without worktrees: project_dir and wt_base mounted."""
        main_repo = tmp_path / "repo"
        main_repo.mkdir()
        wt_base = tmp_path / "worktrees" / "abc123"  # Does not exist yet
        project_dir = main_repo

        config = Config()
        with self._mock_worktree(main_repo, wt_base):
            spec = build_container_spec(config, project_dir, ["bash"])

        sources = [m.source for m in spec.mounts]
        assert str(project_dir) in sources
        # wt_base is now always created and mounted
        assert str(wt_base) in sources
        assert wt_base.exists()


class TestActiveToolScoping:
    """Tests for active_tool mount and env scoping."""

    def test_active_tool_mounts_applied(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """active_tool set: tool's mounts are applied."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".aider").mkdir(parents=True)

        config = Config()
        config.active_tool = "claude"
        config.tools = {
            "claude": ToolConfig(mounts=["~/.claude"]),
            "aider": ToolConfig(mounts=["~/.aider"]),
        }

        with patch.dict(os.environ, {"HOME": str(home)}):
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
        home = tmp_path / "home"
        (home / ".claude" / "ide").mkdir(parents=True)

        config = Config()
        config.active_tool = "claude"
        config.tools = {
            "claude": ToolConfig(mounts=["~/.claude/ide:ro"]),
        }

        with patch.dict(os.environ, {"HOME": str(home)}):
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
        """Default config produces cap_drop/cap_add in spec."""
        config = Config()
        spec = build_container_spec(config, tmp_path, ["bash"])

        assert spec.cap_drop == ["ALL"]
        assert "CHOWN" in spec.cap_add
        assert "KILL" in spec.cap_add
        assert spec.seccomp_profile is None

    def test_custom_seccomp_profile(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Custom seccomp profile path is passed to spec."""
        config = Config(
            security=SecuritySettings(
                cap_drop=["ALL"],
                cap_add=["CHOWN"],
                seccomp_profile="/etc/yaas/seccomp.json",
            ),
        )
        spec = build_container_spec(config, tmp_path, ["bash"])
        assert spec.seccomp_profile == "/etc/yaas/seccomp.json"

    def test_empty_cap_lists_no_flags(
        self, mock_linux, clean_env, tmp_path: Path
    ) -> None:
        """Empty cap lists mean no cap flags generated."""
        config = Config(
            security=SecuritySettings(cap_drop=[], cap_add=[]),
        )
        spec = build_container_spec(config, tmp_path, ["bash"])
        assert spec.cap_drop == []
        assert spec.cap_add == []

    def test_tool_security_override(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Tool security override is reflected in ContainerSpec."""
        config = Config(
            active_tool="claude",
            tools={
                "claude": ToolConfig(
                    security=SecuritySettings(cap_add=["CHOWN", "NET_RAW"]),
                )
            },
        )
        spec = build_container_spec(config, tmp_path, ["bash"])
        assert spec.cap_add == ["CHOWN", "NET_RAW"]

    def test_claude_tool_sets_is_sandbox(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """Claude tool env includes IS_SANDBOX=1 for sandbox-aware root check bypass."""
        config = Config(
            active_tool="claude",
            tools={
                "claude": ToolConfig(env={"IS_SANDBOX": "1"}),
            },
        )
        spec = build_container_spec(config, tmp_path, ["bash"])
        assert spec.environment.get("IS_SANDBOX") == "1"


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


# ============================================================
# No-project mode tests
# ============================================================


class TestNoProjectMode:
    """Tests for building container specs without a project directory."""

    def test_no_project_working_dir(self, mock_linux, clean_env) -> None:
        """When project_dir is None, working_dir is sandbox home."""
        config = Config()
        spec = build_container_spec(config, None, ["bash"])
        assert spec.working_dir == "/home"

    def test_no_project_skips_project_mount(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """When project_dir is None, no project directory is mounted."""
        config = Config()
        spec = build_container_spec(config, None, ["bash"])

        bind_sources = [m.source for m in spec.mounts if m.type == "bind"]
        # No project-like bind mounts (only optional config mounts)
        for source in bind_sources:
            assert not source.startswith(str(tmp_path))

    def test_no_project_omits_project_path_env(self, mock_linux, clean_env) -> None:
        """When project_dir is None, PROJECT_PATH is not set."""
        config = Config()
        spec = build_container_spec(config, None, ["bash"])
        assert "PROJECT_PATH" not in spec.environment

    def test_no_project_omits_mise_trusted_paths(self, mock_linux, clean_env) -> None:
        """When project_dir is None, MISE_TRUSTED_CONFIG_PATHS is not set."""
        config = Config()
        spec = build_container_spec(config, None, ["bash"])
        assert "MISE_TRUSTED_CONFIG_PATHS" not in spec.environment

    def test_no_project_still_has_home_volume(self, mock_linux, clean_env) -> None:
        """When project_dir is None, home volume is still mounted."""
        config = Config()
        spec = build_container_spec(config, None, ["bash"])
        home_mount = next(
            (m for m in spec.mounts if m.target == "/home" and m.type == "volume"), None
        )
        assert home_mount is not None

    def test_no_project_user_mounts_applied(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """When project_dir is None, user-defined mounts are still applied."""
        mount_src = tmp_path / "data"
        mount_src.mkdir()
        config = Config(mounts=[f"{mount_src}:/data"])
        spec = build_container_spec(config, None, ["bash"])

        data_mount = next((m for m in spec.mounts if m.target == "/data"), None)
        assert data_mount is not None
        assert data_mount.source == str(mount_src)

    def test_with_project_has_project_path_env(self, mock_linux, clean_env, tmp_path: Path) -> None:
        """When project_dir is set, PROJECT_PATH and MISE_TRUSTED_CONFIG_PATHS are set."""
        config = Config()
        spec = build_container_spec(config, tmp_path, ["bash"])
        assert spec.environment["PROJECT_PATH"] == str(tmp_path)
        assert spec.environment["MISE_TRUSTED_CONFIG_PATHS"] == str(tmp_path)


class TestBuildPreamble:
    """Tests for _build_preamble function."""

    def test_basic_preamble(self) -> None:
        """Preamble includes sandbox identification."""
        config = Config()
        preamble = _build_preamble(config, Path("/projects/myapp"), [])
        assert "YAAS" in preamble
        assert "sandbox" in preamble.lower()

    def test_resource_limits_shown(self) -> None:
        """Preamble includes configured resource limits."""
        config = Config()
        config.resources = ResourceLimits(memory="8g", cpus=2.0, pids_limit=1000)
        preamble = _build_preamble(config, Path("/projects/myapp"), [])
        assert "8g" in preamble
        assert "2.0" in preamble
        assert "1000" in preamble

    def test_unlimited_when_no_limits(self) -> None:
        """Preamble shows 'unlimited' when no resource limits are set."""
        config = Config()
        preamble = _build_preamble(config, Path("/projects/myapp"), [])
        assert preamble.count("unlimited") == 3  # memory, cpu, pids

    def test_network_mode(self) -> None:
        """Preamble includes network mode."""
        config = Config(network_mode="none")
        preamble = _build_preamble(config, Path("/projects/myapp"), [])
        assert "none" in preamble

    def test_project_path_readwrite(self) -> None:
        """Preamble shows project path with read-write mode."""
        config = Config()
        preamble = _build_preamble(config, Path("/projects/myapp"), [])
        assert "/projects/myapp" in preamble
        assert "read-write" in preamble

    def test_project_path_readonly(self) -> None:
        """Preamble shows project path with read-only mode."""
        config = Config(readonly_project=True)
        preamble = _build_preamble(config, Path("/projects/myapp"), [])
        assert "read-only" in preamble

    def test_no_project(self) -> None:
        """Preamble indicates when no project is mounted."""
        config = Config()
        preamble = _build_preamble(config, None, [])
        assert "no project" in preamble.lower()

    def test_bind_mounts_listed(self) -> None:
        """Preamble lists bind mount targets with read-only labels."""
        config = Config()
        mounts = [
            Mount("/home/user/.claude", "/home/.claude"),
            Mount("/home/user/.ssh/known_hosts", "/etc/ssh/ssh_known_hosts", read_only=True),
            Mount("yaas-home", "/home", type="volume"),
        ]
        preamble = _build_preamble(config, Path("/projects/myapp"), mounts)
        assert "/home/.claude" in preamble
        assert "/etc/ssh/ssh_known_hosts (read-only)" in preamble

    def test_volume_mounts_listed(self) -> None:
        """Preamble lists volume mount targets separately."""
        config = Config()
        mounts = [
            Mount("yaas-home", "/home", type="volume"),
            Mount("yaas-nix", "/nix", type="volume"),
        ]
        preamble = _build_preamble(config, Path("/projects/myapp"), mounts)
        assert "- /home" in preamble
        assert "- /nix" in preamble
        assert "persistent" in preamble.lower()

    def test_caution_note(self) -> None:
        """Preamble includes caution about mounted user files."""
        config = Config()
        preamble = _build_preamble(config, Path("/projects/myapp"), [])
        assert "caution" in preamble.lower()

    def test_preamble_in_environment(self, mock_linux, project_dir, clean_env) -> None:
        """YAAS_PREAMBLE env var is set in container spec."""
        config = Config()
        spec = build_container_spec(config, project_dir, ["bash"])
        assert "YAAS_PREAMBLE" in spec.environment
        assert "YAAS" in spec.environment["YAAS_PREAMBLE"]

    def test_preamble_disabled(self, mock_linux, project_dir, clean_env) -> None:
        """YAAS_PREAMBLE env var is not set when preamble is disabled."""
        config = Config(preamble=False)
        spec = build_container_spec(config, project_dir, ["bash"])
        assert "YAAS_PREAMBLE" not in spec.environment
