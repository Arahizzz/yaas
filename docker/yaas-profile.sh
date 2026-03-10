#!/bin/bash
# /etc/profile.d/yaas.sh — sourced by login shells (box enter/exec).
# Activates mise, nix, and ~/.local/bin so exec'd shells match entrypoint.sh.

# Add local bin to PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

# Nix
NIX_PROFILE="/nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh"
if [[ -f "$NIX_PROFILE" ]]; then
    . "$NIX_PROFILE"
fi

# Mise
if command -v mise &>/dev/null; then
    eval "$(mise activate)"
fi
