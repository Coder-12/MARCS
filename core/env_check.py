"""
core/env_check.py

Centralized environment validation for MARCS.
Provides:
  - check_env()    : verifies required env vars (fatal or warn)
  - warn_env()     : non-fatal warnings for optional but recommended vars

Usage:
    from core.env_check import check_env, warn_env
    check_env()     # fail fast on missing essentials
    warn_env()      # log warnings only
"""

from __future__ import annotations
import os
import logging

logger = logging.getLogger("env_check")

# REQUIRED for using real reviewers
REQUIRED_ENV = [
    "OPENAI_API_KEY",   # or LLM_API_KEY
]

# OPTIONAL but recommended for safety / limits
OPTIONAL_ENV = [
    "MACRS_PROMPT_MAX_CHARS",
    "MACRS_MAX_PATCH_CHARS",
    "MACRS_ARTIFACT_DIR",
    "MACRS_RUN_STARTUP_INSPECTOR",
    "MACRS_INSPECTOR_DRY_RUN",
]

def check_env() -> None:
    """
    Fail-fast check for required environment.
    This will be called at app startup (api/app.py).
    Raises RuntimeError on missing essentials.
    """
    missing = []

    # Allow fallback "LLM_API_KEY" for OPENAI_API_KEY
    has_llm = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY"))

    if not has_llm:
        missing.append("OPENAI_API_KEY (or LLM_API_KEY)")

    if missing:
        msg = "Missing required environment variables: " + ", ".join(missing)
        logger.error(msg)
        raise RuntimeError(msg)

    logger.info("Environment check passed ✔️")


def warn_env() -> None:
    """
    Non-fatal warnings: recommended but not required env vars.
    Useful for: safety limits, inspector flags, artifact settings.
    """
    for name in OPTIONAL_ENV:
        if os.environ.get(name) is None:
            logger.warning(f"Optional env not set: {name}")

    # Friendly completion message
    logger.info("Optional environment check completed.")