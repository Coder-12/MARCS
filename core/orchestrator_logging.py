# core/orchestrator_logging.py
from __future__ import annotations
import logging
import json
import os
from logging.handlers import RotatingFileHandler
from typing import Any, Dict

# Environment-configurable path
# Default: logs/orchestrator.log (project-root/logs)
DEFAULT_MAX_BYTES = int(os.environ.get("MACRS_ORCH_LOG_MAX_BYTES", str(2 * 1024 * 1024)))  # 2 MB
DEFAULT_BACKUP_COUNT = int(os.environ.get("MACRS_ORCH_LOG_BACKUP_COUNT", "3"))

def get_default_log_path():
    return os.environ.get("MACRS_ORCH_LOG_PATH", "logs/orchestrator.log")

class JSONFormatter(logging.Formatter):
    """
    Simple JSON formatter for structured logs.
    Each log record will become a JSON object with: ts, level, msg, and record.extra fields.
    """
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {}
        payload["ts"] = self.formatTime(record, self.datefmt)
        payload["level"] = record.levelname
        # if message already JSON-y, keep as string under message
        payload["message"] = record.getMessage()

        # attach any extra dict passed as 'extra' under 'fields' key
        # Python logging uses record.__dict__ for extras; filter standard keys
        std_attrs = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName", "processName",
            "process"
        }
        extras = {}
        for k, v in record.__dict__.items():
            if k in std_attrs:
                continue
            if k in ("message",):
                continue
            extras[k] = v
        if extras:
            payload["fields"] = extras

        return json.dumps(payload, ensure_ascii=False)


def configure_orchestrator_logger(log_path: str | None = None, level: int | str = logging.INFO) -> None:
    """
    Test-safe + production-safe logger configuration.

    ALWAYS reconfigures the 'orchestrator' logger:
    - clears old handlers (important when MACRS_ORCH_LOG_PATH changes)
    - installs fresh RotatingFileHandler
    """

    path = log_path or get_default_log_path()

    # Ensure directory exists
    log_dir = os.path.dirname(path) or "."
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("orchestrator")
    logger.setLevel(level)

    # --- CRITICAL FIX: Remove ALL existing handlers ---
    for h in list(logger.handlers):
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    # Add new handler pointing to CURRENT path
    handler = RotatingFileHandler(
        path,
        maxBytes=DEFAULT_MAX_BYTES,
        backupCount=DEFAULT_BACKUP_COUNT,
        encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(JSONFormatter())

    logger.addHandler(handler)

    # don't propagate to root
    logger.propagate = False