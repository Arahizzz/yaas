#!/bin/bash
# Runtime setup for standard containers (podman/docker).
# Sourced by entrypoint.sh — sets SHELL_UID/SHELL_GID from actual process identity.

SHELL_UID=$(id -u)
SHELL_GID=$(id -g)
