"""Centralized constants for YAAS."""

import os
from pathlib import Path

import platformdirs

# Cross-platform config, cache, and data directories
CONFIG_DIR = Path(platformdirs.user_config_dir("yaas"))
CACHE_DIR = Path(platformdirs.user_cache_dir("yaas"))
DATA_DIR = Path(platformdirs.user_data_dir("yaas"))

# Worktree storage location
WORKTREES_DIR = DATA_DIR / "worktrees"

# Runtime container image
RUNTIME_IMAGE = os.getenv("YAAS_RUNTIME_IMAGE", "ghcr.io/arahizzz/yaas/runtime:0.x-latest")

# Container volumes for persistence
MISE_DATA_VOLUME = "yaas-data"  # ~/.local/share/mise (tools)
CACHE_VOLUME = "yaas-cache"  # ~/.cache (general cache)

# Clone feature constants
CLONE_WORKSPACE = "/home/workspace"
CLONE_VOLUME_PREFIX = "yaas-clone-"

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

