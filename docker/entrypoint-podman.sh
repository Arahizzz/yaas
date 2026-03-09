#!/bin/bash
# Runtime setup for Podman containers.
# Sourced by entrypoint.sh.
#
# Rootless podman always creates a user namespace: container UID 0 maps to the
# host user's UID. File ownership on bind mounts is correct.
# UID spoofing (LD_PRELOAD) is controlled per-tool via YAAS_SPOOF_UID.
