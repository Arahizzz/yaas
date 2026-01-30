"""Logging infrastructure for YAAS warnings and errors."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.text import Text

# Module-level logger for YAAS
logger = logging.getLogger("yaas")

# Console for warnings/errors
_console = Console(stderr=True)


class RichConsoleHandler(logging.Handler):
    """Handler that writes warnings/errors to Rich console with styling."""

    def __init__(self, console: Console | None = None) -> None:
        super().__init__()
        self._console = console or _console
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            style = self._get_style(record.levelno)
            text = Text(msg, style=style)
            self._console.print(text)
        except Exception:
            self.handleError(record)

    def _get_style(self, levelno: int) -> str:
        if levelno >= logging.ERROR:
            return "red"
        if levelno >= logging.WARNING:
            return "yellow"
        return "dim"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure default logging with Rich console handler.

    Call this at startup to set up console logging for warnings/errors.
    """
    logger.setLevel(level)

    # Remove any existing handlers
    logger.handlers.clear()

    # Add console handler for warnings/errors
    handler = RichConsoleHandler()
    handler.setLevel(logging.WARNING)
    logger.addHandler(handler)


def get_logger() -> logging.Logger:
    """Get the YAAS logger instance."""
    return logger
