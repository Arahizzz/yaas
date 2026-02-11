#!/bin/bash
set -e

# Add local bin to PATH if not already there
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

# ============================================================
# XDG runtime directory setup
# ============================================================
# Create /run/user/$UID for GPG agent sockets (if not exists)
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
if [[ ! -d "$XDG_RUNTIME_DIR" ]]; then
    mkdir -p "$XDG_RUNTIME_DIR"
    chmod 700 "$XDG_RUNTIME_DIR"
fi
# Fix ownership if the container runtime created the directory as root
# (happens when bind-mounting sockets into /run/user/$UID/)
if [[ "$(stat -c %u "$XDG_RUNTIME_DIR")" != "$(id -u)" ]]; then
    sudo chown "$(id -u):$(id -g)" "$XDG_RUNTIME_DIR"
fi

# ============================================================
# Mise setup (MISE_YES=1 auto-confirms trust prompts)
# ============================================================
# Note: mise upgrade is now run in a separate container by yaas before
# the interactive container starts. This allows output to be captured
# in the startup UI panel.

# Activate mise - this adds shims to PATH and loads env vars from [env] section
eval "$(mise activate bash)"

exec "$@"
