#!/bin/bash
# Runtime setup for krun MicroVM containers.
# Sourced by entrypoint.sh — sets SHELL_UID/SHELL_GID to fake (spoofed) values.
#
# In krun, the VM boots as real root (UID 0). Rootless podman maps container
# UID 0 → host UID, so file ownership is correct. We create passwd/group
# entries for the fake UID/GID and stage LD_PRELOAD so the user's command
# sees the spoofed identity.

SHELL_UID="${YAAS_FAKE_UID:-1000}"
SHELL_GID="${YAAS_FAKE_GID:-1000}"

# Stage LD_PRELOAD (applied at exec time, not during setup — sudo must see real UID 0)
YAAS_LD_PRELOAD=/usr/local/lib/libfakeuid.so
