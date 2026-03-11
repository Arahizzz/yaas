#!/bin/bash
# Podman DinD setup — configure podman inside the container.
# Sourced by entrypoint.sh when YAAS_PODMAN=1.
#
# Nested podman runs ROOTFUL: the container process is UID 0 (real root in
# rootless podman's user namespace). fuse-overlayfs is needed because kernel
# overlay doesn't work inside a user namespace.

# ============================================================
# Generate podman config
# ============================================================
CONTAINERS_CONF_DIR="/etc/containers"
sudo mkdir -p "$CONTAINERS_CONF_DIR"

# Read current oom_score_adj so nested containers don't try to lower it
# (podman defaults to 0, but the kernel rejects lowering below the parent's value).
OOM_SCORE_ADJ="$(cat /proc/self/oom_score_adj 2>/dev/null || echo 0)"

if [[ "${YAAS_RUNTIME}" == "podman-krun" ]]; then
    # krun VM has a real kernel (KVM) — use proper namespace isolation.
    sudo tee "$CONTAINERS_CONF_DIR/containers.conf" >/dev/null <<CONF
[containers]
netns = "host"
userns = "host"
ipcns = "private"
utsns = "private"
cgroupns = "private"
cgroups = "enabled"
log_driver = "k8s-file"
oom_score_adj = ${OOM_SCORE_ADJ}
# krun kernel lacks POSIX mqueue support.
mounts = ["type=tmpfs,destination=/dev/mqueue"]

[engine]
cgroup_manager = "cgroupfs"
events_logger = "file"
runtime = "crun"
CONF
else
    # Regular podman/docker — all host namespaces (can't create inside userns).
    # Based on: https://github.com/containers/image_build/blob/main/podman/containers.conf
    sudo tee "$CONTAINERS_CONF_DIR/containers.conf" >/dev/null <<CONF
[containers]
netns = "host"
userns = "host"
ipcns = "host"
utsns = "host"
cgroupns = "host"
cgroups = "disabled"
log_driver = "k8s-file"
oom_score_adj = ${OOM_SCORE_ADJ}
# krun kernel lacks POSIX mqueue support.
mounts = ["type=tmpfs,destination=/dev/mqueue"]

[engine]
cgroup_manager = "cgroupfs"
events_logger = "file"
runtime = "crun"
CONF
fi

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

# ============================================================
# Podman wrapper — sudo for rootful Docker privilege drop
# ============================================================
# sudo: needed for rootful Docker (setpriv drops to non-root).
# For podman runtime (already UID 0), sudo is a no-op.
REAL_PODMAN="$(command -v podman)"
WRAPPER_DIR="$HOME/.local/bin"
mkdir -p "$WRAPPER_DIR"
cat > "$WRAPPER_DIR/podman" <<WRAPPER
#!/bin/bash
exec sudo "$REAL_PODMAN" "\$@"
WRAPPER
chmod +x "$WRAPPER_DIR/podman"
# Ensure wrapper shadows system podman
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
