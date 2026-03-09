"""Tests for container runtime."""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from tests.helpers import make_config, make_spec, mock_docker_socket, mock_which
from yaas.runtime import DockerRuntime, Mount, PodmanKrunRuntime, PodmanRuntime

# ============================================================
# Mount and ContainerSpec dataclass tests
# ============================================================


class TestMount:
    """Tests for Mount dataclass."""

    def test_defaults(self) -> None:
        """Test Mount dataclass defaults."""
        mount = Mount(source="/host/path", target="/container/path")

        assert mount.source == "/host/path"
        assert mount.target == "/container/path"
        assert mount.type == "bind"
        assert mount.read_only is False

    def test_readonly(self) -> None:
        """Test Mount with readonly flag."""
        mount = Mount(source="/host", target="/container", read_only=True)

        assert mount.read_only is True


class TestContainerSpec:
    """Tests for ContainerSpec dataclass."""

    def test_defaults(self) -> None:
        """Test ContainerSpec dataclass defaults."""
        spec = make_spec(environment={"FOO": "bar"})

        assert spec.image == "test:latest"
        assert spec.command == ["bash"]
        assert spec.memory is None
        assert spec.cpus is None
        assert spec.pids_limit is None


# ============================================================
# PodmanRuntime tests
# ============================================================


class TestPodmanRuntime:
    """Tests for PodmanRuntime."""

    def test_build_command(self) -> None:
        """Test PodmanRuntime command building."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = make_spec(
                command=["echo", "hello"],
                environment={"FOO": "bar"},
                mounts=[Mount(source="/host", target="/container")],
                memory="8g",
                cpus=2.0,
            )
            cmd = runtime._build_command(spec)

        assert cmd[0] == "podman"
        assert "run" in cmd
        assert "--rm" in cmd
        assert "-t" in cmd
        assert "-i" in cmd
        # Podman uses LD_PRELOAD UID spoofing, not --user or --userns=keep-id
        assert "--user" not in cmd
        assert "--userns=keep-id" not in cmd
        assert "YAAS_HOST_UID=1000" in cmd
        assert "YAAS_HOST_GID=1000" in cmd
        assert "--memory" in cmd
        assert "8g" in cmd
        assert "--cpus" in cmd
        assert "2.0" in cmd
        assert "test:latest" in cmd
        assert "echo" in cmd
        assert "hello" in cmd

    def test_injects_yaas_runtime_env(self) -> None:
        """Test PodmanRuntime injects YAAS_RUNTIME=podman."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = make_spec()
            cmd = runtime._build_command(spec)

        assert "YAAS_RUNTIME=podman" in cmd
        idx = cmd.index("YAAS_RUNTIME=podman")
        assert cmd[idx - 1] == "-e"

    def test_command_prefix(self) -> None:
        """Test PodmanRuntime command_prefix returns podman."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanRuntime()
            assert runtime.command_prefix == ["podman"]

    def test_not_available_on_non_linux(self) -> None:
        """Test PodmanRuntime is not available on non-Linux platforms."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.runtime.is_linux", return_value=False))
            stack.enter_context(mock_which({"podman": "/usr/local/bin/podman"}))
            runtime = PodmanRuntime()
            assert runtime.is_available() is False

    def test_create_volume_success(self) -> None:
        """Test create_volume returns True on success."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.runtime.is_linux", return_value=True))
            mock_run = stack.enter_context(patch("subprocess.run"))
            mock_run.return_value = MagicMock(returncode=0)

            runtime = PodmanRuntime()
            result = runtime.create_volume("test-volume")

            assert result is True
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args == ["podman", "volume", "create", "test-volume"]

    def test_create_volume_failure(self) -> None:
        """Test create_volume returns False on failure."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.runtime.is_linux", return_value=True))
            mock_run = stack.enter_context(patch("subprocess.run"))
            mock_run.return_value = MagicMock(returncode=1, stderr="error message")

            runtime = PodmanRuntime()
            result = runtime.create_volume("test-volume")

            assert result is False

    def test_remove_volume_success(self) -> None:
        """Test remove_volume returns True on success."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.runtime.is_linux", return_value=True))
            mock_run = stack.enter_context(patch("subprocess.run"))
            mock_run.return_value = MagicMock(returncode=0)

            runtime = PodmanRuntime()
            result = runtime.remove_volume("test-volume")

            assert result is True
            args = mock_run.call_args[0][0]
            assert args == ["podman", "volume", "rm", "-f", "test-volume"]

    def test_remove_volume_failure(self) -> None:
        """Test remove_volume returns False on failure."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.runtime.is_linux", return_value=True))
            mock_run = stack.enter_context(patch("subprocess.run"))
            mock_run.return_value = MagicMock(returncode=1, stderr="error message")

            runtime = PodmanRuntime()
            result = runtime.remove_volume("test-volume")

            assert result is False


