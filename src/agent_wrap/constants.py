"""Centralized constants for agent-wrap."""

from pathlib import Path

# Default container image
DEFAULT_IMAGE = "ghcr.io/arahizzz/agent-wrap:latest"

# Config file locations
GLOBAL_CONFIG_PATH = Path.home() / ".config" / "agent-wrap" / "config.toml"
PROJECT_CONFIG_NAME = ".agent-wrap.toml"

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

# Tool shortcuts (agent-wrap claude → runs claude)
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
