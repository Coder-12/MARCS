# services/golden_registry.py
from __future__ import annotations
import time
import threading
from typing import Dict, Any, Optional, List
from core.logging import get_logger
from eval.loader import load_golden_cases

logger = get_logger("services.golden_registry")


class GoldenRegistry:
    """
    In-memory registry of golden cases.
    - Loads latest version directory via eval.loader.load_golden_cases()
    - Exposes get/all/reload helpers
    - Thread-safe for reads + hot-reload
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._cases: Dict[str, Dict[str, Any]] = {}
        self._loaded_at: Optional[float] = None
        # initial load
        self.reload()

    def reload(self) -> None:
        """Reload golden cases from disk (latest version as loader picks)."""
        logger.info("golden_registry_reload: start")
        cases = load_golden_cases()
        out: Dict[str, Dict[str, Any]] = {}
        for c in cases:
            # store as plain dict (pydantic -> dict)
            try:
                if hasattr(c, "model_dump"):
                    out[c.id] = c.model_dump()
                else:
                    out[c.get("id")] = c
            except Exception:
                # fallback: try to read id key
                try:
                    out[c["id"]] = c
                except Exception:
                    continue
        with self._lock:
            self._cases = out
            self._loaded_at = time.time()
        logger.info("golden_registry_reload: done", count=len(out), ts=self._loaded_at)

    def get(self, case_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._cases.get(case_id)

    def all(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return dict(self._cases)

    def loaded_at(self) -> Optional[float]:
        return self._loaded_at


# Module-level singleton
golden_registry = GoldenRegistry()