# ============================================================
# PodmanKrunRuntime tests
# ============================================================


class TestPodmanKrunRuntime:
    """Tests for PodmanKrunRuntime."""

    def test_build_command_has_annotation(self) -> None:
        """Test that krun annotation is added before image name."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanKrunRuntime()
            spec = make_spec(command=["echo", "hello"])
            cmd = runtime._build_command(spec)

        assert "--annotation=run.oci.handler=krun" in cmd
        # Annotation must appear before the image
        ann_idx = cmd.index("--annotation=run.oci.handler=krun")
        img_idx = cmd.index("test:latest")
        assert ann_idx < img_idx

    def test_omits_userns_and_user_flags(self) -> None:
        """Test that krun omits --userns and --user (VM boots as root)."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanKrunRuntime()
            spec = make_spec()
            cmd = runtime._build_command(spec)

        assert "--userns=keep-id" not in cmd
        assert "--user" not in cmd
        assert "podman" == cmd[0]

    def test_passes_runtime_and_host_uid_env_vars(self) -> None:
        """Test that krun injects YAAS_RUNTIME and YAAS_HOST_UID/GID."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanKrunRuntime()
            spec = make_spec(user="1000:1000")
            cmd = runtime._build_command(spec)

        assert "YAAS_RUNTIME=podman-krun" in cmd
        assert "YAAS_HOST_UID=1000" in cmd
        assert "YAAS_HOST_GID=1000" in cmd
        uid_idx = cmd.index("YAAS_HOST_UID=1000")
        gid_idx = cmd.index("YAAS_HOST_GID=1000")
        assert cmd[uid_idx - 1] == "-e"
        assert cmd[gid_idx - 1] == "-e"

    def test_forces_nix_substituters(self) -> None:
        """Test that krun injects NIX_CONFIG to force substituters online."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanKrunRuntime()
            spec = make_spec()
            cmd = runtime._build_command(spec)

        assert "NIX_CONFIG=substitute = true" in cmd
        nix_idx = cmd.index("NIX_CONFIG=substitute = true")
        assert cmd[nix_idx - 1] == "-e"

    def test_available_with_krun(self) -> None:
        """Test is_available when both podman and krun are present."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.runtime.is_linux", return_value=True))
            stack.enter_context(
                mock_which({"podman": "/usr/bin/podman", "krun": "/usr/bin/krun"})
            )
            runtime = PodmanKrunRuntime()
            assert runtime.is_available() is True

    def test_not_available_without_krun(self) -> None:
        """Test is_available when krun binary is missing."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.runtime.is_linux", return_value=True))
            stack.enter_context(mock_which({"podman": "/usr/bin/podman", "krun": None}))
            runtime = PodmanKrunRuntime()
            assert runtime.is_available() is False

    def test_not_available_on_non_linux(self) -> None:
        """Test is_available on non-Linux platforms."""
        with ExitStack() as stack:
            stack.enter_context(patch("yaas.runtime.is_linux", return_value=False))
            stack.enter_context(
                mock_which({"podman": "/usr/bin/podman", "krun": "/usr/bin/krun"})
            )
            runtime = PodmanKrunRuntime()
            assert runtime.is_available() is False

    def test_adjust_config_disables_lxcfs(self) -> None:
        """Test that adjust_config disables lxcfs for MicroVM compatibility."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanKrunRuntime()
        config = make_config(lxcfs=True)
        runtime.adjust_config(config)
        assert config.lxcfs is False

    def test_adjust_config_noop_when_lxcfs_disabled(self) -> None:
        """Test that adjust_config is a no-op when lxcfs is already disabled."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanKrunRuntime()
        config = make_config(lxcfs=False)
        runtime.adjust_config(config)
        assert config.lxcfs is False

    def test_adjust_config_disables_network_host(self) -> None:
        """Test that adjust_config falls back from host to bridge networking."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanKrunRuntime()
        config = make_config(network_mode="host")
        runtime.adjust_config(config)
        assert config.network_mode == "bridge"

    def test_adjust_config_preserves_bridge_network(self) -> None:
        """Test that adjust_config leaves bridge networking unchanged."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanKrunRuntime()
        config = make_config(network_mode="bridge")
        runtime.adjust_config(config)
        assert config.network_mode == "bridge"

    def test_adjust_config_disables_capabilities(self) -> None:
        """Test that adjust_config clears capability restrictions for MicroVM."""
        from yaas.config import SecuritySettings

        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanKrunRuntime()
        config = make_config(
            security=SecuritySettings(capabilities=["CHOWN", "DAC_OVERRIDE"]),
        )
        runtime.adjust_config(config)
        assert config.security.capabilities is None

    def test_adjust_config_noop_when_no_capabilities(self) -> None:
        """Test that adjust_config is a no-op when capabilities are already None."""
        from yaas.config import SecuritySettings

        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanKrunRuntime()
        config = make_config(security=SecuritySettings(capabilities=None))
        runtime.adjust_config(config)
        assert config.security.capabilities is None



