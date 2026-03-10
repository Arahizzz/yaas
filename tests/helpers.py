"""Shared test utilities and helpers."""

from __future__ import annotations

import sys
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from yaas.runtime import ContainerSpec, Mount

if TYPE_CHECKING:
    from yaas.config import Config

# ============================================================
# Platform markers
# ============================================================

linux_only = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Test only runs on Linux",
)

macos_only = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Test only runs on macOS",
)

not_windows = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Test does not run on Windows",
)


# ============================================================
# Runtime test helpers
# ============================================================


@contextmanager
def mock_docker_socket(accessible: bool = True) -> Generator[None, None, None]:
    """Mock Docker socket accessibility."""
    with patch("yaas.runtime.docker._can_access_docker_socket", return_value=accessible):
        yield


@contextmanager
def mock_which(commands: dict[str, str | None]) -> Generator[None, None, None]:
    """Mock shutil.which for specific commands.

    Patches shutil.which in all runtime submodules (podman, docker, krun).
    """

    def which_side_effect(cmd: str) -> str | None:
        return commands.get(cmd)

    with ExitStack() as stack:
        stack.enter_context(
            patch("yaas.runtime.podman.shutil.which", side_effect=which_side_effect)
        )
        stack.enter_context(
            patch("yaas.runtime.docker.shutil.which", side_effect=which_side_effect)
        )
        stack.enter_context(patch("yaas.runtime.krun.shutil.which", side_effect=which_side_effect))
        yield


def make_spec(**overrides: object) -> ContainerSpec:
    """Create a ContainerSpec with sensible defaults for testing."""
    defaults: dict[str, object] = {
        "image": "test:latest",
        "command": ["bash"],
        "working_dir": "/workspace",
        "user": "1000:1000",
        "environment": {},
        "mounts": [],
        "network_mode": None,
        "tty": True,
        "stdin_open": True,
    }
    defaults.update(overrides)
    return ContainerSpec(**defaults)  # type: ignore[arg-type]


def make_config(**overrides: object) -> Config:
    """Create a Config with sensible defaults for testing."""
    from yaas.config import Config

    return Config(**overrides)  # type: ignore[arg-type]


__all__ = [
    "Mount",
    "linux_only",
    "macos_only",
    "make_config",
    "make_spec",
    "mock_docker_socket",
    "mock_which",
    "not_windows",
]
