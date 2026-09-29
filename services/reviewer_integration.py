# services/reviewer_integration.py
from __future__ import annotations
import os
import logging
from typing import Dict, Any, Optional

from core.logging import get_logger
from agents.results import SynthesizedReview
from services.reviewer import Reviewer  # step-2 reviewer implemented earlier
from agents.results import AgentFinding  # optional, if you want to validate fields
from services.file_extractor import extract_files_from_event

logger = get_logger("services.reviewer_integration")

# Environment flag (Option A)
LLM_MODE_ENV = "MACRS_LLM_MODE"  # "test" | "real"

async def generate_review_for_event(evt) -> Dict[str, Any]:
    """
    Generate a review for `evt`.
    - If MACRS_LLM_MODE == "real", calls the real Reviewer implementation.
    - Otherwise, returns None to let the caller follow the existing fake flow.
    """
    mode = os.environ.get(LLM_MODE_ENV, "test").lower().strip()
    # ---------------------------------------------------------
    # Test mode: no real LLM, return None (fallback to Phase-0)
    # ---------------------------------------------------------
    if mode != "real":
        # Not in real mode — let caller use fake/deterministic path
        logger.debug("generate_review_for_event: running in test mode (no real LLM).")
        return None # type: ignore[arg-type]

    # ---------------------------------------------------------
    # STEP-5: extract real files (GitHub / local / synthetic)
    # ---------------------------------------------------------
    files = await extract_files_from_event(evt)

    # In real mode — call reviewer. We try to be defensive about the payload shape.
    reviewer = Reviewer()

    # Some events may not include files (e.g. synthetic events). Provide minimal context.
    # The Reviewer implementation should handle missing/empty files gracefully.
    try:
        # Pass event id so reviewer can include it in the result if desired
        review_dict = await reviewer.review_repo_files(
            event_id=getattr(evt, "delivery_id", None),
            repo=evt.repo_full_name,
            files=files,
        )
    except TypeError:
        # Older reviewer signature may not accept kwargs: attempt basic call
        review_dict = await reviewer.review_repo_files(evt.repo_full_name, files)

    # Basic validation: ensure event_id exists
    if review_dict is None:
        logger.warning("reviewer returned None for event %s", getattr(evt, "delivery_id", None))
        return None # type: ignore[arg-type]

    # ensure review_dict is a serializable dict (review_store expects a dict)
    if not isinstance(review_dict, dict):
        logger.warning("reviewer returned non-dict; converting via SynthesizedReview if possible")
        try:
            # If reviewer returned a SynthesizedReview model
            review_dict = review_dict.model_dump() # type: ignore[arg-type]
        except Exception:
            # last resort: str()
            review_dict = {"event_id": getattr(evt, "delivery_id", None), "summary": str(review_dict)}

    # Ensure event_id alignment if reviewer omitted it
    if "event_id" not in review_dict or not review_dict.get("event_id"):
        review_dict["event_id"] = str(getattr(evt, "delivery_id", "") or "")

    return review_dict