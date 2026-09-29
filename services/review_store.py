# services/review_store.py
from __future__ import annotations
from typing import Dict, Optional
import asyncio
from datetime import datetime, timezone

from agents.results import SynthesizedReview
from core.storage import get_storage
from core.logging import get_logger

# new import to invalidate cache after save
import asyncio as _asyncio
from services.fast_eval_cache import fast_eval_cache

logger = get_logger("services.review_store")

# Simple in-memory dict store with asyncio lock
_STORE: Dict[str, SynthesizedReview] = {}
_LOCK = asyncio.Lock()
_storage = get_storage()

# For tests/debug convenience: keep last saved event id and cached review dict (best-effort)
last_saved_event_id: Optional[str] = None
_last_saved_raw: Optional[Dict] = None

async def save_review(review: SynthesizedReview) -> None:
    """Persist SynthesizedReview (dict) using configured storage backend.
    Ensures a created_at timestamp is present in the serialized dict so golden tests
    and logs always have a timestamp to check.
    """
    global last_saved_event_id, _last_saved_raw

    async with _LOCK:
        # pydantic v2 uses model_dump(), v1 falls back to dict()
        data = review.model_dump() if hasattr(review, "model_dump") else dict(review)

        # Ensure created_at exists (ISO8601 UTC) for stable golden/eval checks.
        if "created_at" not in data or not data.get("created_at"):
            # use UTC ISO format with timezone
            data["created_at"] = datetime.now(timezone.utc).isoformat()

        await _storage.save_review(str(review.event_id), data)
        logger.info("review_saved", event_id=review.event_id, repo=review.repo, ts=data.get("created_at"))

        # update test/debug cache (best-effort)
        try:
            last_saved_event_id = str(review.event_id)
            _last_saved_raw = data
        except Exception:
            # non-fatal
            pass

    # Invalidate fast-eval cache entries for this event (best-effort, don't block)
    try:
        # schedule async invalidation without awaiting
        _asyncio.create_task(fast_eval_cache.invalidate_event(str(review.event_id)))
    except Exception:
        logger.exception("cache_invalidate_failed", event_id=review.event_id)

async def get_review(event_id: str) -> Optional[SynthesizedReview]:
    async with _LOCK:
        d = await _storage.get_review(event_id)
        if d is None:
            # fallback to last-saved in-process cache for very fast local tests
            if last_saved_event_id == str(event_id) and _last_saved_raw is not None:
                d = _last_saved_raw
            else:
                return None
        # reconstruct pydantic model
        try:
            return SynthesizedReview.model_validate(d)
        except Exception:
            try:
                return SynthesizedReview(**d)
            except Exception as e:
                logger.error("reconstruct_review_failed", event_id=event_id, error=str(e))
                return None


# convenience for tests / debug
async def all_reviews() -> Dict[str, SynthesizedReview]:
    async with _LOCK:
        d = await _storage.list_reviews()
        out = {}
        for k, v in d.items():
            try:
                out[k] = SynthesizedReview.model_validate(v)
            except Exception:
                try:
                    out[k] = SynthesizedReview(**v)
                except Exception:
                    logger.exception("all_reviews_deserialize_failed", key=k)
        return out