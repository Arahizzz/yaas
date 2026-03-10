"""Podman with libkrun MicroVM isolation."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Config

from ..logging import get_logger
from .podman import PodmanRuntime
from .types import ContainerSpec

logger = get_logger()


class PodmanKrunRuntime(PodmanRuntime):
    """Podman with libkrun MicroVM isolation.

    Uses crun's krun handler to run containers inside lightweight VMs (KVM).
    Requires crun-krun package (provides krun binary + libkrun.so).
    """

    name = "podman-krun"

    def is_available(self) -> bool:
        return super().is_available() and shutil.which("krun") is not None

    # Features incompatible with libkrun MicroVMs (host sockets, FUSE mounts, etc.)
    _INCOMPATIBLE_FEATURES: dict[str, str] = {
        "lxcfs": "lxcfs (not needed, VM has its own /proc)",
        "clipboard": "clipboard passthrough (virtiofs can't forward Unix sockets)",
        "ssh_agent": "SSH agent forwarding (virtiofs can't forward Unix sockets)",
    }

    def adjust_config(self, config: Config) -> None:
        for feat, description in self._INCOMPATIBLE_FEATURES.items():
            if getattr(config, feat):
                logger.warning(f"{description} is not supported with libkrun — disabling")
                setattr(config, feat, False)
        if config.network_mode == "host":
            logger.warning("--network host is not supported with libkrun — falling back to bridge")
            config.network_mode = "bridge"
        if config.security.capabilities is not None:
            logger.warning("capability restrictions are not supported with libkrun — disabling")
            config.security.capabilities = None

    def _inject_krun_flags(self, cmd: list[str], spec: ContainerSpec) -> list[str]:
        """Insert krun-specific flags before the image argument."""
        image_idx = cmd.index(spec.image)
        krun_flags: list[str] = [
            "-e",
            "NIX_CONFIG=substitute = true",
            "--annotation=run.oci.handler=krun",
        ]
        cmd[image_idx:image_idx] = krun_flags
        # Strip --init (VM has its own init)
        if "--init" in cmd:
            cmd.remove("--init")
        return cmd

    def _build_command(self, spec: ContainerSpec) -> list[str]:
        cmd = super()._build_command(spec)
        return self._inject_krun_flags(cmd, spec)

    def _build_create_command(self, spec: ContainerSpec) -> list[str]:
        cmd = super()._build_create_command(spec)
        return self._inject_krun_flags(cmd, spec)
