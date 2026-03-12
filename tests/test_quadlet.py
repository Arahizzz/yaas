"""Tests for quadlet .container file generation."""

from __future__ import annotations

from yaas.quadlet import generate_quadlet
from yaas.runtime import ContainerSpec, Mount


def _make_spec(**overrides: object) -> ContainerSpec:
    """Create a minimal ContainerSpec with sensible box defaults."""
    defaults: dict[str, object] = {
        "image": "ghcr.io/arahizzz/yaas/runtime:latest",
        "command": ["sleep", "infinity"],
        "working_dir": "/home",
        "user": "1000:1000",
        "environment": {
            "HOME": "/home",
            "YAAS": "1",
            "YAAS_HOST_UID": "1000",
            "YAAS_HOST_GID": "1000",
            "YAAS_RUNTIME": "podman",
        },
        "mounts": [],
        "network_mode": "host",
        "tty": False,
        "stdin_open": False,
        "name": "yaas-box-test",
        "init": True,
        "labels": {"yaas.box.spec": "shell"},
    }
    defaults.update(overrides)
    return ContainerSpec(**defaults)  # type: ignore[arg-type]


class TestGenerateQuadlet:
    def test_basic_structure(self) -> None:
        spec = _make_spec()
        output = generate_quadlet(spec)

        assert "[Unit]" in output
        assert "[Container]" in output
        assert "[Service]" in output
        assert "[Install]" in output

    def test_unit_description(self) -> None:
        spec = _make_spec(name="yaas-box-dev")
        output = generate_quadlet(spec)
        assert "Description=YAAS box: yaas-box-dev" in output

    def test_image_and_name(self) -> None:
        spec = _make_spec()
        output = generate_quadlet(spec)
        assert "Image=ghcr.io/arahizzz/yaas/runtime:latest" in output
        assert "ContainerName=yaas-box-test" in output

    def test_entrypoint(self) -> None:
        spec = _make_spec(entrypoint=["sleep", "infinity"])
        output = generate_quadlet(spec)
        assert "Entrypoint=sleep infinity" in output

    def test_entrypoint_with_special_chars(self) -> None:
        spec = _make_spec(entrypoint=["/bin/sh", "-c", "echo hello world"])
        output = generate_quadlet(spec)
        assert "Entrypoint=/bin/sh -c 'echo hello world'" in output

    def test_exec_with_default_command(self) -> None:
        spec = _make_spec()
        output = generate_quadlet(spec)
        assert "Exec=sleep infinity" in output

    def test_no_exec_when_empty_command(self) -> None:
        spec = _make_spec(command=[])
        output = generate_quadlet(spec)
        assert "Exec=" not in output

    def test_exec_with_command(self) -> None:
        spec = _make_spec(command=["bash", "-c", "echo hi"])
        output = generate_quadlet(spec)
        assert "Exec=bash -c 'echo hi'" in output

    def test_working_dir(self) -> None:
        spec = _make_spec(working_dir="/project")
        output = generate_quadlet(spec)
        assert "WorkingDir=/project" in output

    def test_security_label_disable(self) -> None:
        spec = _make_spec()
        output = generate_quadlet(spec)
        assert "SecurityLabelDisable=true" in output

    def test_environment_variables(self) -> None:
        spec = _make_spec(environment={"FOO": "bar", "BAZ": "qux"})
        output = generate_quadlet(spec)
        assert "Environment=FOO=bar" in output
        assert "Environment=BAZ=qux" in output

    def test_bind_mounts(self) -> None:
        mounts = [
            Mount(source="/host/path", target="/container/path"),
            Mount(source="/host/ro", target="/container/ro", read_only=True),
        ]
        spec = _make_spec(mounts=mounts)
        output = generate_quadlet(spec)
        assert "Mount=type=bind,source=/host/path,target=/container/path" in output
        assert "Mount=type=bind,source=/host/ro,target=/container/ro,readonly" in output

    def test_volume_mounts(self) -> None:
        mounts = [
            Mount(source="yaas-home", target="/home", type="volume"),
            Mount(source="yaas-nix", target="/nix", type="volume", read_only=True),
        ]
        spec = _make_spec(mounts=mounts)
        output = generate_quadlet(spec)
        assert "Volume=yaas-home:/home" in output
        assert "Volume=yaas-nix:/nix:ro" in output

    def test_tmpfs_mounts(self) -> None:
        mounts = [Mount(source="", target="/tmp", type="tmpfs")]
        spec = _make_spec(mounts=mounts)
        output = generate_quadlet(spec)
        assert "Mount=type=tmpfs,target=/tmp" in output

    def test_network_mode(self) -> None:
        spec = _make_spec(network_mode="host")
        output = generate_quadlet(spec)
        assert "Network=host" in output

    def test_no_network_when_none(self) -> None:
        spec = _make_spec(network_mode=None)
        output = generate_quadlet(spec)
        assert "Network=" not in output

    def test_ports(self) -> None:
        spec = _make_spec(ports=["8080:8080", "3000:3000"])
        output = generate_quadlet(spec)
        assert "PublishPort=8080:8080" in output
        assert "PublishPort=3000:3000" in output

    def test_devices(self) -> None:
        spec = _make_spec(devices=["/dev/fuse"])
        output = generate_quadlet(spec)
        assert "AddDevice=/dev/fuse" in output

    def test_labels(self) -> None:
        spec = _make_spec(labels={"yaas.box.spec": "shell", "custom": "value"})
        output = generate_quadlet(spec)
        assert "Label=yaas.box.spec=shell" in output
        assert "Label=custom=value" in output

    def test_capabilities(self) -> None:
        spec = _make_spec(cap_drop=["ALL"], cap_add=["NET_BIND_SERVICE", "CHOWN"])
        output = generate_quadlet(spec)
        assert "DropCapability=ALL" in output
        assert "AddCapability=NET_BIND_SERVICE" in output
        assert "AddCapability=CHOWN" in output

    def test_no_capabilities_when_empty(self) -> None:
        spec = _make_spec()
        output = generate_quadlet(spec)
        assert "DropCapability=" not in output
        assert "AddCapability=" not in output

    def test_pids_limit(self) -> None:
        spec = _make_spec(pids_limit=1000)
        output = generate_quadlet(spec)
        assert "PidsLimit=1000" in output

    def test_keep_groups(self) -> None:
        spec = _make_spec(keep_groups=True)
        output = generate_quadlet(spec)
        assert "GroupAdd=keep-groups" in output

    def test_no_keep_groups(self) -> None:
        spec = _make_spec(keep_groups=False)
        output = generate_quadlet(spec)
        assert "GroupAdd" not in output

    def test_init_in_podman_args(self) -> None:
        spec = _make_spec(init=True)
        output = generate_quadlet(spec)
        assert "PodmanArgs=" in output
        assert "--init" in output

    def test_memory_in_podman_args(self) -> None:
        spec = _make_spec(memory="8g")
        output = generate_quadlet(spec)
        assert "--memory 8g" in output
        assert "--memory-swap 8g" in output

    def test_memory_with_swap(self) -> None:
        spec = _make_spec(memory="8g", memory_swap="12g")
        output = generate_quadlet(spec)
        assert "--memory 8g" in output
        assert "--memory-swap 12g" in output

    def test_cpus_in_podman_args(self) -> None:
        spec = _make_spec(cpus=2.0)
        output = generate_quadlet(spec)
        assert "--cpus 2.0" in output

    def test_pid_mode_in_podman_args(self) -> None:
        spec = _make_spec(pid_mode="host")
        output = generate_quadlet(spec)
        assert "--pid host" in output

    def test_privileged_in_podman_args(self) -> None:
        spec = _make_spec(privileged=True)
        output = generate_quadlet(spec)
        assert "--privileged" in output

    def test_seccomp_in_podman_args(self) -> None:
        spec = _make_spec(seccomp_profile="/path/to/profile.json")
        output = generate_quadlet(spec)
        assert "--security-opt seccomp=/path/to/profile.json" in output

    def test_no_podman_args_when_empty(self) -> None:
        spec = _make_spec(init=False, memory=None, cpus=None, pid_mode=None, privileged=False)
        output = generate_quadlet(spec)
        assert "PodmanArgs=" not in output

    def test_service_section(self) -> None:
        spec = _make_spec()
        output = generate_quadlet(spec)
        assert "Restart=on-failure" in output
        assert "TimeoutStartSec=900" in output

    def test_install_section(self) -> None:
        spec = _make_spec()
        output = generate_quadlet(spec)
        assert "WantedBy=default.target" in output
