# services/patch_integration.py
from __future__ import annotations
import os
import logging
from typing import Dict, Any, Optional

from core.logging import get_logger
from agents.llm_patch_generator import LLMPatchGenerator, PATCH_MODE_ENV

logger = get_logger("services.patch_integration")

async def try_real_llm_patches(review: Any, context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """
    Try to generate real LLM patches for `review`.

    Returns mapping patch_id -> patch_text.
    If environment not in 'real' mode, or if files are missing, returns {}.
    This function is defensive — never raises; errors are logged and empty dict returned.
    """
    mode = os.environ.get(PATCH_MODE_ENV, "test").lower().strip()
    if mode != "real":
        logger.debug("try_real_llm_patches: skipping (mode != real)")
        return {}

    try:
        # prefer files from context if available (context may be the event payload)
        files = {}
        if context and isinstance(context, dict):
            # expected shape: context.get("files") or context["payload"]["files"]
            files = context.get("files") or context.get("payload", {}).get("files") or {}

        # fallback to review.metadata.files if present
        meta = getattr(review, "metadata", None) or (review.get("metadata") if isinstance(review, dict) else {})
        if not files and isinstance(meta, dict):
            files = meta.get("files") or meta.get("repo_files") or {}

        if not files:
            logger.debug("try_real_llm_patches: no files available; skipping LLM patches")
            return {}

        gen = LLMPatchGenerator()
        patches = await gen.generate_patches_for_review(review, files=files, max_tokens=512, temperature=0.0)
        return patches or {}
    except Exception as e:
        logger.exception("try_real_llm_patches_failed")
        return {}