"""
Structured logging setup using structlog.
Call configure_logging() once at application startup (inside lifespan).
After that, use structlog.get_logger() anywhere to get a bound logger.
"""

import logging
import structlog
from app.core.config import settings


def configure_logging() -> None:
    """Configure structlog to emit clean, structured log lines."""

    # Set the stdlib root logger level so uvicorn and httpx logs are captured
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(message)s",
    )

    structlog.configure(
        processors=[
            # Add log level name to every event
            structlog.stdlib.add_log_level,
            # Add ISO timestamp
            structlog.processors.TimeStamper(fmt="iso"),
            # Render as a readable key=value string in development;
            # swap to JSONRenderer for production log aggregators
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
