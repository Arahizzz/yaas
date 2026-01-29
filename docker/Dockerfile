FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# ============================================================
# System packages (only things not available via mise)
# ============================================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Build essentials (for native compilation)
    build-essential make cmake pkg-config \
    # VCS and security
    git openssh-client gnupg ca-certificates \
    # Basic utilities
    curl wget unzip zip xz-utils \
    sudo less vim nano \
    # Clipboard tools for image pasting
    wl-clipboard xclip \
    # Container-in-container support
    docker.io podman \
    && rm -rf /var/lib/apt/lists/*

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

WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
