import logging
import logging.config
import sys
from typing import Any, Dict, Type, cast

import structlog
from structlog.typing import BindableLogger
from core.tracing import get_trace_id, get_span_id

# Define the local/production context once
IS_LOCAL: bool = sys.stdout.isatty()

def add_trace_context(logger, method_name, event_dict):
    """
    structlog processor that injects trace ids into structured logs.
    """
    trace = get_trace_id()
    span = get_span_id()
    if trace:
        event_dict.setdefault("trace_id", trace)
    if span:
        event_dict.setdefault("span_id", span)
    return event_dict

def configure_stdlib_logging(log_level: int = logging.INFO) -> None:
    """
    Configures the standard library logging using dictConfig.
    This is necessary for libraries (like FastAPI/Uvicorn) that use
    the standard library logging module.
    """
    config: Dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,  # Keep existing loggers
        "formatters": {
            # This is a dummy formatter; the actual formatting is done by structlog
            "plain": {"format": "%(message)s"},
        },
        "handlers": {
            "default": {
                "level": log_level,
                "class": "logging.StreamHandler",
                "formatter": "plain",
                "stream": sys.stdout,
            },
        },
        "root": {
            "handlers": ["default"],
            "level": log_level,
        },
        "loggers": {
            # Silence noisy loggers if needed
            "uvicorn": {"level": log_level, "propagate": True},
            "uvicorn.access": {"level": logging.WARNING, "propagate": False},
        },
    }
    logging.config.dictConfig(config)

def configure_logging(level: int = logging.INFO) -> None:
    """
    Configure application-wide structured logging using structlog.
    Provides:
      - JSON logs in production (Docker)
      - Colorized logs locally
      - Structured event data
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    # 1. Standard processors for both environments
    processors = [
        # Log entry context: Add level, and PID
        structlog.stdlib.filter_by_level,  # Drop logs below the configured stdlib level

        # Add logger name and log level
        structlog.stdlib.add_logger_name,
        structlog.processors.add_log_level,

        # Add timestamp
        timestamper,
        add_trace_context,

        # Add context and stack trace information
        structlog.processors.StackInfoRenderer(),
        structlog.processors.CallsiteParameterAdder(
            [
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.LINENO,
            ]
        ),

        # Format exceptions
        structlog.processors.ExceptionRenderer(),
    ]

    # 2. Add environment-specific processors (Renderer)
    # Local: pretty logs
    if IS_LOCAL:
        processors.append(structlog.dev.ConsoleRenderer())
    # Production: JSON logs
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        # Factory for creating standard library loggers
        logger_factory=structlog.stdlib.LoggerFactory(),
        # Wrapper to make stdlib loggers structlog-aware
        # wrapper_class=cast(Type[BindableLogger], structlog.stdlib.BoundLogger),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        # Use existing loggers where possible
        cache_logger_on_first_use=True,
    )

    # 3. Configure structlog
    # Configure root logger (FastAPI, Uvicorn compatibility)
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )

def setup_logging(log_level: int = logging.INFO) -> None:
    """
    Main entry point to set up all logging.
    """
    configure_stdlib_logging(log_level)
    configure_logging()

def get_logger(name: str = None):
    """
    Return a structlog logger with the given name.
    Usage: logger = get_logger(__name__)
    """
    return structlog.get_logger(name or __name__)