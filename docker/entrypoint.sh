#!/bin/bash
set -e

# ============================================================
# UID/GID setup
# ============================================================
# HOST_UID/HOST_GID: the host user's identity (passed by all runtimes).
# SHELL_UID/SHELL_GID: the UID/GID the main process actually runs as (set by runtime entrypoint).
#   - Podman/Docker rootless/krun: 0 (user namespace maps 0 → host UID)
#   - Docker rootful: HOST_UID (setpriv drops privileges at exec time)
HOST_UID="${YAAS_HOST_UID:-1000}"
HOST_GID="${YAAS_HOST_GID:-1000}"

# ============================================================
# Runtime-specific setup (sets SHELL_UID/SHELL_GID, optionally YAAS_SETPRIV)
# ============================================================
case "${YAAS_RUNTIME}" in
    podman)      source /opt/yaas/entrypoint-podman.sh ;;
    docker)      source /opt/yaas/entrypoint-docker.sh ;;
    podman-krun) source /opt/yaas/entrypoint-krun.sh ;;
    *)           echo "Unknown YAAS_RUNTIME: ${YAAS_RUNTIME:-<unset>}" >&2; exit 1 ;;
esac

# ============================================================
# User setup (Docker rootful only — setpriv drops to SHELL_UID, needs passwd entry)
# ============================================================
# Podman/rootless Docker/krun run as UID 0 (root), which already has a passwd entry.
if [[ "$SHELL_UID" != "0" ]]; then
    if ! getent group "$SHELL_GID" >/dev/null 2>&1; then
        sudo groupadd -g "$SHELL_GID" yaas 2>/dev/null || true
    fi

    if ! getent passwd "$SHELL_UID" >/dev/null 2>&1; then
        sudo useradd -u "$SHELL_UID" -g "$SHELL_GID" -d /home -s /bin/bash -M yaas 2>/dev/null || true
    else
        # Fix home directory for users with wrong home (SSH uses getpwuid, not $HOME)
        sudo usermod -d /home "$(getent passwd "$SHELL_UID" | cut -d: -f1)" 2>/dev/null || true
    fi
fi

# ============================================================
# Rootless Podman setup (subuid/subgid for user namespaces)
# ============================================================
# Nested podman needs subuid/subgid for the UID that will run it (SHELL_UID).
if ! grep -q "^${SHELL_UID}:" /etc/subuid 2>/dev/null; then
    echo "${SHELL_UID}:100000:65536" | sudo tee -a /etc/subuid >/dev/null
fi
if ! grep -q "^${SHELL_UID}:" /etc/subgid 2>/dev/null; then
    echo "${SHELL_UID}:100000:65536" | sudo tee -a /etc/subgid >/dev/null
fi

# ============================================================
# Common setup
# ============================================================

# Add local bin to PATH if not already there
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

# XDG runtime directory for the actual running UID
export XDG_RUNTIME_DIR="/run/user/${SHELL_UID}"
if [[ ! -d "$XDG_RUNTIME_DIR" ]]; then
    mkdir -p "$XDG_RUNTIME_DIR"
fi
if [[ "$(stat -c '%u' "$XDG_RUNTIME_DIR")" != "$SHELL_UID" ]]; then
    sudo chown "$SHELL_UID:$SHELL_GID" "$XDG_RUNTIME_DIR"
fi
chmod 700 "$XDG_RUNTIME_DIR"

# Clipboard socket fixup: host sockets are mounted under /run/host/,
# symlink them into the container's XDG_RUNTIME_DIR so wl-paste/xclip find them.
if [[ -n "${WAYLAND_DISPLAY:-}" && -e "/run/host/${WAYLAND_DISPLAY}" ]]; then
    ln -sf "/run/host/${WAYLAND_DISPLAY}" "${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}"
fi
if [[ -n "${DISPLAY:-}" && -d "/run/host/.X11-unix" ]]; then
    ln -sfn "/run/host/.X11-unix" /tmp/.X11-unix
fi

# ============================================================
# Volume ownership (only when process drops privileges)
# ============================================================
# Docker rootful: setpriv drops to SHELL_UID at exec time, so volumes
# must be owned by SHELL_UID for the unprivileged process to write.
# Podman/Docker rootless/krun: process stays UID 0, volumes are already
# accessible. Chowning would map to subordinate UIDs on the host.
if [[ "$SHELL_UID" != "0" ]]; then
    if [[ -d /nix/store && "$(stat -c '%u' /nix/store)" != "$SHELL_UID" ]]; then
        sudo chown -R "$SHELL_UID:$SHELL_GID" /nix 2>/dev/null || true
    fi
    if [[ "$(stat -c '%u' "$HOME")" != "$SHELL_UID" ]]; then
        sudo chown -R --one-file-system "$SHELL_UID:$SHELL_GID" "$HOME" 2>/dev/null || true
    fi
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

# Box containers: install all configured tools on startup (idempotent, fast if already installed)
if [[ "${YAAS_BOX:-}" == "1" ]]; then
    mise install || true
fi

# Activate mise — adds shims to PATH and loads env vars from [env] section.
# Must run AFTER mise install so activate sees installed tools and updates PATH.
eval "$(mise activate bash)"

# ============================================================
# Podman DinD setup (if requested)
# ============================================================
# Clean up stale wrappers from previous runs (persist in yaas-home volume).
# The podman wrapper creates an infinite sudo recursion loop if left behind.
# The nix wrapper was used for LD_PRELOAD UID spoofing (removed).
rm -f "$HOME/.local/bin/podman" "$HOME/.local/bin/nix"

if [[ "${YAAS_PODMAN:-}" == "1" ]]; then
    source /opt/yaas/setup-podman.sh
fi

# ============================================================
# Exec
# ============================================================
# Expand environment variables in command arguments
# (e.g. $YAAS_PREAMBLE in tool commands like --append-system-prompt $YAAS_PREAMBLE)
args=()
for arg in "$@"; do
    args+=("$(envsubst <<< "$arg")")
done

# setpriv for privilege drop (rootful Docker only).
if [[ -n "${YAAS_SETPRIV:-}" ]]; then
    exec ${YAAS_SETPRIV} "${args[@]}"
fi
exec "${args[@]}"
