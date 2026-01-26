FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# ============================================================
# System packages
# ============================================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Essentials
    bash zsh git curl wget sudo ca-certificates \
    # Build tools
    build-essential make cmake pkg-config \
    # Python
    python3 python3-pip python3-venv \
    # Common utilities
    jq ripgrep fd-find fzf tree less vim nano htop \
    openssh-client gnupg unzip zip xz-utils \
    # For container socket access
    docker.io podman \
    && rm -rf /var/lib/apt/lists/*

# Create symlinks for fd (Ubuntu renames it)
RUN ln -sf /usr/bin/fdfind /usr/local/bin/fd

# ============================================================
# Node.js 22 LTS
# ============================================================
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# ============================================================
# Bun (fast JS runtime + package manager)
# ============================================================
ENV BUN_INSTALL=/usr/local
RUN curl -fsSL https://bun.sh/install | bash

# ============================================================
# Go (latest stable)
# ============================================================
COPY --from=golang:1.23 /usr/local/go /usr/local/go
ENV PATH="/usr/local/go/bin:$PATH"
ENV GOPATH="/go"
ENV PATH="$GOPATH/bin:$PATH"

# ============================================================
# Python tooling (uv - fast package manager)
# ============================================================
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# ============================================================
# Nushell
# ============================================================
RUN ARCH=$(uname -m) && \
    NU_VERSION=$(curl -sI https://github.com/nushell/nushell/releases/latest | grep -i location | sed 's/.*tag\///' | tr -d '\r\n') && \
    echo "Installing nushell ${NU_VERSION}" && \
    if [ "$ARCH" = "x86_64" ]; then \
        curl -fsSL "https://github.com/nushell/nushell/releases/download/${NU_VERSION}/nu-${NU_VERSION#v}-x86_64-unknown-linux-gnu.tar.gz" \
        | tar xz -C /usr/local/bin --strip-components=1 --wildcards '*/nu'; \
    elif [ "$ARCH" = "aarch64" ]; then \
        curl -fsSL "https://github.com/nushell/nushell/releases/download/${NU_VERSION}/nu-${NU_VERSION#v}-aarch64-unknown-linux-gnu.tar.gz" \
        | tar xz -C /usr/local/bin --strip-components=1 --wildcards '*/nu'; \
    fi

# ============================================================
# AI CLIs
# ============================================================
# Set HOME for installers (matches runtime HOME=/home)
ENV HOME=/home

# Claude Code - installs to $HOME/.local/bin (added to PATH by entrypoint)
RUN curl -fsSL https://claude.ai/install.sh | bash

# Other AI tools via npm
RUN npm install -g \
    @openai/codex \
    @google/gemini-cli \
    opencode-ai

# ============================================================
# Entrypoint
# ============================================================
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Make /home writable by any UID (container runs as host user via --userns=keep-id)
RUN mkdir -p "$HOME/.local/bin" "$HOME/.config" && chmod -R 777 /home

WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
