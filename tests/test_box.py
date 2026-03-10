"""Tests for box (persistent container) functionality."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from yaas.config import (
    BoxSpec,
    Config,
    ResourceLimits,
    _merge_toml,
    load_config,
    resolve_box_config,
)
from yaas.constants import BOX_CONTAINER_PREFIX, RUNTIME_IMAGE
from yaas.container import build_box_spec
from yaas.runtime import ContainerSpec, ExecSpec, PodmanRuntime


@pytest.fixture(autouse=True)
def _isolate_global_config(tmp_path: Path) -> None:
    """Prevent tests from reading or auto-creating the real global config."""
    fake_config_path = tmp_path / "nonexistent" / "config.toml"
    with (
        patch("yaas.config.GLOBAL_CONFIG_PATH", fake_config_path),
        patch("yaas.config._ensure_global_config"),
    ):
        yield  # type: ignore[misc]


# ============================================================
# BoxSpec dataclass
# ============================================================


class TestBoxSpec:
    """Tests for BoxSpec dataclass."""

    def test_defaults(self) -> None:
        box = BoxSpec()
        assert box.command == []
        assert box.shell is None
        assert box.base is None
        assert box.ssh_agent is None
        assert box.git_config is None

    def test_custom_fields(self) -> None:
        box = BoxSpec(
            command=["sleep", "infinity"],
            shell=["zsh"],
            ssh_agent=True,
        )
        assert box.command == ["sleep", "infinity"]
        assert box.shell == ["zsh"]
        assert box.ssh_agent is True


# ============================================================
# Config box parsing (_merge_boxes via TOML)
# ============================================================


def _load_toml(toml: str) -> Config:
    with TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        (project_dir / ".yaas.toml").write_text(toml)
        return load_config(project_dir)


class TestBoxConfigParsing:
    """Tests for box config loading from TOML."""

    def test_basic_box_spec(self) -> None:
        config = _load_toml("[box.shell]\nssh_agent = true\ngit_config = true")
        assert "shell" in config.boxes
        assert config.boxes["shell"].ssh_agent is True
        assert config.boxes["shell"].git_config is True

    def test_box_with_command(self) -> None:
        config = _load_toml('[box.custom]\ncommand = ["tail", "-f", "/dev/null"]\nshell = ["zsh"]')
        box = config.boxes["custom"]
        assert box.command == ["tail", "-f", "/dev/null"]
        assert box.shell == ["zsh"]

    def test_box_with_base(self) -> None:
        config = _load_toml('[box.hardened]\nbase = "none"\nnetwork_mode = "none"')
        box = config.boxes["hardened"]
        assert box.base == "none"
        assert box.network_mode == "none"

    def test_box_with_resources(self) -> None:
        config = _load_toml('[box.heavy]\n[box.heavy.resources]\nmemory = "16g"\ncpus = 4.0')
        box = config.boxes["heavy"]
        assert box.resources is not None
        assert box.resources.memory == "16g"
        assert box.resources.cpus == 4.0

    def test_box_with_env(self) -> None:
        config = _load_toml('[box.dev]\nenv = { MY_VAR = "value", TOKEN = true }')
        box = config.boxes["dev"]
        assert box.env == {"MY_VAR": "value", "TOKEN": True}

    def test_box_with_mounts(self) -> None:
        config = _load_toml('[box.dev]\nmounts = ["~/.config/app"]')
        assert config.boxes["dev"].mounts == ["~/.config/app"]

    def test_invalid_shell_skipped(self) -> None:
        """Non-list shell value causes the box spec to be skipped."""
        config = _load_toml('[box.bad]\nshell = "bash"')
        assert "bad" not in config.boxes

    def test_non_table_skipped(self) -> None:
        config = _load_toml('box = { bad = "string" }')
        assert "bad" not in config.boxes

    def test_multiple_boxes(self) -> None:
        config = _load_toml('[box.shell]\nssh_agent = true\n\n[box.hardened]\nbase = "none"')
        assert "shell" in config.boxes
        assert "hardened" in config.boxes

    def test_box_merge_global_and_project(self) -> None:
        """Project box config merges with global box config."""
        config = Config()
        with TemporaryDirectory() as tmpdir:
            global_path = Path(tmpdir) / "global.toml"
            global_path.write_text("[box.shell]\nssh_agent = true")
            project_path = Path(tmpdir) / "project.toml"
            project_path.write_text("[box.shell]\ngit_config = true")
            _merge_toml(config, global_path)
            _merge_toml(config, project_path)

        box = config.boxes["shell"]
        assert box.ssh_agent is True
        assert box.git_config is True

    def test_box_with_security(self) -> None:
        config = _load_toml(
            '[box.secure]\n[box.secure.security]\ncapabilities = ["CHOWN", "SETUID"]'
        )
        box = config.boxes["secure"]
        assert box.security is not None
        assert box.security.capabilities == ["CHOWN", "SETUID"]


# ============================================================
# resolve_box_config
# ============================================================


class TestResolveBoxConfig:
    """Tests for resolve_box_config function."""

    def test_default_no_project_mount(self) -> None:
        """Boxes default to mount_project=False."""
        config = Config(boxes={"shell": BoxSpec()})
        resolved = resolve_box_config(config, "shell")
        assert resolved.mount_project is False

    def test_explicit_mount_project(self) -> None:
        """Box spec can explicitly enable project mount."""
        config = Config(boxes={"dev": BoxSpec(mount_project=True)})
        resolved = resolve_box_config(config, "dev")
        assert resolved.mount_project is True

    def test_inherits_global_config(self) -> None:
        """Box with default base inherits global config."""
        config = Config(
            ssh_agent=True,
            network_mode="host",
            boxes={"shell": BoxSpec()},
        )
        resolved = resolve_box_config(config, "shell")
        assert resolved.ssh_agent is True
        assert resolved.network_mode == "host"

    def test_box_overrides_global(self) -> None:
        """Box spec overrides global config values."""
        config = Config(
            ssh_agent=True,
            network_mode="host",
            boxes={"secure": BoxSpec(ssh_agent=False, network_mode="none")},
        )
        resolved = resolve_box_config(config, "secure")
        assert resolved.ssh_agent is False
        assert resolved.network_mode == "none"

    def test_base_minimal(self) -> None:
        """base='minimal' starts from hardcoded defaults, ignoring global config."""
        config = Config(
            ssh_agent=True,
            network_mode="host",
            boxes={"minimal": BoxSpec(base="minimal")},
        )
        resolved = resolve_box_config(config, "minimal")
        # Should use Config() defaults, not global values
        assert resolved.ssh_agent is False
        assert resolved.network_mode == "bridge"

    def test_base_none(self) -> None:
        """base='none' starts with no caps and no network."""
        config = Config(
            ssh_agent=True,
            network_mode="host",
            boxes={"locked": BoxSpec(base="none")},
        )
        resolved = resolve_box_config(config, "locked")
        assert resolved.network_mode == "none"
        assert resolved.security.capabilities == []
        assert resolved.ssh_agent is False

    def test_base_none_with_overrides(self) -> None:
        """base='none' can still have explicit overrides from the box spec."""
        config = Config(
            boxes={"custom": BoxSpec(base="none", ssh_agent=True)},
        )
        resolved = resolve_box_config(config, "custom")
        assert resolved.network_mode == "none"  # from base=none
        assert resolved.ssh_agent is True  # explicit override

    def test_base_default(self) -> None:
        """base='default' behaves like omitted base (inherits global)."""
        config = Config(
            ssh_agent=True,
            boxes={"shell": BoxSpec(base="default")},
        )
        resolved = resolve_box_config(config, "shell")
        assert resolved.ssh_agent is True

    def test_missing_box_spec(self) -> None:
        """Resolving a missing box spec still works (defaults only)."""
        config = Config(ssh_agent=True)
        resolved = resolve_box_config(config, "nonexistent")
        assert resolved.mount_project is False
        assert resolved.ssh_agent is True

    def test_env_overlay(self) -> None:
        """Box env overlays on top of global env."""
        config = Config(
            env={"GLOBAL": "yes"},
            boxes={"dev": BoxSpec(env={"BOX_VAR": "val"})},
        )
        resolved = resolve_box_config(config, "dev")
        assert resolved.env == {"GLOBAL": "yes", "BOX_VAR": "val"}

    def test_resource_override(self) -> None:
        """Box resources override global resources at field level."""
        config = Config(
            resources=ResourceLimits(memory="8g", cpus=2.0),
            boxes={"heavy": BoxSpec(resources=ResourceLimits(memory="32g"))},
        )
        resolved = resolve_box_config(config, "heavy")
        assert resolved.resources.memory == "32g"
        assert resolved.resources.cpus == 2.0  # inherited


# ============================================================
# build_box_spec
# ============================================================


class TestBuildBoxSpec:
    """Tests for build_box_spec function."""

    def test_basic(self, mock_linux, clean_env) -> None:
        config = Config(boxes={"shell": BoxSpec()})
        spec = build_box_spec(config, "shell", "yaas-box-mybox")

        assert spec.image == RUNTIME_IMAGE
        assert spec.name == "yaas-box-mybox"
        assert spec.entrypoint is None
        assert spec.command == ["sleep", "infinity"]
        assert spec.init is True
        assert spec.tty is False
        assert spec.stdin_open is False
        assert spec.labels["yaas.box.spec"] == "shell"

    def test_custom_command(self, mock_linux, clean_env) -> None:
        config = Config(boxes={"custom": BoxSpec(command=["tail", "-f", "/dev/null"])})
        spec = build_box_spec(config, "custom", "yaas-box-test")
        assert spec.command == ["tail", "-f", "/dev/null"]

    def test_no_project_mount_by_default(self, mock_linux, clean_env) -> None:
        config = Config(boxes={"shell": BoxSpec()})
        spec = build_box_spec(config, "shell", "yaas-box-test")
        # Working dir should be /home (not a project dir)
        assert spec.working_dir == "/home"

    def test_shared_volumes_present_default(self, mock_linux, clean_env) -> None:
        """Default base includes shared volumes (home, nix)."""
        config = Config(boxes={"shell": BoxSpec()})
        spec = build_box_spec(config, "shell", "yaas-box-test")

        targets = [m.target for m in spec.mounts]
        assert "/home" in targets
        assert "/nix" in targets

    def test_base_none_skips_shared_volumes(self, mock_linux, clean_env) -> None:
        """base='none' skips shared volumes."""
        config = Config(boxes={"locked": BoxSpec(base="none")})
        spec = build_box_spec(config, "locked", "yaas-box-locked")

        volume_mounts = [m for m in spec.mounts if m.type == "volume"]
        volume_targets = [m.target for m in volume_mounts]
        assert "/home" not in volume_targets
        assert "/nix" not in volume_targets

    def test_base_none_no_network(self, mock_linux, clean_env) -> None:
        config = Config(boxes={"locked": BoxSpec(base="none")})
        spec = build_box_spec(config, "locked", "yaas-box-locked")
        assert spec.network_mode == "none"

    def test_base_none_empty_caps(self, mock_linux, clean_env) -> None:
        config = Config(boxes={"locked": BoxSpec(base="none")})
        spec = build_box_spec(config, "locked", "yaas-box-locked")
        assert spec.capabilities == []

    def test_security_passthrough(self, mock_linux, clean_env) -> None:
        """Default security settings are passed through."""
        config = Config(boxes={"shell": BoxSpec()})
        spec = build_box_spec(config, "shell", "yaas-box-test")
        assert spec.capabilities is not None
        assert "CHOWN" in spec.capabilities

    def test_resource_limits(self, mock_linux, clean_env) -> None:
        config = Config(
            resources=ResourceLimits(memory="8g", cpus=2.0),
            boxes={"shell": BoxSpec()},
        )
        spec = build_box_spec(config, "shell", "yaas-box-test")
        assert spec.memory == "8g"
        assert spec.cpus == 2.0


# ============================================================
# ExecSpec
# ============================================================


class TestExecSpec:
    """Tests for ExecSpec dataclass."""

    def test_defaults(self) -> None:
        spec = ExecSpec(container_name="yaas-box-test", command=["bash"])
        assert spec.container_name == "yaas-box-test"
        assert spec.command == ["bash"]
        assert spec.tty is True
        assert spec.stdin_open is True
        assert spec.working_dir is None
        assert spec.user is None
        assert spec.environment == {}

    def test_custom(self) -> None:
        spec = ExecSpec(
            container_name="yaas-box-test",
            command=["ls", "-la"],
            working_dir="/workspace",
            user="1000:1000",
            environment={"FOO": "bar"},
            tty=False,
        )
        assert spec.command == ["ls", "-la"]
        assert spec.working_dir == "/workspace"
        assert spec.user == "1000:1000"
        assert spec.tty is False


# ============================================================
# Runtime: create/exec command building
# ============================================================


class TestPodmanCreateCommand:
    """Tests for PodmanRuntime._build_create_command."""

    def test_create_has_no_rm(self) -> None:
        """Create command should NOT have --rm."""
        with patch("yaas.runtime.podman.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = ContainerSpec(
                image="test:latest",
                command=[],
                working_dir="/home",
                user="1000:1000",
                environment={},
                mounts=[],
                network_mode="host",
                tty=False,
                stdin_open=False,
                name="yaas-box-test",
                init=True,
            )
            cmd = runtime._build_create_command(spec)

        assert cmd[0] == "podman"
        assert "create" in cmd
        assert "--rm" not in cmd
        assert "--init" in cmd
        assert "--name" in cmd
        name_idx = cmd.index("--name")
        assert cmd[name_idx + 1] == "yaas-box-test"

    def test_create_with_labels(self) -> None:
        with patch("yaas.runtime.podman.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = ContainerSpec(
                image="test:latest",
                command=[],
                working_dir="/home",
                user="1000:1000",
                environment={},
                mounts=[],
                network_mode=None,
                tty=False,
                stdin_open=False,
                labels={"yaas.box.spec": "shell"},
            )
            cmd = runtime._build_create_command(spec)

        assert "--label" in cmd
        label_idx = cmd.index("--label")
        assert cmd[label_idx + 1] == "yaas.box.spec=shell"

    def test_create_with_entrypoint(self) -> None:
        with patch("yaas.runtime.podman.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = ContainerSpec(
                image="test:latest",
                command=[],
                working_dir="/home",
                user="1000:1000",
                environment={},
                mounts=[],
                network_mode=None,
                tty=False,
                stdin_open=False,
                entrypoint=["sleep", "infinity"],
            )
            cmd = runtime._build_create_command(spec)

        assert "--entrypoint" in cmd


class TestPodmanExecCommand:
    """Tests for PodmanRuntime._build_exec_command."""

    def test_basic_exec(self) -> None:
        with patch("yaas.runtime.podman.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = ExecSpec(
                container_name="yaas-box-test",
                command=["bash"],
                tty=True,
                stdin_open=True,
            )
            cmd = runtime._build_exec_command(spec)

        assert cmd[0] == "podman"
        assert "exec" in cmd
        assert "-t" in cmd
        assert "-i" in cmd
        assert "yaas-box-test" in cmd
        assert "bash" in cmd

    def test_exec_with_workdir(self) -> None:
        with patch("yaas.runtime.podman.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = ExecSpec(
                container_name="yaas-box-test",
                command=["ls"],
                working_dir="/workspace",
            )
            cmd = runtime._build_exec_command(spec)

        assert "--workdir" in cmd
        assert "/workspace" in cmd

    def test_exec_with_env(self) -> None:
        with patch("yaas.runtime.podman.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = ExecSpec(
                container_name="test",
                command=["env"],
                environment={"FOO": "bar"},
            )
            cmd = runtime._build_exec_command(spec)

        assert "-e" in cmd
        assert "FOO=bar" in cmd

    def test_exec_no_tty(self) -> None:
        with patch("yaas.runtime.podman.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = ExecSpec(
                container_name="test",
                command=["ls"],
                tty=False,
                stdin_open=False,
            )
            cmd = runtime._build_exec_command(spec)

        assert "-t" not in cmd
        assert "-i" not in cmd


# ============================================================
# Runtime: run command still works (regression)
# ============================================================


class TestRunCommandRegression:
    """Ensure refactored _build_command still produces correct output."""

    def test_run_has_rm(self) -> None:
        with patch("yaas.runtime.podman.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = ContainerSpec(
                image="test:latest",
                command=["bash"],
                working_dir="/workspace",
                user="1000:1000",
                environment={},
                mounts=[],
                network_mode=None,
                tty=True,
                stdin_open=True,
            )
            cmd = runtime._build_command(spec)

        assert "run" in cmd
        assert "--rm" in cmd

    def test_run_no_init_by_default(self) -> None:
        with patch("yaas.runtime.podman.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = ContainerSpec(
                image="test:latest",
                command=["bash"],
                working_dir="/workspace",
                user="1000:1000",
                environment={},
                mounts=[],
                network_mode=None,
                tty=True,
                stdin_open=True,
            )
            cmd = runtime._build_command(spec)

        assert "--init" not in cmd


# ============================================================
# KrunRuntime strips --init
# ============================================================


class TestKrunStipsInit:
    """Krun runtime strips --init (VM has its own init)."""

    def test_create_strips_init(self) -> None:
        from yaas.runtime import PodmanKrunRuntime

        with patch("yaas.runtime.podman.is_linux", return_value=True):
            runtime = PodmanKrunRuntime()
            spec = ContainerSpec(
                image="test:latest",
                command=[],
                working_dir="/home",
                user="1000:1000",
                environment={},
                mounts=[],
                network_mode=None,
                tty=False,
                stdin_open=False,
                init=True,
            )
            cmd = runtime._build_create_command(spec)

        assert "--init" not in cmd
        assert "--annotation=run.oci.handler=krun" in cmd


# ============================================================
# BOX_CONTAINER_PREFIX
# ============================================================


class TestBoxContainerPrefix:
    def test_prefix_value(self) -> None:
        assert BOX_CONTAINER_PREFIX == "yaas-box-"

    def test_container_name(self) -> None:
        name = f"{BOX_CONTAINER_PREFIX}mybox"
        assert name == "yaas-box-mybox"


# ============================================================
# Tool base field
# ============================================================


class TestToolBaseField:
    """Tests for base field on tools (resolve_effective_config)."""

    def test_tool_base_minimal(self) -> None:
        from yaas.config import ToolConfig, resolve_effective_config

        config = Config(
            ssh_agent=True,
            network_mode="host",
            active_tool="test",
            tools={"test": ToolConfig(base="minimal")},
        )
        resolved = resolve_effective_config(config)
        assert resolved.ssh_agent is False
        assert resolved.network_mode == "bridge"

    def test_tool_base_none(self) -> None:
        from yaas.config import ToolConfig, resolve_effective_config

        config = Config(
            ssh_agent=True,
            active_tool="test",
            tools={"test": ToolConfig(base="none")},
        )
        resolved = resolve_effective_config(config)
        assert resolved.network_mode == "none"
        assert resolved.security.capabilities == []

    def test_tool_base_default_inherits(self) -> None:
        from yaas.config import ToolConfig, resolve_effective_config

        config = Config(
            ssh_agent=True,
            active_tool="test",
            tools={"test": ToolConfig(base="default")},
        )
        resolved = resolve_effective_config(config)
        assert resolved.ssh_agent is True

    def test_tool_base_from_toml(self) -> None:
        config = _load_toml('[tools.hardened]\nbase = "minimal"\ncommand = ["bash"]')
        assert config.tools["hardened"].base == "minimal"
