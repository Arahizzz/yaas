"""Tests for container runtime."""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from tests.helpers import make_spec, mock_docker_socket, mock_which
from yaas.runtime import DockerRuntime, Mount, PodmanRuntime

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
        assert "--user" in cmd
        assert "1000:1000" in cmd
        assert "--memory" in cmd
        assert "8g" in cmd
        assert "--cpus" in cmd
        assert "2.0" in cmd
        assert "test:latest" in cmd
        assert "echo" in cmd
        assert "hello" in cmd

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
