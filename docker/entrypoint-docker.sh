#!/bin/bash
# Runtime setup for Docker containers.
# Sourced by entrypoint.sh — optionally stages YAAS_GOSU for privilege drop.
#
# Rootful Docker: UID 0 inside = real root. Entrypoint runs setup as root,
# then gosu drops to the target UID at exec time (security: don't run user
# processes as root).
#
# Rootless Docker: like rootless Podman, container UID 0 maps to the host user's UID.
# UID spoofing (LD_PRELOAD) is controlled per-tool via YAAS_SPOOF_UID.

if [[ "${YAAS_DOCKER_ROOTFUL:-}" == "1" ]]; then
    # Rootful Docker: gosu drops to host UID at exec time.
    YAAS_GOSU="gosu ${SHELL_UID}:${SHELL_GID}"
fi
