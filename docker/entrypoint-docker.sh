#!/bin/bash
# Runtime setup for Docker containers.
# Sourced by entrypoint.sh — sets SHELL_UID/SHELL_GID and optionally YAAS_SETPRIV.
#
# Rootful Docker: UID 0 inside = real root. Entrypoint runs setup as root,
# then setpriv drops to the target UID at exec time.
#
# Rootless Docker: like rootless Podman, container UID 0 maps to the host user's UID.
# Process stays UID 0 throughout.

if [[ "${YAAS_DOCKER_ROOTFUL:-}" == "1" ]]; then
    SHELL_UID="$HOST_UID"
    SHELL_GID="$HOST_GID"
    YAAS_SETPRIV="setpriv --reuid=${SHELL_UID} --regid=${SHELL_GID} --init-groups --"
else
    SHELL_UID=0
    SHELL_GID=0
fi
