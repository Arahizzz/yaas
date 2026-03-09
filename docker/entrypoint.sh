#!/bin/bash
set -e

# ============================================================
# Host UID/GID (passed by all runtimes for user setup and chown)
# ============================================================
SHELL_UID="${YAAS_HOST_UID:-1000}"
SHELL_GID="${YAAS_HOST_GID:-1000}"

# ============================================================
# Runtime-specific setup (optionally sets YAAS_GOSU for rootful Docker)
# ============================================================
case "${YAAS_RUNTIME}" in
    podman)      source /entrypoint-podman.sh ;;
    docker)      source /entrypoint-docker.sh ;;
    podman-krun) source /entrypoint-krun.sh ;;
    *)           echo "Unknown YAAS_RUNTIME: ${YAAS_RUNTIME:-<unset>}" >&2; exit 1 ;;
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
    # Fix home directory for users with wrong home (SSH uses getpwuid, not $HOME)
    sudo usermod -d /home "$(getent passwd "$SHELL_UID" | cut -d: -f1)" 2>/dev/null || true
fi

# ============================================================
# Rootless Podman setup (subuid/subgid for user namespaces)
# ============================================================
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

# XDG runtime directory — use SHELL_UID (the identity the user's process will see)
export XDG_RUNTIME_DIR="/run/user/${SHELL_UID}"
if [[ ! -d "$XDG_RUNTIME_DIR" ]]; then
    mkdir -p "$XDG_RUNTIME_DIR"
fi
# Ensure correct ownership (may be root-owned from Dockerfile or previous run)
if [[ "$(stat -c '%u' "$XDG_RUNTIME_DIR")" != "$SHELL_UID" ]]; then
    sudo chown "$SHELL_UID:$SHELL_GID" "$XDG_RUNTIME_DIR"
fi
chmod 700 "$XDG_RUNTIME_DIR"

# ============================================================
# Nix setup
# ============================================================
# Fix ownership for UID passthrough (image builds as root, runtime uses host UID).
# Single-user Nix requires the running user to own /nix. The guard ensures
# the expensive recursive chown only runs once per volume lifecycle.
if [[ -d /nix/store && "$(stat -c '%u' /nix/store)" != "$SHELL_UID" ]]; then
    sudo chown -R "$SHELL_UID:$SHELL_GID" /nix 2>/dev/null || true
fi

# Fix $HOME ownership for UID passthrough (image builds as root, runtime uses host UID).
# Check actual ownership instead of marker file — volumes persist across rebuilds
# and may have stale ownership from a previous UID or root-built image layers.
if [[ "$(stat -c '%u' "$HOME")" != "$SHELL_UID" ]]; then
    sudo chown -R "$SHELL_UID:$SHELL_GID" "$HOME" 2>/dev/null || true
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

# Wrap nix to strip LD_PRELOAD — when YAAS_SPOOF_UID is set, the AI CLI's
# process tree inherits LD_PRELOAD. Nix (C++) sees fake UID from libfakeuid
# and rejects /nix as "not owned by current user". The wrapper ensures nix
# always runs with real UID 0.
if [[ "${YAAS_SPOOF_UID:-}" == "1" ]] && command -v nix &>/dev/null; then
    REAL_NIX="$(command -v nix)"
    cat > "$HOME/.local/bin/nix" <<NIX_WRAPPER
#!/bin/bash
unset LD_PRELOAD
exec sudo "$REAL_NIX" "\$@"
NIX_WRAPPER
    chmod +x "$HOME/.local/bin/nix"
fi

# Activate mise — adds shims to PATH and loads env vars from [env] section
eval "$(mise activate bash)"

# ============================================================
# Podman DinD setup (if requested)
# ============================================================
if [[ "${YAAS_PODMAN:-}" == "1" ]]; then
    source /setup-podman.sh
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

# LD_PRELOAD for UID spoofing (podman/rootless docker — process is UID 0, spoof to host UID).
# LD_PRELOAD is inherited by the entire process tree. Setuid binaries (sudo)
# are excluded by libfakeuid's setuid detection.
if [[ "${YAAS_SPOOF_UID:-}" == "1" ]]; then
    export LD_PRELOAD=/usr/local/lib/libfakeuid.so
    # Git's safe.directory check compares getuid() against file owner st_uid.
    # With LD_PRELOAD, getuid() returns 1000 but files are owned by UID 0.
    # Use env vars instead of git config --global (gitconfig may be a bind mount).
    export GIT_CONFIG_COUNT="${GIT_CONFIG_COUNT:-0}"
    export GIT_CONFIG_KEY_${GIT_CONFIG_COUNT}="safe.directory"
    export GIT_CONFIG_VALUE_${GIT_CONFIG_COUNT}="*"
    export GIT_CONFIG_COUNT=$((GIT_CONFIG_COUNT + 1))
fi

# gosu for privilege drop (rootful docker — separate security concern, always applied).
if [[ -n "${YAAS_GOSU:-}" ]]; then
    exec ${YAAS_GOSU} "${args[@]}"
fi
exec "${args[@]}"
