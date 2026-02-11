"""Pytest configuration and fixtures."""

import os
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

# ============================================================
# Platform mocking fixtures
# ============================================================


@contextmanager
def _mock_platform(
    is_linux: bool = False,
    is_macos: bool = False,
    is_wsl: bool = False,
    uid: int = 1000,
    gid: int = 1000,
) -> Generator[None, None, None]:
    """Context manager for mocking platform detection across all modules.

    Patches platform detection at multiple levels:
    - yaas.platform: Source module where is_linux/is_macos/is_wsl are defined
    - yaas.container: Module that imports is_linux/is_macos/is_wsl
    - yaas.runtime: Module that imports is_linux
    """
    with ExitStack() as stack:
        # Patch at source (platform.py) - affects internal calls like get_container_socket_paths
        stack.enter_context(patch("yaas.platform.is_linux", return_value=is_linux))
        stack.enter_context(patch("yaas.platform.is_macos", return_value=is_macos))
        stack.enter_context(patch("yaas.platform.is_wsl", return_value=is_wsl))
        # Patch imported references in other modules
        stack.enter_context(patch("yaas.container.is_linux", return_value=is_linux))
        stack.enter_context(patch("yaas.container.is_macos", return_value=is_macos))
        stack.enter_context(patch("yaas.container.is_wsl", return_value=is_wsl))
        stack.enter_context(patch("yaas.container.get_uid_gid", return_value=(uid, gid)))
        stack.enter_context(patch("yaas.runtime.is_linux", return_value=is_linux))
        yield


@pytest.fixture
def mock_linux() -> Generator[None, None, None]:
    """Mock Linux platform for container and runtime modules."""
    with _mock_platform(is_linux=True, is_macos=False):
        yield


@pytest.fixture
def mock_wsl() -> Generator[None, None, None]:
    """Mock WSL2 platform (is_linux=True, is_wsl=True)."""
    with _mock_platform(is_linux=True, is_macos=False, is_wsl=True):
        yield


@pytest.fixture
def mock_macos() -> Generator[None, None, None]:
    """Mock macOS platform for container and runtime modules."""
    with _mock_platform(is_linux=False, is_macos=True):
        yield


@pytest.fixture
def mock_other_platform() -> Generator[None, None, None]:
    """Mock non-Linux/non-macOS platform (e.g., Windows)."""
    with _mock_platform(is_linux=False, is_macos=False):
        yield


# ============================================================
# Environment fixtures
# ============================================================


@pytest.fixture
def clean_env() -> Generator[dict[str, str], None, None]:
    """Provide a clean environment with only basic variables."""
    clean = {"USER": "testuser"}
    with patch.dict(os.environ, clean, clear=True):
        yield clean


# ============================================================
# Project directory fixtures
# ============================================================


@pytest.fixture
def project_dir() -> Generator[Path, None, None]:
    """Provide a temporary project directory."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)
