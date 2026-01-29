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

# ============================================================
# Mise setup (MISE_YES=1 auto-confirms trust prompts)
# ============================================================

# Run mise upgrade if enabled (controlled by YAAS_AUTO_UPGRADE_TOOLS env var)
if [[ "${YAAS_AUTO_UPGRADE_TOOLS:-true}" == "true" ]]; then
    mise upgrade --yes || echo "Warning: Some tools failed to install/upgrade"
fi

# Activate mise - this adds shims to PATH and loads env vars from [env] section
eval "$(mise activate bash)"

exec "$@"
