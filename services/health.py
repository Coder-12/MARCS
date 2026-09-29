# services/health.py
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import FastAPI
from pydantic import BaseModel

from services.startup import get_last_startup_decisions

app = FastAPI(title="MACRS health", version=os.environ.get("MACRS_VERSION", "dev"))

class HealthResponse(BaseModel):
    status: str
    ts: str
    version: str
    details: Dict[str, Any] = {}

@app.get("/health", response_model=HealthResponse)
async def health():
    """Lightweight liveness probe. Returns up + timestamp."""
    return HealthResponse(
        status="ok",
        ts=datetime.now(timezone.utc).isoformat(),
        version=os.environ.get("MACRS_VERSION", "dev"),
        details={}
    )

@app.get("/ready", response_model=HealthResponse)
async def ready():
    """
    Readiness: perform minimal checks:
      - environment sanity
      - optionally check LLM client can be instantiated (no network call)
    """
    details = {}
    # report whether important credentials are set (do NOT return secrets)
    details["has_llm_key"] = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY"))
    # Add optional inspector summary
    try:
        decisions = get_last_startup_decisions()
        if decisions:
            # only include concise summary
            details["inspector_decisions_count"] = int(len(decisions)) # type: ignore[arg-type]
            details["inspector_decisions_sample"] = decisions[:5]
        else:
            details["inspector_decisions_count"] = 0 # type: ignore[arg-type]
    except Exception:
        details["inspector_decisions_count"] = "error"

    # basic env vars
    details["prompt_max_chars"] = os.environ.get("MACRS_PROMPT_MAX_CHARS")
    details["max_patch_chars"] = os.environ.get("MACRS_MAX_PATCH_CHARS")
    return HealthResponse(
        status="ready",
        ts=datetime.now(timezone.utc).isoformat(),
        version=os.environ.get("MACRS_VERSION", "dev"),
        details=details
    )