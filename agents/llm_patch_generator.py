# agents/llm_patch_generator.py
from __future__ import annotations
import os
import json
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from core.llm_client import LLMClient
from core.logging import get_logger

logger = get_logger("agents.llm_patch_generator")

# Environment controls
PATCH_MODE_ENV = "MACRS_PATCH_MODE"  # "test" or "real"
MAX_PATCH_CHARS = int(os.environ.get("MACRS_MAX_PATCH_CHARS", "20000"))
MAX_FINDING_MESSAGE = 1000

SYSTEM_PROMPT = """
You are a professional code-patching assistant. Given a single file's source and
a specific finding (id, message, explanation), produce a single JSON object ONLY
with the following shape:

{
  "file": "<filename>",
  "patch": "<PATCH_TEXT>"
}

Where "patch" is either:
 - a minimal unified diff starting with '*** ' or '--- ' (preferred), or
 - a short replacement snippet prefixed with "REPLACEMENT:\n<code>"

Return only the JSON object; nothing else. Keep patch short and focused.
"""

USER_TEMPLATE = """
Repository: {repo}
Event ID: {event_id}

Filename: {filename}

Finding:
id: {finding_id}
category: {category}
message: {message}
explanation: {explanation}

File head (first {head_chars} chars):
{file_head}

Produce the JSON object described in the system instruction.
"""

class LLMPatchGenerator:
    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()

    async def generate_patch_for_finding(
        self,
        repo: str,
        event_id: str,
        filename: str,
        file_text: str,
        finding: Dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> Optional[str]:
        """
        Returns patch text (string) or None on failure/invalid.
        Does NOT raise on parse errors — returns None to allow fallback.
        """
        try:
            # Build prompt
            head_chars = 4000
            file_head = file_text[:head_chars] if file_text else ""
            msg = USER_TEMPLATE.format(
                repo=repo,
                event_id=event_id or "",
                filename=filename,
                finding_id=finding.get("id", "unknown"),
                category=finding.get("category", "style"),
                message=(finding.get("message") or "")[:MAX_FINDING_MESSAGE],
                explanation=(finding.get("explanation") or "")[:4000],
                file_head=file_head,
                head_chars=head_chars,
            ).strip()

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT.strip()},
                {"role": "user", "content": msg},
            ]

            resp = await self.llm.chat_complete(messages=messages, temperature=temperature, max_tokens=max_tokens)

            # extract content
            content = None
            choices = resp.get("choices") or []
            if choices:
                content = choices[0].get("message", {}).get("content") or choices[0].get("text")
            else:
                content = resp.get("text") or resp.get("content")

            if not content:
                logger.warning("llm_patch_no_content", repo=repo, filename=filename, event_id=event_id)
                return None

            # find JSON object in content
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1 and end > start:
                json_text = content[start:end+1]
            else:
                json_text = content

            parsed = json.loads(json_text)
            patch = parsed.get("patch") or parsed.get("diff") or parsed.get("patch_text")
            file_field = parsed.get("file") or filename

            # minimal validation
            if not patch or not isinstance(patch, str):
                logger.warning("llm_patch_missing_patch_field", repo=repo, filename=filename, event_id=event_id)
                return None

            if file_field != filename:
                # If model returned a different file, treat it as invalid (defensive)
                logger.warning("llm_patch_file_mismatch", expected=filename, got=file_field, event_id=event_id)
                return None

            if len(patch) > MAX_PATCH_CHARS:
                logger.warning("llm_patch_too_long", length=len(patch), max=MAX_PATCH_CHARS)
                return None

            # Accept patch
            return patch

        except Exception as e:
            logger.exception("llm_patch_generation_failed", filename=filename, event_id=event_id)
            return None


    async def generate_patches_for_review(
        self,
        review: Any,
        files: Optional[Dict[str, str]] = None,
        max_per_finding: int = 1,
        **kwargs,
    ) -> Dict[str, str]:
        """
        Attempt to generate patches for the given SynthesizedReview object.

        - review: model or dict containing event_id, repo, findings (list of dicts).
        - files: optional mapping filename -> content. If missing, returns {} (no-op)
        Returns mapping {patch_id -> patch_text}
        """
        # defensive checks
        try:
            event_id = getattr(review, "event_id", None) or (review.get("event_id") if isinstance(review, dict) else None) or ""
            repo = getattr(review, "repo", None) or (review.get("repo") if isinstance(review, dict) else None) or ""
            findings = getattr(review, "findings", None) or (review.get("findings") if isinstance(review, dict) else []) or []
        except Exception:
            return {}

        # prefer explicit files param; fallback to review.metadata.files if present
        files = files or {}
        if not files:
            meta = getattr(review, "metadata", None) or (review.get("metadata") if isinstance(review, dict) else {})
            if isinstance(meta, dict):
                files = meta.get("files") or meta.get("repo_files") or {}

        if not files:
            # Nothing to patch (we won't attempt to fetch a repo). Let caller fallback.
            logger.debug("llm_patch_no_files_available", event_id=event_id, repo=repo)
            return {}

        generator = self
        out: Dict[str, str] = {}

        # iterate findings and try to generate patches for each
        for idx, f in enumerate(findings):
            # Determine target filename candidate:
            # If finding has "filename" or "file", use it; otherwise attempt best-effort mapping:
            filename = f.get("filename") or f.get("file") or next(iter(files.keys()), None)
            if not filename or filename not in files:
                # skip: cannot patch
                logger.debug("llm_patch_no_target_file", finding_id=f.get("id"), event_id=event_id)
                continue

            file_text = files[filename]
            patch_text = await generator.generate_patch_for_finding(
                repo=repo,
                event_id=event_id,
                filename=filename,
                file_text=file_text,
                finding=f,
                **kwargs,
            )

            if patch_text:
                pid = f.get("id") or f"patch-{idx+1}"
                # keep patch short; store mapping to filename for later linker
                out[pid] = patch_text

        return out