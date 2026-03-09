#!/bin/bash
# Podman DinD setup — auto-install and configure podman inside the container.
# Sourced by entrypoint.sh when YAAS_PODMAN=1.
#
# Nested podman runs ROOTFUL: Go programs bypass LD_PRELOAD (direct syscalls),
# so podman sees real UID 0 and uses /etc/containers/ and /var/lib/containers/.
# fuse-overlayfs is needed because kernel overlay doesn't work inside a user
# namespace (the outer rootless podman always creates one).

# ============================================================
# Auto-install podman + fuse-overlayfs if not present
# ============================================================
if ! command -v podman &>/dev/null; then
    echo "Installing podman via nix (first run)..."
    mise use -g "nix:podman" "nix:fuse-overlayfs" 2>/dev/null || true
    eval "$(mise activate bash)"
fi

# ============================================================
# Generate podman config
# ============================================================
CONTAINERS_CONF_DIR="/etc/containers"
sudo mkdir -p "$CONTAINERS_CONF_DIR"

# Always overwrite configs — nix podman package may ship defaults that
# don't include fuse-overlayfs or cgroupfs settings we need.
sudo tee "$CONTAINERS_CONF_DIR/containers.conf" >/dev/null <<'CONF'
[containers]
# Host networking: netavark can't create network namespaces inside the
# outer rootless podman user namespace (setns blocked).
netns = "host"
# Replace default mqueue mount with tmpfs — krun VMs lack mqueue kernel support.
mounts = ["type=tmpfs,destination=/dev/mqueue"]
# Disable cgroups — controllers aren't delegated into the container.
# Resource limits are already enforced by the outer container runtime.
cgroups = "disabled"

[engine]
cgroup_manager = "cgroupfs"
events_logger = "file"
CONF

# Resolve fuse-overlayfs path (may be in nix profile, not in default PATH)
FUSE_OVERLAYFS="$(command -v fuse-overlayfs 2>/dev/null || echo "fuse-overlayfs")"
sudo tee "$CONTAINERS_CONF_DIR/storage.conf" >/dev/null <<CONF
[storage]
driver = "overlay"

[storage.options.overlay]
mount_program = "${FUSE_OVERLAYFS}"
mountopt = "nodev,fsync=0,allow_other"
CONF

# FUSE mounts are only accessible to the mounting process by default.
# allow_other lets child processes (conmon, crun) access fuse-overlayfs mounts.
# user_allow_other in fuse.conf is required since we're in a user namespace
# (CAP_SYS_ADMIN is namespace-scoped, not init_user_ns).
if ! grep -qs 'user_allow_other' /etc/fuse.conf 2>/dev/null; then
    echo "user_allow_other" | sudo tee /etc/fuse.conf >/dev/null
fi

sudo tee "$CONTAINERS_CONF_DIR/policy.json" >/dev/null <<'JSON'
{
    "default": [{"type": "insecureAcceptAnything"}]
}
JSON

sudo tee "$CONTAINERS_CONF_DIR/registries.conf" >/dev/null <<'CONF'
unqualified-search-registries = ["docker.io", "quay.io"]
CONF

# Force podman to use our configs — nix podman has its own store paths baked in
# at compile time and ignores /etc/containers/. These env vars override that.
export CONTAINERS_STORAGE_CONF="$CONTAINERS_CONF_DIR/storage.conf"
export CONTAINERS_CONF="$CONTAINERS_CONF_DIR/containers.conf"
export CONTAINERS_REGISTRIES_CONF="$CONTAINERS_CONF_DIR/registries.conf"

# ============================================================
# Podman wrapper — strip LD_PRELOAD + sudo
# ============================================================
# 1. LD_PRELOAD: conmon/crun (C programs) see fake UID from libfakeuid.so
#    and incorrectly try rootless user namespace setup.
# 2. sudo: needed for rootful Docker (gosu drops to non-root).
#    For podman runtime (already UID 0), sudo is a no-op.
REAL_PODMAN="$(command -v podman)"
WRAPPER_DIR="$HOME/.local/bin"
mkdir -p "$WRAPPER_DIR"
cat > "$WRAPPER_DIR/podman" <<WRAPPER
#!/bin/bash
unset LD_PRELOAD
exec sudo "$REAL_PODMAN" "\$@"
WRAPPER
chmod +x "$WRAPPER_DIR/podman"
# Ensure wrapper shadows nix podman (mise activate may have reordered PATH)
export PATH="$WRAPPER_DIR:$PATH"

# Reset storage if it was previously initialized with a different driver
if [[ -d /var/lib/containers/storage && ! -f /var/lib/containers/storage/.yaas-init ]]; then
    podman system reset --force 2>/dev/null || true
    sudo mkdir -p /var/lib/containers/storage
    touch /var/lib/containers/storage/.yaas-init
fi

# ============================================================
# Podman Docker-compatible socket (if requested)
# ============================================================
if [[ "${YAAS_PODMAN_DOCKER_SOCKET:-}" == "1" ]]; then
    PODMAN_SOCK_DIR="${XDG_RUNTIME_DIR}/podman"
    mkdir -p "$PODMAN_SOCK_DIR"
    podman system service --time=0 "unix://${PODMAN_SOCK_DIR}/podman.sock" &
    export DOCKER_HOST="unix://${PODMAN_SOCK_DIR}/podman.sock"
fi
