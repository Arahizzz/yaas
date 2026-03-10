FROM fedora:latest

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# ============================================================
# 1. System packages (only things not available via mise)
# ============================================================
RUN dnf group install -y development-tools && dnf install -y \
    # Extra build tools not in the group
    cmake bc \
    # Development libraries (not in the group)
    openssl-devel libxml2-devel libffi-devel \
    # VCS extras (git is in the group)
    git-lfs \
    # Utilities
    wget libicu nano \
    # Clipboard tools
    wl-clipboard xclip \
    # Podman DinD (baked in, no nix runtime install)
    podman fuse-overlayfs \
    && dnf clean all \
    && git lfs install

# ============================================================
# 2. sudo + PAM for privileged setup in entrypoint
# ============================================================
RUN echo "ALL ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/nopasswd \
    && chmod 440 /etc/sudoers.d/nopasswd

# ============================================================
# 3. Tool managers: mise + nix (slow installs, cached aggressively)
# ============================================================
RUN curl -fsSL https://mise.run | sh \
    && mv /root/.local/bin/mise /usr/local/bin/mise

RUN curl --proto '=https' --tlsv1.2 -sSf -L https://install.determinate.systems/nix \
    | sh -s -- install linux --init none --no-confirm

# ============================================================
# 4. Runtime environment
# ============================================================
ENV HOME=/home
ENV MISE_DATA_DIR=/home/.local/share/mise
ENV MISE_CACHE_DIR=/home/.cache/mise

# Writable dirs for dynamic UID passthrough + XDG runtime dir
RUN mkdir -p "$HOME/.local/bin" "$HOME/.local/share/mise" "$HOME/.cache" "$HOME/.config/mise" \
    && chmod -R 777 /home \
    && mkdir -p /run/user && chmod 1777 /run/user

# ============================================================
# 5. Entrypoint scripts (change most often — last for cache)
# ============================================================
COPY --chmod=755 entrypoint.sh entrypoint-podman.sh entrypoint-docker.sh entrypoint-krun.sh setup-podman.sh /opt/yaas/

WORKDIR /workspace
ENTRYPOINT ["/opt/yaas/entrypoint.sh"]
CMD ["bash"]
