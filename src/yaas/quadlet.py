"""Generate Podman quadlet .container files from ContainerSpec."""

from __future__ import annotations

import shlex

from .runtime.types import ContainerSpec, _format_mount


def generate_quadlet(spec: ContainerSpec) -> str:
    """Generate a Podman quadlet .container file from a ContainerSpec.

    The spec should already have runtime-specific env vars (YAAS_HOST_UID,
    YAAS_HOST_GID, YAAS_RUNTIME) injected before calling this function.

    Returns the complete .container file content as a string.
    """
    lines: list[str] = []

    # --- [Unit] ---
    lines.append("[Unit]")
    desc = f"YAAS box: {spec.name}" if spec.name else "YAAS box"
    lines.append(f"Description={desc}")
    lines.append("")

    # --- [Container] ---
    lines.append("[Container]")
    lines.append(f"Image={spec.image}")

    if spec.name:
        lines.append(f"ContainerName={spec.name}")

    if spec.entrypoint:
        lines.append(f"Entrypoint={shlex.join(spec.entrypoint)}")

    if spec.command:
        lines.append(f"Exec={shlex.join(spec.command)}")

    lines.append(f"WorkingDir={spec.working_dir}")
    lines.append("SecurityLabelDisable=true")

    # Network
    if spec.network_mode:
        lines.append(f"Network={spec.network_mode}")

    # Ports
    if spec.ports:
        for port in spec.ports:
            lines.append(f"PublishPort={port}")

    # Devices
    if spec.devices:
        for device in spec.devices:
            lines.append(f"AddDevice={device}")

    # Labels
    for key, value in spec.labels.items():
        lines.append(f"Label={key}={value}")

    # Environment
    for key, value in spec.environment.items():
        lines.append(f"Environment={key}={value}")

    # Mounts
    for m in spec.mounts:
        if m.type == "volume":
            vol = f"{m.source}:{m.target}"
            if m.read_only:
                vol += ":ro"
            lines.append(f"Volume={vol}")
        else:
            lines.append(f"Mount={_format_mount(m)}")

    # Security
    for cap in spec.cap_drop:
        lines.append(f"DropCapability={cap}")
    for cap in spec.cap_add:
        lines.append(f"AddCapability={cap}")

    # PID limit (native quadlet directive)
    if spec.pids_limit:
        lines.append(f"PidsLimit={spec.pids_limit}")

    # Supplementary groups
    if spec.groups:
        for gid in spec.groups:
            lines.append(f"GroupAdd={gid}")

    # PodmanArgs for flags without native quadlet directives
    podman_args: list[str] = []

    if spec.init:
        podman_args.append("--init")

    if spec.memory:
        podman_args.extend(["--memory", spec.memory])
        swap = spec.memory_swap or spec.memory
        podman_args.extend(["--memory-swap", swap])

    if spec.cpus:
        podman_args.extend(["--cpus", str(spec.cpus)])

    if spec.pid_mode:
        podman_args.extend(["--pid", spec.pid_mode])

    if spec.privileged:
        podman_args.append("--privileged")

    if spec.seccomp_profile:
        podman_args.extend(["--security-opt", f"seccomp={spec.seccomp_profile}"])

    if podman_args:
        lines.append(f"PodmanArgs={shlex.join(podman_args)}")

    lines.append("")

    # --- [Service] ---
    lines.append("[Service]")
    lines.append("Restart=on-failure")
    lines.append("TimeoutStartSec=900")
    lines.append("")

    # --- [Install] ---
    lines.append("[Install]")
    lines.append("WantedBy=default.target")
    lines.append("")

    return "\n".join(lines)
