"""Centralized constants for YAAS."""

from pathlib import Path

import os
import platformdirs

# Cross-platform config and cache directories
CONFIG_DIR = Path(platformdirs.user_config_dir("yaas"))
CACHE_DIR = Path(platformdirs.user_cache_dir("yaas"))

# Runtime container image
RUNTIME_IMAGE = os.getenv("YAAS_RUNTIME_IMAGE", "ghcr.io/arahizzz/yaas/runtime:0.x-latest")

# Container volumes for persistence
MISE_DATA_VOLUME = "yaas-data"  # ~/.local/share/mise (tools)
CACHE_VOLUME = "yaas-cache"  # ~/.cache (general cache)

# Mise config path (auto-created if missing)
MISE_CONFIG_PATH = CONFIG_DIR / "mise.toml"

# Config file locations
GLOBAL_CONFIG_PATH = CONFIG_DIR / "config.toml"
PROJECT_CONFIG_NAME = ".yaas.toml"

# API keys to auto-forward
API_KEYS = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "COPILOT_GITHUB_TOKEN",
    "OPENROUTER_API_KEY",
]

# Config directories to mount directly (relative to $HOME)
CONFIG_MOUNTS = {
    ".claude": "Claude Code config",
    ".codex": "Codex config",
    ".gemini": "Gemini config",
    ".opencode": "OpenCode config",
    ".gitconfig": "Git config (file)",
    ".config/git": "Git XDG config (dir)",
}

# Tool shortcuts (yaas claude → runs claude)
TOOL_SHORTCUTS = ["claude", "codex", "gemini", "opencode"]

# YOLO flags for each tool (auto-approve all tool calls)
TOOL_YOLO_FLAGS: dict[str, list[str]] = {
    "claude": ["--dangerously-skip-permissions"],
    "codex": ["--dangerously-bypass-approvals-and-sandbox"],
    "gemini": ["--yolo"],
    "opencode": [],
}

# Container sockets for docker-in-docker
CONTAINER_SOCKETS = [
    "/run/user/{uid}/podman/podman.sock",  # Rootless podman
    "/var/run/docker.sock",  # Docker
    "/run/podman/podman.sock",  # Rootful podman
]
