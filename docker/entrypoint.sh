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

# Auto-install tools on first run (if shims directory is empty)
SHIMS_DIR="$MISE_DATA_DIR/shims"
if [[ ! -d "$SHIMS_DIR" ]] || [[ -z "$(ls -A "$SHIMS_DIR" 2>/dev/null)" ]]; then
    echo "First run detected - installing mise tools..."
    mise install --yes || echo "Warning: Some tools failed to install"
fi

# Activate mise - this adds shims to PATH and loads env vars from [env] section
eval "$(mise activate bash)"

exec "$@"
