#!/bin/bash
# Runtime setup for krun MicroVM containers.
# Sourced by entrypoint.sh.
#
# In krun, the VM boots as real root (UID 0). Rootless podman maps container
# UID 0 → host UID, so file ownership is correct.
# UID spoofing (LD_PRELOAD) is controlled per-tool via YAAS_SPOOF_UID.
