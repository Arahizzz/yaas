"""Tests for container runtime."""

from unittest.mock import patch

from yaas.runtime import ContainerSpec, Mount, PodmanRuntime, DockerRuntime


def test_mount_dataclass() -> None:
    """Test Mount dataclass defaults."""
    mount = Mount(source="/host/path", target="/container/path")

    assert mount.source == "/host/path"
    assert mount.target == "/container/path"
    assert mount.type == "bind"
    assert mount.read_only is False


def test_mount_readonly() -> None:
    """Test Mount with readonly flag."""
    mount = Mount(source="/host", target="/container", read_only=True)

    assert mount.read_only is True


def test_container_spec_defaults() -> None:
    """Test ContainerSpec dataclass defaults."""
    spec = ContainerSpec(
        image="test:latest",
        command=["bash"],
        working_dir="/workspace",
        user="1000:1000",
        environment={"FOO": "bar"},
        mounts=[],
        network_mode=None,
        tty=True,
        stdin_open=True,
    )

    assert spec.image == "test:latest"
    assert spec.command == ["bash"]
    assert spec.memory is None
    assert spec.cpus is None
    assert spec.pids_limit is None


def test_podman_build_command() -> None:
    """Test PodmanRuntime command building."""
    runtime = PodmanRuntime()
    spec = ContainerSpec(
        image="test:latest",
        command=["echo", "hello"],
        working_dir="/workspace",
        user="1000:1000",
        environment={"FOO": "bar"},
        mounts=[Mount(source="/host", target="/container")],
        network_mode=None,
        tty=True,
        stdin_open=True,
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


def test_docker_build_command() -> None:
    """Test DockerRuntime command building."""
    with patch("yaas.runtime._can_access_docker_socket", return_value=True):
        runtime = DockerRuntime()
    spec = ContainerSpec(
        image="test:latest",
        command=["bash"],
        working_dir="/workspace",
        user="1000:1000",
        environment={},
        mounts=[],
        network_mode="none",
        tty=False,
        stdin_open=False,
    )

    cmd = runtime._build_command(spec)

    assert cmd[0] == "docker"
    assert "--network" in cmd
    assert "none" in cmd
    assert "-t" not in cmd
    assert "-i" not in cmd


def test_podman_command_prefix() -> None:
    """Test PodmanRuntime command_prefix returns podman."""
    runtime = PodmanRuntime()
    assert runtime.command_prefix == ["podman"]


def test_docker_command_prefix_without_sudo() -> None:
    """Test DockerRuntime command_prefix when socket is accessible."""
    with patch("yaas.runtime._can_access_docker_socket", return_value=True):
        runtime = DockerRuntime()

    assert runtime.command_prefix == ["docker"]
    assert runtime._use_sudo is False


def test_docker_command_prefix_with_sudo() -> None:
    """Test DockerRuntime command_prefix when socket not accessible but sudo available."""
    with patch("yaas.runtime._can_access_docker_socket", return_value=False):
        with patch("yaas.runtime.shutil.which", side_effect=lambda x: "/usr/bin/sudo" if x == "sudo" else None):
            runtime = DockerRuntime()

    assert runtime.command_prefix == ["sudo", "docker"]
    assert runtime._use_sudo is True


def test_docker_is_available_with_socket_access() -> None:
    """Test DockerRuntime is available when socket is accessible."""
    with patch("yaas.runtime._can_access_docker_socket", return_value=True):
        with patch("yaas.runtime.shutil.which", return_value="/usr/bin/docker"):
            runtime = DockerRuntime()
            assert runtime.is_available() is True


def test_docker_is_available_with_sudo() -> None:
    """Test DockerRuntime is available when using sudo."""
    with patch("yaas.runtime._can_access_docker_socket", return_value=False):
        with patch("yaas.runtime.shutil.which", side_effect=lambda x: "/usr/bin/" + x if x in ("docker", "sudo") else None):
            runtime = DockerRuntime()
            assert runtime.is_available() is True
            assert runtime._use_sudo is True


def test_docker_not_available_without_access() -> None:
    """Test DockerRuntime is not available when no socket access and no sudo."""
    with patch("yaas.runtime._can_access_docker_socket", return_value=False):
        with patch("yaas.runtime.shutil.which", side_effect=lambda x: "/usr/bin/docker" if x == "docker" else None):
            runtime = DockerRuntime()
            assert runtime.is_available() is False


def test_docker_build_command_with_sudo() -> None:
    """Test DockerRuntime command building when using sudo."""
    with patch("yaas.runtime._can_access_docker_socket", return_value=False):
        with patch("yaas.runtime.shutil.which", side_effect=lambda x: "/usr/bin/" + x if x in ("docker", "sudo") else None):
            runtime = DockerRuntime()

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

    assert cmd[0] == "sudo"
    assert cmd[1] == "docker"
    assert "run" in cmd
