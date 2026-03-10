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
HOME_VOLUME = "yaas-home"  # /home (entire home directory)
NIX_VOLUME = "yaas-nix"  # /nix (Nix store and database)

# Box (persistent container) constants
BOX_CONTAINER_PREFIX = "yaas-box-"

# Mise config path (auto-created if missing)
MISE_CONFIG_PATH = CONFIG_DIR / "mise.toml"

# Config file locations
GLOBAL_CONFIG_PATH = CONFIG_DIR / "config.toml"
PROJECT_CONFIG_NAME = ".yaas.toml"
