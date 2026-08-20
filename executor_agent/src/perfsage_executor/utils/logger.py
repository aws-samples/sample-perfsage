"""Structured logging configuration for PerfSage Executor Agent."""

from __future__ import annotations

import logging
import sys
from typing import Any

from perfsage_executor.config import get_settings


class StructuredFormatter(logging.Formatter):
    """JSON-like structured log formatter for production use."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        # Include extra fields
        for key in ("test_id", "tool", "action", "duration_ms"):
            if hasattr(record, key):
                log_data[key] = getattr(record, key)
        return str(log_data)


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger instance.

    Args:
        name: Logger name, typically __name__ of the calling module.

    Returns:
        Configured logging.Logger instance.
    """
    settings = get_settings()
    logger = logging.getLogger(f"perfsage.{name}")

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)

    logger.setLevel(getattr(logging, settings.agent.log_level.upper(), logging.INFO))
    return logger