# ============================================================
# DockerRuntime tests
# ============================================================


class TestDockerRuntime:
    """Tests for DockerRuntime."""

    def test_build_command(self) -> None:
        """Test DockerRuntime command building."""
        with mock_docker_socket(accessible=True):
            runtime = DockerRuntime()

        spec = make_spec(network_mode="none", tty=False, stdin_open=False)
        cmd = runtime._build_command(spec)

        assert cmd[0] == "docker"
        assert "--network" in cmd
        assert "none" in cmd
        assert "-t" not in cmd
        assert "-i" not in cmd

    def test_command_prefix_without_sudo(self) -> None:
        """Test DockerRuntime command_prefix when socket is accessible."""
        with mock_docker_socket(accessible=True):
            runtime = DockerRuntime()

        assert runtime.command_prefix == ["docker"]
        assert runtime._use_sudo is False

    def test_command_prefix_with_sudo(self) -> None:
        """Test DockerRuntime command_prefix when socket not accessible."""
        with ExitStack() as stack:
            stack.enter_context(mock_docker_socket(accessible=False))
            stack.enter_context(mock_which({"sudo": "/usr/bin/sudo", "docker": None}))
            runtime = DockerRuntime()

        assert runtime.command_prefix == ["sudo", "docker"]
        assert runtime._use_sudo is True

    def test_available_with_socket_access(self) -> None:
        """Test DockerRuntime is available when socket is accessible."""
        with ExitStack() as stack:
            stack.enter_context(mock_docker_socket(accessible=True))
            stack.enter_context(mock_which({"docker": "/usr/bin/docker"}))
            runtime = DockerRuntime()
            assert runtime.is_available() is True

    def test_available_with_sudo(self) -> None:
        """Test DockerRuntime is available when using sudo."""
        with ExitStack() as stack:
            stack.enter_context(mock_docker_socket(accessible=False))
            stack.enter_context(mock_which({"docker": "/usr/bin/docker", "sudo": "/usr/bin/sudo"}))
            runtime = DockerRuntime()
            assert runtime.is_available() is True
            assert runtime._use_sudo is True

    def test_not_available_without_access(self) -> None:
        """Test DockerRuntime is not available when no socket access and no sudo."""
        with ExitStack() as stack:
            stack.enter_context(mock_docker_socket(accessible=False))
            stack.enter_context(mock_which({"docker": "/usr/bin/docker", "sudo": None}))
            runtime = DockerRuntime()
            assert runtime.is_available() is False

    def test_injects_yaas_runtime_env(self) -> None:
        """Test DockerRuntime injects YAAS_RUNTIME=docker."""
        with mock_docker_socket(accessible=True):
            runtime = DockerRuntime()

        spec = make_spec()
        cmd = runtime._build_command(spec)

        assert "YAAS_RUNTIME=docker" in cmd
        idx = cmd.index("YAAS_RUNTIME=docker")
        assert cmd[idx - 1] == "-e"

    def test_rootful_uses_gosu_env(self) -> None:
        """Test rootful Docker passes YAAS_DOCKER_ROOTFUL for gosu privilege drop."""
        with mock_docker_socket(accessible=True):
            runtime = DockerRuntime()
        runtime._rootless = False

        spec = make_spec()
        cmd = runtime._build_command(spec)

        assert "--user" not in cmd
        assert "YAAS_HOST_UID=1000" in cmd
        assert "YAAS_HOST_GID=1000" in cmd
        assert "YAAS_DOCKER_ROOTFUL=1" in cmd

    def test_rootless_passes_host_uid(self) -> None:
        """Test rootless Docker passes YAAS_HOST_UID/GID without YAAS_DOCKER_ROOTFUL."""
        with mock_docker_socket(accessible=True):
            runtime = DockerRuntime()
        runtime._rootless = True

        spec = make_spec()
        cmd = runtime._build_command(spec)

        assert "--user" not in cmd
        assert "YAAS_HOST_UID=1000" in cmd
        assert "YAAS_HOST_GID=1000" in cmd
        assert "YAAS_DOCKER_ROOTFUL=1" not in cmd

    def test_build_command_with_sudo(self) -> None:
        """Test DockerRuntime command building when using sudo."""
        with ExitStack() as stack:
            stack.enter_context(mock_docker_socket(accessible=False))
            stack.enter_context(mock_which({"docker": "/usr/bin/docker", "sudo": "/usr/bin/sudo"}))
            runtime = DockerRuntime()

        spec = make_spec()
        cmd = runtime._build_command(spec)

        assert cmd[0] == "sudo"
        assert cmd[1] == "docker"
        assert "run" in cmd

    def test_create_volume_success(self) -> None:
        """Test create_volume returns True on success."""
        with ExitStack() as stack:
            stack.enter_context(mock_docker_socket(accessible=True))
            mock_run = stack.enter_context(patch("subprocess.run"))
            mock_run.return_value = MagicMock(returncode=0)

            runtime = DockerRuntime()
            result = runtime.create_volume("test-volume")

            assert result is True
            args = mock_run.call_args[0][0]
            assert args == ["docker", "volume", "create", "test-volume"]

    def test_create_volume_with_sudo(self) -> None:
        """Test create_volume uses sudo when needed."""
        with ExitStack() as stack:
            stack.enter_context(mock_docker_socket(accessible=False))
            stack.enter_context(mock_which({"docker": "/usr/bin/docker", "sudo": "/usr/bin/sudo"}))
            mock_run = stack.enter_context(patch("subprocess.run"))
            mock_run.return_value = MagicMock(returncode=0)

            runtime = DockerRuntime()
            result = runtime.create_volume("test-volume")

            assert result is True
            args = mock_run.call_args[0][0]
            assert args == ["sudo", "docker", "volume", "create", "test-volume"]

    def test_remove_volume_success(self) -> None:
        """Test remove_volume returns True on success."""
        with ExitStack() as stack:
            stack.enter_context(mock_docker_socket(accessible=True))
            mock_run = stack.enter_context(patch("subprocess.run"))
            mock_run.return_value = MagicMock(returncode=0)

            runtime = DockerRuntime()
            result = runtime.remove_volume("test-volume")

            assert result is True
            args = mock_run.call_args[0][0]
            assert args == ["docker", "volume", "rm", "-f", "test-volume"]


