# services/fast_eval_cache.py
from __future__ import annotations
import time
import asyncio
from typing import Dict, Any, Optional, List
from core.logging import get_logger

logger = get_logger("services.fast_eval_cache")


class FastEvalCache:
    """
    Lightweight async-safe in-memory cache for evaluation results.
    Keys are arbitrary strings (we'll use `event_id[:]:case_id` form).
    """

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            v = self._cache.get(key)
            if not v:
                return None
            # return a shallow copy to avoid accidental mutation
            copy = dict(v)
            copy["cached_at"] = v.get("cached_at")
            return copy

    async def set(self, key: str, value: Dict[str, Any]) -> None:
        async with self._lock:
            entry = dict(value)
            entry["cached_at"] = time.time()
            self._cache[key] = entry
            logger.debug("fast_eval_cache_set", key=key)

    async def invalidate_event(self, event_id_prefix: str) -> None:
        """
        Remove keys starting with event_id_prefix (or equal).
        Useful when a review for event_id is updated.
        """
        async with self._lock:
            to_del = [k for k in self._cache.keys() if k.startswith(event_id_prefix)]
            for k in to_del:
                del self._cache[k]
            if to_del:
                logger.info("fast_eval_cache_invalidate_event", event_id=event_id_prefix, removed=len(to_del))

    async def clear(self) -> None:
        async with self._lock:
            n = len(self._cache)
            self._cache.clear()
            logger.info("fast_eval_cache_cleared", removed=n)

    async def stats(self) -> Dict[str, Any]:
        async with self._lock:
            return {"size": len(self._cache)}


# Module-level singleton
fast_eval_cache = FastEvalCache()