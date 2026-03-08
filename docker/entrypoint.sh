#!/bin/bash
set -e

# ============================================================
# Runtime-specific setup (sets SHELL_UID, SHELL_GID, optionally YAAS_LD_PRELOAD)
# ============================================================
case "${YAAS_RUNTIME}" in
    podman|docker) source /entrypoint-container.sh ;;
    podman-krun)   source /entrypoint-krun.sh ;;
    *)             echo "Unknown YAAS_RUNTIME: ${YAAS_RUNTIME:-<unset>}" >&2; exit 1 ;;
esac

# ============================================================
# User setup (create passwd/group entry for SHELL_UID/SHELL_GID)
# ============================================================
if ! getent group "$SHELL_GID" >/dev/null 2>&1; then
    sudo groupadd -g "$SHELL_GID" yaas 2>/dev/null || true
fi

if ! getent passwd "$SHELL_UID" >/dev/null 2>&1; then
    sudo useradd -u "$SHELL_UID" -g "$SHELL_GID" -d /home -s /bin/bash -M yaas 2>/dev/null || true
else
    # Fix home directory for users auto-created by --userns=keep-id
    # (podman sets home to workdir; SSH uses getpwuid, not $HOME)
    sudo usermod -d /home "$(getent passwd "$SHELL_UID" | cut -d: -f1)" 2>/dev/null || true
fi

# ============================================================
# Common setup
# ============================================================

# Add local bin to PATH if not already there
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

# XDG runtime directory — use SHELL_UID (the identity the user's process will see)
export XDG_RUNTIME_DIR="/run/user/${SHELL_UID}"
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
# Use real UID (id -u), not SHELL_UID — chown needs the actual process identity.
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
# Ensure mise-nix plugin is installed (plugin dir lives in persistent volume)
if [[ ! -d "${MISE_DATA_DIR}/plugins/nix" ]]; then
    mise plugin install nix https://github.com/jbadeau/mise-nix.git 2>/dev/null || true
fi

# Activate mise — adds shims to PATH and loads env vars from [env] section
eval "$(mise activate bash)"

# ============================================================
# Exec
# ============================================================
# Expand environment variables in command arguments
# (e.g. $YAAS_PREAMBLE in tool commands like --append-system-prompt $YAAS_PREAMBLE)
args=()
for arg in "$@"; do
    args+=("$(envsubst <<< "$arg")")
done

# Apply LD_PRELOAD for krun: only the user's command sees spoofed UID.
# Setuid binaries (sudo) strip LD_PRELOAD, so they see real UID 0.
if [[ -n "${YAAS_LD_PRELOAD:-}" ]]; then
    export LD_PRELOAD="${YAAS_LD_PRELOAD}"
fi
exec "${args[@]}"
