# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

YAAS (Yet Another Agent Sandbox) is a Python CLI tool that runs AI coding agents in sandboxed containers with proper UID/GID passthrough, tool management via mise, and resource limits.

## Commands

### Development

```bash
# Install dependencies
uv sync
uv sync --all-extras            # Include dev dependencies

# Run tests
uv run pytest                   # All tests
uv run pytest -v                # Verbose
uv run pytest tests/test_config.py  # Single file
uv run pytest -k test_basic     # Pattern match
uv run pytest --cov=src/yaas    # With coverage

# Linting and type checking
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run mypy src/yaas/

# Run CLI directly during development
uv run yaas --help
```

### Docker Image

The runtime image is at `ghcr.io/arahizzz/yaas/runtime:0.x-latest`. Dockerfile is in `docker/`.

## Architecture

```
src/yaas/
├── cli.py          # Typer CLI entry point (yaas.cli:app)
├── config.py       # Two-level config loading (global + project)
├── container.py    # Container spec building and mount logic
├── runtime.py      # Runtime abstraction (Podman/Docker)
├── platform.py     # Cross-platform detection and compatibility
├── worktree.py     # Git worktree management
└── data/mise.toml  # Default tool configuration
```

### Key Design Patterns

- **Runtime Protocol**: `runtime.py` defines a protocol that both `PodmanRuntime` and `DockerRuntime` implement. Add new runtimes by implementing this protocol.
- **Container Specs**: `container.py` builds dataclass-based specs that runtimes translate to CLI commands. Three builders: `build_container_spec()`, `build_clone_spec()`, `build_clone_work_spec()`.
- **Config Hierarchy**: Global config at `~/.config/yaas/config.toml`, project overrides in `.yaas.toml`. Merged in `config.py`.
- **Platform Abstraction**: All platform-specific logic (socket paths, UID handling, clipboard) is isolated in `platform.py`.

### Test Fixtures

`tests/conftest.py` provides:
- `mock_linux`, `mock_macos`, `mock_other_platform` - Platform mocking
- `clean_env` - Environment isolation
- `tmp_project_dir` - Temporary project directories

## Code Conventions

- Python 3.10+ with strict mypy
- Line length 100 (ruff)
- Dataclasses for structured data
- Protocol-based abstractions for runtime independence
