"""
Centralized logging configuration for the application.

Provides structured logging with consistent formatting across all modules.
"""
import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from config.settings import settings

KST = timezone(timedelta(hours=9))


def kst_converter(*args):
    """Convert timestamp to KST (UTC+9) for logging."""
    ts = args[-1] if args else time.time()
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(KST)
    return dt.timetuple()


# Enforce KST formatting across all loggers globally
logging.Formatter.converter = staticmethod(kst_converter)


class LoggerFactory:
    """Factory for creating configured loggers."""

    _configured = False

    @classmethod
    def configure_root_logger(cls) -> None:
        """Configure the root logger with application settings."""
        if cls._configured:
            return

        logging.basicConfig(
            level=getattr(logging, settings.LOG_LEVEL),
            format=settings.LOG_FORMAT,
            handlers=[
                logging.StreamHandler(sys.stdout)
            ]
        )

        # Suppress noisy third-party library logs
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)

        cls._configured = True

    @classmethod
    def set_log_level(cls, level: str) -> None:
        """Dynamically change the root logger level at runtime."""
        numeric_level = getattr(logging, level.upper(), logging.INFO)
        logging.getLogger().setLevel(numeric_level)

    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        """
        Get a logger with the specified name.

        Args:
            name: Logger name (typically __name__ of the module)

        Returns:
            Configured logger instance
        """
        cls.configure_root_logger()
        return logging.getLogger(name)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Convenience function to get a logger.

    Args:
        name: Logger name (defaults to 'train_agent' if not specified)

    Returns:
        Configured logger instance

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Starting reservation process")
    """
    return LoggerFactory.get_logger(name or 'train_agent')
