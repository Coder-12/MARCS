# agents/patch_generator.py
from __future__ import annotations
from typing import Optional, Dict, Any, List
import hashlib
import asyncio

from agents.results import AgentFinding, AgentResult, SynthesizedReview
from core.logging import get_logger
from core.tracing import start_span, get_trace_id, get_span_id
from services.patch_verifier import verify_patch

logger = get_logger("agents.patch_generator")


class PatchGenerator:
    """
    Phase-0 LLM Patch Generator stub.

    - Accepts a SynthesizedReview (or AgentFinding) and returns a
      "suggested_patch" text and a short metadata dict.
    - This is intentionally deterministic and safe (no external calls).
    - Replace the stub logic with real LLM/AST transforms in Phase-1.

    Interface:
      async generate_patch(finding, context) -> str | None
      async generate_patches_for_review(review, context) -> Dict[finding_id, patch_text]
    """

    def __init__(self, model_name: str = "stub-v0"):
        self.model_name = model_name
        self.logger = get_logger("patch.generator")

    async def generate_patch(self, finding: AgentFinding, context: Dict[str, Any]) -> Optional[str]:
        """
        Produce a patch suggestion for a single finding.
        The stub produces:
          - For style: a suggested code comment
          - For security: a textual remediation snippet
        """
        with start_span("patchgen.generate", parent_span_id=context.get("span_id")):
            # Simulate latency
            await asyncio.sleep(0.05)

            # Deterministic pseudo-patch based on finding id + trace
            key = f"{finding.id}:{finding.location}:{get_trace_id() or ''}"
            digest = hashlib.sha1(key.encode()).hexdigest()[:8]

            if finding.category == "style":
                patch = f"# PATCH_STUB_{digest}\n# Suggestion: shorten long lines or add a docstring.\n"
                patch += f"# Rationale: {finding.explanation or 'style issue'}\n"
                return patch

            if finding.category == "security":
                patch = (
                    f"# PATCH_STUB_{digest}\n"
                    f"# Suggestion: remove secrets and use environment variables / vault.\n"
                    f"# Rationale: {finding.explanation or 'security issue'}\n"
                    "## Example remediation:\n"
                    "config_value = os.getenv('SOME_SECRET')\n"
                )
                return patch

            # other categories: return a small textual hint
            patch = f"# PATCH_STUB_{digest}\n# Suggestion: review {finding.category} issue: {finding.explanation}\n"
            return patch

    async def generate_patches_for_review(self, review: SynthesizedReview, context: Dict[str, Any]) -> Dict[str, str]:
        """
        Generate patches for all findings that don't already have suggested_patch.
        Returns a dict mapping finding.id -> patch_text.
        """

        out = {}
        review.metadata.setdefault("patch_rejections", {})

        with start_span("patchgen.batch", parent_span_id=context.get("span_id")):
            for f in review.findings:

                # 1. If already has a patch, verify it also
                if f.suggested_patch:
                    verify_res = verify_patch(f.suggested_patch)
                    if verify_res["ok"] == "true":
                        out[f.id] = f.suggested_patch
                    else:
                        logger.warning(
                            "patch_rejected_existing",
                            fid=f.id,
                            reason=verify_res["reason"],
                            check=verify_res["check"]
                        )
                        review.metadata["patch_rejections"][f.id] = verify_res

                    continue

                # 2. Generate a new patch
                try:
                    p = await self.generate_patch(f, context)
                except Exception as e:
                    self.logger.error("patchgen_error", error=str(e), finding_id=f.id)
                    continue

                if not p:
                    continue

                # 3. VERIFY newly-generated patch
                verify_res = verify_patch(p)

                if verify_res["ok"] == "true":
                    out[f.id] = p
                else:
                    logger.warning(
                        "patch_rejected_generated",
                        fid=f.id,
                        reason=verify_res["reason"],
                        check=verify_res["check"]
                    )
                    review.metadata["patch_rejections"][f.id] = verify_res

            return out