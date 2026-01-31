"""Shared test utilities and helpers."""

import sys
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from yaas.runtime import ContainerSpec, Mount

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
    with patch("yaas.runtime._can_access_docker_socket", return_value=accessible):
        yield


@contextmanager
def mock_which(commands: dict[str, str | None]) -> Generator[None, None, None]:
    """Mock shutil.which for specific commands."""

    def which_side_effect(cmd: str) -> str | None:
        return commands.get(cmd)

    with patch("yaas.runtime.shutil.which", side_effect=which_side_effect):
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


__all__ = [
    "Mount",
    "linux_only",
    "macos_only",
    "make_spec",
    "mock_docker_socket",
    "mock_which",
    "not_windows",
]
