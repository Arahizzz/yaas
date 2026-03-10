"""Base runtime class with shared implementation for Podman and Docker."""

from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Config

from ..logging import get_logger
from .types import ContainerSpec, ExecSpec, _format_mount

logger = get_logger()


class BaseRuntime(ABC):
    """Abstract base class with shared container runtime logic.

    Subclasses must implement:
    - name (class attribute)
    - command_prefix (property)
    - is_available()
    - _add_runtime_specific_flags() — runtime-specific setup (e.g. userns)
    - _add_user_flags() — UID/GID passthrough mechanism
    """

    name: str

    @property
    @abstractmethod
    def command_prefix(self) -> list[str]:
        """Command prefix for invoking the runtime."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this runtime is available."""
        ...

    def adjust_config(self, config: Config) -> None:
        """Adjust config for runtime compatibility. Default: no-op."""
        pass

    @abstractmethod
    def _add_runtime_specific_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        """Add runtime-specific flags (e.g. userns for Podman). Called first."""
        ...

    @abstractmethod
    def _add_user_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        """Add UID/GID passthrough flags. Called after tty/stdin."""
        ...

    # ------------------------------------------------------------------
    # Shared command building
    # ------------------------------------------------------------------

    def _build_common_flags(self, cmd: list[str], spec: ContainerSpec) -> None:
        """Append flags shared between run and create commands."""
        self._add_runtime_specific_flags(cmd, spec)

        # Disable SELinux label confinement (needed for bind mounts)
        cmd.extend(["--security-opt", "label=disable"])

        # Interactive/TTY
        if spec.tty:
            cmd.append("-t")
        if spec.stdin_open:
            cmd.append("-i")

        self._add_user_flags(cmd, spec)

        # Container name
        if spec.name:
            cmd.extend(["--name", spec.name])

        # Working directory
        cmd.extend(["--workdir", spec.working_dir])

        # Network
        if spec.network_mode:
            cmd.extend(["--network", spec.network_mode])

        # Port publishing
        if spec.ports:
            for port in spec.ports:
                cmd.extend(["-p", port])

        # PID namespace
        if spec.pid_mode:
            cmd.extend(["--pid", spec.pid_mode])

        # Init (tini/catatonit as PID 1)
        if spec.init:
            cmd.append("--init")

        # Labels
        for key, value in spec.labels.items():
            cmd.extend(["--label", f"{key}={value}"])

        # Entrypoint override
        if spec.entrypoint is not None:
            cmd.extend(["--entrypoint", json.dumps(spec.entrypoint)])

        # Runtime identifier
        cmd.extend(["-e", f"YAAS_RUNTIME={self.name}"])

        # Environment
        for key, value in spec.environment.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Mounts
        for m in spec.mounts:
            cmd.extend(["--mount", _format_mount(m)])

        # Resource limits
        if spec.memory:
            cmd.extend(["--memory", spec.memory])
            swap = spec.memory_swap or spec.memory
            cmd.extend(["--memory-swap", swap])

        if spec.cpus:
            cmd.extend(["--cpus", str(spec.cpus)])

        if spec.pids_limit:
            cmd.extend(["--pids-limit", str(spec.pids_limit)])

        # Devices
        if spec.devices:
            for device in spec.devices:
                cmd.extend(["--device", device])

        # Security
        if spec.privileged:
            cmd.append("--privileged")
        else:
            if spec.capabilities is not None:
                cmd.extend(["--cap-drop", "ALL"])
                for cap in spec.capabilities:
                    cmd.extend(["--cap-add", cap])
            if spec.seccomp_profile:
                cmd.extend(["--security-opt", f"seccomp={spec.seccomp_profile}"])

    def _build_command(self, spec: ContainerSpec) -> list[str]:
        cmd = [*self.command_prefix, "run", "--rm"]
        self._build_common_flags(cmd, spec)
        cmd.append(spec.image)
        cmd.extend(spec.command)
        return cmd

    def _build_create_command(self, spec: ContainerSpec) -> list[str]:
        cmd = [*self.command_prefix, "create"]
        self._build_common_flags(cmd, spec)
        cmd.append(spec.image)
        cmd.extend(spec.command)
        return cmd

    def _build_exec_command(self, spec: ExecSpec) -> list[str]:
        cmd = [*self.command_prefix, "exec"]
        if spec.tty:
            cmd.append("-t")
        if spec.stdin_open:
            cmd.append("-i")
        if spec.working_dir:
            cmd.extend(["--workdir", spec.working_dir])
        if spec.user:
            cmd.extend(["--user", spec.user])
        for key, value in spec.environment.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.append(spec.container_name)
        cmd.extend(spec.command)
        return cmd

    # ------------------------------------------------------------------
    # Shared lifecycle methods
    # ------------------------------------------------------------------

    def run(self, spec: ContainerSpec) -> int:
        cmd = self._build_command(spec)
        result = subprocess.run(cmd)
        return result.returncode

    def create_volume(self, name: str) -> bool:
        """Create a named volume. Returns True on success."""
        result = subprocess.run(
            [*self.command_prefix, "volume", "create", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.debug(f"Failed to create volume {name}: {result.stderr}")
        return result.returncode == 0

    def remove_volume(self, name: str) -> bool:
        """Remove a named volume. Returns True on success."""
        result = subprocess.run(
            [*self.command_prefix, "volume", "rm", "-f", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.debug(f"Failed to remove volume {name}: {result.stderr}")
        return result.returncode == 0

    def create_container(self, spec: ContainerSpec) -> bool:
        cmd = self._build_create_command(spec)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning(f"Failed to create container: {result.stderr.strip()}")
        return result.returncode == 0

    def start_container(self, name: str) -> bool:
        result = subprocess.run(
            [*self.command_prefix, "start", name], capture_output=True, text=True
        )
        if result.returncode != 0:
            logger.warning(f"Failed to start container {name}: {result.stderr.strip()}")
        return result.returncode == 0

    def stop_container(self, name: str) -> bool:
        result = subprocess.run(
            [*self.command_prefix, "stop", name], capture_output=True, text=True
        )
        if result.returncode != 0:
            logger.warning(f"Failed to stop container {name}: {result.stderr.strip()}")
        return result.returncode == 0

    def remove_container(self, name: str, force: bool = False) -> bool:
        cmd = [*self.command_prefix, "rm"]
        if force:
            cmd.append("-f")
        cmd.append(name)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning(f"Failed to remove container {name}: {result.stderr.strip()}")
        return result.returncode == 0

    def exec_container(self, spec: ExecSpec) -> int:
        cmd = self._build_exec_command(spec)
        result = subprocess.run(cmd)
        return result.returncode

    def list_containers(self, prefix: str) -> list[dict[str, Any]]:
        result = subprocess.run(
            [
                *self.command_prefix,
                "ps",
                "-a",
                "--filter",
                f"name={prefix}",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        try:
            # Podman outputs one JSON object per line; Docker outputs a JSON array
            lines = result.stdout.strip().splitlines()
            containers: list[dict[str, Any]] = []
            for line in lines:
                if line.strip():
                    parsed = json.loads(line)
                    if isinstance(parsed, list):
                        containers.extend(parsed)
                    else:
                        containers.append(parsed)
            return containers
        except json.JSONDecodeError:
            return []

    def inspect_container(self, name: str) -> dict[str, Any] | None:
        result = subprocess.run(
            [*self.command_prefix, "inspect", name], capture_output=True, text=True
        )
        if result.returncode != 0:
            return None
        try:
            data = json.loads(result.stdout)
            result_data: dict[str, Any] = data[0] if isinstance(data, list) and data else data
            return result_data
        except json.JSONDecodeError:
            return None
