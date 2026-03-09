#!/bin/bash
# Runtime setup for Podman containers.
# Sourced by entrypoint.sh.
#
# Rootless podman always creates a user namespace: container UID 0 maps to the
# host user's UID. Process stays UID 0 throughout.
SHELL_UID=0
SHELL_GID=0
