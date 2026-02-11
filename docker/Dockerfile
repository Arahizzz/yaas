FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# ============================================================
# System packages (only things not available via mise)
# ============================================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core build tools
    build-essential make cmake pkg-config \
    autoconf automake libtool \
    coreutils bc \
    # Essential development libraries
    libssl-dev zlib1g-dev libxml2-dev \
    # VCS and security
    git git-lfs openssh-client gnupg ca-certificates \
    # Utilities
    curl wget unzip zip xz-utils zstd \
    sudo less vim nano \
    # Locale support (often needed)
    locales icu-devtools \
    # Clipboard tools
    wl-clipboard xclip \
    # GUI integration (D-Bus, GPU, audio)
    dbus \
    libgl1-mesa-dri mesa-utils \
    pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install


# Allow passwordless sudo for all users
RUN echo "ALL ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/nopasswd \
    && chmod 440 /etc/sudoers.d/nopasswd

# Configure PAM for sudo to skip account validation (shadow file not available
# when passwd/group are mounted from host but shadow isn't)
RUN echo -e "auth sufficient pam_permit.so\naccount sufficient pam_permit.so\nsession sufficient pam_permit.so" > /etc/pam.d/sudo

# ============================================================
# Install mise (tool version manager)
# ============================================================
RUN curl -fsSL https://mise.run | sh \
    && mv /root/.local/bin/mise /usr/local/bin/mise

# ============================================================
# Environment setup
# ============================================================
ENV HOME=/home
ENV MISE_DATA_DIR=/home/.local/share/mise
ENV MISE_CACHE_DIR=/home/.cache/mise

# ============================================================
# Entrypoint
# ============================================================
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Make /home writable by any UID (container runs as host user via --userns=keep-id)
RUN mkdir -p "$HOME/.local/bin" "$HOME/.local/share/mise" "$HOME/.cache" "$HOME/.config/mise" \
    && chmod -R 777 /home

# Create /run/user for XDG_RUNTIME_DIR (entrypoint creates /run/user/$UID)
RUN mkdir -p /run/user && chmod 1777 /run/user

WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
