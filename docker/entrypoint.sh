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
# Nix setup
# ============================================================
# Fix ownership for UID passthrough (image builds as root, runtime uses host UID).
# Single-user Nix requires the running user to own /nix. The guard ensures
# the expensive recursive chown only runs once per volume lifecycle.
if [[ -d /nix/store && "$(stat -c '%u' /nix/store)" != "$(id -u)" ]]; then
    sudo chown -R "$(id -u):$(id -g)" /nix 2>/dev/null || true
fi

# Also fix $HOME ownership so Nix doesn't warn about unowned home directory
if [[ "$(stat -c '%u' "$HOME")" != "$(id -u)" ]]; then
    sudo chown "$(id -u):$(id -g)" "$HOME" 2>/dev/null || true
fi

# Source profile to add nix to PATH
NIX_PROFILE="/nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh"
if [[ -f "$NIX_PROFILE" ]]; then
    . "$NIX_PROFILE"
fi

# ============================================================
# Mise setup (MISE_YES=1 auto-confirms trust prompts)
# ============================================================
# Note: mise upgrade is now run in a separate container by yaas before
# the interactive container starts. This allows output to be captured
# in the startup UI panel.

# Ensure mise-nix plugin is installed (plugin dir lives in persistent volume)
if [[ ! -d "${MISE_DATA_DIR}/plugins/nix" ]]; then
    mise plugin install nix https://github.com/jbadeau/mise-nix.git 2>/dev/null || true
fi

# Activate mise - this adds shims to PATH and loads env vars from [env] section
eval "$(mise activate bash)"

exec "$@"
