FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# ============================================================
# 1. System packages (only things not available via mise)
# ============================================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core build tools
    build-essential make cmake pkg-config \
    autoconf automake libtool \
    coreutils bc \
    # Essential development libraries
    libssl-dev zlib1g-dev libxml2-dev libffi-dev \
    # VCS and security
    git git-lfs openssh-client gnupg ca-certificates \
    # Utilities
    curl wget unzip zip xz-utils zstd gettext-base \
    sudo less vim nano \
    # Locale support (often needed)
    locales icu-devtools \
    # Clipboard tools
    wl-clipboard xclip \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install

# ============================================================
# 2. User identity support: sudo + PAM + libfakeuid.so
#    sudo/PAM for podman/docker (dynamic user via --userns=keep-id)
#    libfakeuid for krun (LD_PRELOAD UID spoofing in MicroVM)
# ============================================================
RUN echo "ALL ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/nopasswd \
    && chmod 440 /etc/sudoers.d/nopasswd \
    && echo -e "auth sufficient pam_permit.so\naccount sufficient pam_permit.so\nsession sufficient pam_permit.so" > /etc/pam.d/sudo

COPY fakeuid.c /tmp/fakeuid.c
RUN gcc -shared -fPIC -o /usr/local/lib/libfakeuid.so /tmp/fakeuid.c \
    && rm /tmp/fakeuid.c

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

# Writable dirs for host UID (--userns=keep-id) + XDG runtime dir
RUN mkdir -p "$HOME/.local/bin" "$HOME/.local/share/mise" "$HOME/.cache" "$HOME/.config/mise" \
    && chmod -R 777 /home \
    && mkdir -p /run/user && chmod 1777 /run/user

# ============================================================
# 5. Entrypoint scripts (change most often — last for cache)
# ============================================================
COPY --chmod=755 entrypoint.sh entrypoint-container.sh entrypoint-krun.sh /

WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