# ============================================================
# UID spoofing tests
# ============================================================


class TestSpoofUid:
    """Tests for per-tool spoof_uid flag."""

    def test_podman_spoof_uid_passes_env(self) -> None:
        """Test Podman passes YAAS_SPOOF_UID=1 when spoof_uid is true."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = make_spec(spoof_uid=True)
            cmd = runtime._build_command(spec)

        assert "YAAS_SPOOF_UID=1" in cmd

    def test_podman_no_spoof_uid_by_default(self) -> None:
        """Test Podman does not pass YAAS_SPOOF_UID when spoof_uid is false."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = make_spec(spoof_uid=False)
            cmd = runtime._build_command(spec)

        assert "YAAS_SPOOF_UID=1" not in cmd

    def test_docker_spoof_uid_passes_env(self) -> None:
        """Test Docker passes YAAS_SPOOF_UID=1 when spoof_uid is true."""
        with mock_docker_socket(accessible=True):
            runtime = DockerRuntime()

        spec = make_spec(spoof_uid=True)
        cmd = runtime._build_command(spec)

        assert "YAAS_SPOOF_UID=1" in cmd

    def test_docker_no_spoof_uid_by_default(self) -> None:
        """Test Docker does not pass YAAS_SPOOF_UID when spoof_uid is false."""
        with mock_docker_socket(accessible=True):
            runtime = DockerRuntime()

        spec = make_spec(spoof_uid=False)
        cmd = runtime._build_command(spec)

        assert "YAAS_SPOOF_UID=1" not in cmd

    def test_host_uid_always_passed(self) -> None:
        """Test YAAS_HOST_UID/GID are always passed regardless of spoof_uid."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = make_spec(spoof_uid=False, user="1000:1000")
            cmd = runtime._build_command(spec)

        assert "YAAS_HOST_UID=1000" in cmd
        assert "YAAS_HOST_GID=1000" in cmd
        assert "YAAS_SPOOF_UID=1" not in cmd


# ============================================================
# Security flag tests (shared by both runtimes)
# ============================================================


class TestSecurityFlags:
    """Tests for capability and seccomp CLI flag generation."""

    def test_podman_capabilities(self) -> None:
        """Test Podman generates --cap-drop ALL and --cap-add flags from capabilities list."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = make_spec(capabilities=["CHOWN", "KILL"])
            cmd = runtime._build_command(spec)

        assert "--cap-drop" in cmd
        assert cmd[cmd.index("--cap-drop") + 1] == "ALL"
        assert "--cap-add" in cmd
        cap_add_indices = [i for i, x in enumerate(cmd) if x == "--cap-add"]
        cap_add_values = [cmd[i + 1] for i in cap_add_indices]
        assert "CHOWN" in cap_add_values
        assert "KILL" in cap_add_values

    def test_docker_capabilities(self) -> None:
        """Test Docker generates --cap-drop ALL and --cap-add flags from capabilities list."""
        with mock_docker_socket(accessible=True):
            runtime = DockerRuntime()

        spec = make_spec(capabilities=["CHOWN", "KILL"])
        cmd = runtime._build_command(spec)

        assert "--cap-drop" in cmd
        assert cmd[cmd.index("--cap-drop") + 1] == "ALL"
        cap_add_indices = [i for i, x in enumerate(cmd) if x == "--cap-add"]
        cap_add_values = [cmd[i + 1] for i in cap_add_indices]
        assert "CHOWN" in cap_add_values
        assert "KILL" in cap_add_values

    def test_no_cap_flags_when_none(self) -> None:
        """Test that no cap flags are generated when fields are None."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = make_spec()
            cmd = runtime._build_command(spec)

        assert "--cap-drop" not in cmd
        assert "--cap-add" not in cmd

    def test_podman_seccomp_profile(self) -> None:
        """Test Podman generates --security-opt seccomp= flag."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = make_spec(seccomp_profile="/path/to/profile.json")
            cmd = runtime._build_command(spec)

        assert "--security-opt" in cmd
        # Podman also has label=disable as first --security-opt, find the seccomp one
        seccomp_opts = [
            cmd[i + 1]
            for i, x in enumerate(cmd)
            if x == "--security-opt" and cmd[i + 1].startswith("seccomp=")
        ]
        assert len(seccomp_opts) == 1
        assert seccomp_opts[0] == "seccomp=/path/to/profile.json"

    def test_docker_seccomp_profile(self) -> None:
        """Test Docker generates --security-opt seccomp= flag."""
        with mock_docker_socket(accessible=True):
            runtime = DockerRuntime()

        spec = make_spec(seccomp_profile="/path/to/profile.json")
        cmd = runtime._build_command(spec)

        seccomp_opts = [
            cmd[i + 1]
            for i, x in enumerate(cmd)
            if x == "--security-opt" and cmd[i + 1].startswith("seccomp=")
        ]
        assert len(seccomp_opts) == 1
        assert seccomp_opts[0] == "seccomp=/path/to/profile.json"

    def test_no_seccomp_flag_when_none(self) -> None:
        """Test that no seccomp flag is generated when profile is None."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = make_spec()
            cmd = runtime._build_command(spec)

        seccomp_opts = [
            cmd[i + 1]
            for i, x in enumerate(cmd)
            if x == "--security-opt" and cmd[i + 1].startswith("seccomp=")
        ]
        assert len(seccomp_opts) == 0


# ============================================================
# Port publishing tests
# ============================================================


class TestPortPublishing:
    """Tests for port publishing CLI flag generation."""

    def test_podman_ports(self) -> None:
        """Test Podman generates -p flags from ports list."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = make_spec(ports=["8080:8080", "3000:3000"])
            cmd = runtime._build_command(spec)

        port_indices = [i for i, x in enumerate(cmd) if x == "-p"]
        port_values = [cmd[i + 1] for i in port_indices]
        assert "8080:8080" in port_values
        assert "3000:3000" in port_values

    def test_docker_ports(self) -> None:
        """Test Docker generates -p flags from ports list."""
        with mock_docker_socket(accessible=True):
            runtime = DockerRuntime()

        spec = make_spec(ports=["8080:8080"])
        cmd = runtime._build_command(spec)

        assert "-p" in cmd
        assert cmd[cmd.index("-p") + 1] == "8080:8080"

    def test_no_port_flags_when_none(self) -> None:
        """Test that no port flags are generated when ports is None."""
        with patch("yaas.runtime.is_linux", return_value=True):
            runtime = PodmanRuntime()
            spec = make_spec()
            cmd = runtime._build_command(spec)

        assert "-p" not in cmd
