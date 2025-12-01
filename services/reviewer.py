# services/reviewer.py
from __future__ import annotations
import os
import json
import asyncio
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from core.llm_client import LLMClient
from core.logging import get_logger

from sanitizers.prompt_cap import apply_prompt_cap
from utils.prompt_builder import build_prompt_string

# NEW IMPORTS FOR B4 & B5
from sanitizers.llm_input_clean import clean_text, clean_files
from sanitizers.llm_output_clean import rescue_json

# NEW: output schema guard
from sanitizers.output_schema_guard import validate_and_heal_review_json

from sanitizers.reviewer_extractor_validator import cross_validate

from utils.file_hash import file_dict_hashes

from services.patch_applier import apply_patches
from services.diff_generator import (
    generate_repo_diff_from_applied,
    write_final_artifact,
)

from services.journal_inspector import scan_and_recover_all

import tempfile

logger = get_logger("services.reviewer")

# Prompt template pieces (kept short & strict — we require JSON output)
SYSTEM_PROMPT = """
You are a professional code review assistant. Given source code and test context,
produce a concise JSON object with:
- findings: list of {id, category, severity, message, explanation, confidence}
- suggested_patches: mapping from finding id -> patch text (a small unified diff or code suggestion)
- summary: short textual summary
Return ONLY a single JSON object (no explanation).
"""

REVIEWER_USER_TEMPLATE = """
Repository: {repo}
Event ID: {event_id}
Files: {file_list}

Context:
{context}

Source (files concatenated, start each with "=== <filename> ==="):
{files_concat}

Produce the JSON described in the system instruction. Keep messages concise.
"""

# simple helper to build a minimal finding id
def _next_finding_id(repo: str, idx: int) -> str:
    return f"{repo.replace('/', '_')}-f{idx}"

def _safe_confidence(val):
    if isinstance(val, (int, float)):
        return float(val)

    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("high", "h"):
            return 0.9
        if v in ("medium", "m", "mid"):
            return 0.6
        if v in ("low", "l"):
            return 0.3
        try:
            return float(val)
        except Exception:
            return 0.5

    return 0.5

class Reviewer:
    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()

    async def review_repo_files(
        self,
        event_id: str = "",
        repo: str = "",
        files: Dict[str, str] | Dict[str, Any] = None,
        context: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        apply_patches_flag: bool = False,
        auto_recover: bool = True
    ) -> Dict[str, Any]:
        """
        Perform an LLM-powered review of the provided files.

        `files` is a mapping filename -> source text.
        Returns a dict shaped for SynthesizedReview (event_id, repo, findings, suggested_patches, summary, metadata).
        """
        recover_results: List[Dict[str, Any]] = []
        if auto_recover:
            try:
                recover_results = scan_and_recover_all()
                logger.info("journal_auto_recover", results=recover_results)
            except Exception:
                logger.exception("journal_recover_failed")

        # ------------------------------------------------------------------
        # 1) Ensure files dict exists
        # ------------------------------------------------------------------
        files = files or {}

        original_files = {k: v for k, v in files.items()}

        # compute file hashes for metadata (keep original snapshot)
        file_hashes, repo_hash = file_dict_hashes(files)

        # ---------------------------------------------------------
        # 2) Apply global prompt size cap BEFORE building prompt
        # ---------------------------------------------------------
        prompt_files, cap_meta = apply_prompt_cap(files)
        if cap_meta["dropped_files"] or cap_meta["truncated_in_place"]:
            logger.info("prompt_cap_applied", meta=cap_meta)

        # ------------------------------------------------------------------
        # 3) INPUT SANITIZATION (B4)
        # ------------------------------------------------------------------
        clean_pf = clean_files(prompt_files)
        clean_ctx = clean_text(context or "")

        files_concat = build_prompt_string(clean_pf)
        file_list = ", ".join(list(clean_pf.keys())[:10])

        msg = REVIEWER_USER_TEMPLATE.format(
            repo=repo,
            event_id=event_id,
            file_list=file_list,
            context=clean_ctx,
            files_concat=files_concat,
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.strip()},
            {"role": "user", "content": msg.strip()},
        ]

        # ------------------------------------------------------------------
        # 4) LLM CALL AND LATENCY TRACKING
        # ------------------------------------------------------------------
        start_ts = time.time()
        resp = await self.llm.chat_complete(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        latency_ms = int((time.time() - start_ts) * 1000)

        # parse response. Different model shapes possible — try to extract content
        content = None
        usage = None
        try:
            usage = resp.get("usage") if isinstance(resp, dict) else None
            choices = resp.get("choices") or []
            if choices:
                content = choices[0].get("message", {}).get("content") \
                          or choices[0].get("text")
            else:
                content = resp.get("text")
            if not content:
                raise ValueError("empty LLM content")
        except Exception:
            logger.exception("llm_response_parse_failed")
            raise

        # ------------------------------------------------------------------
        # 5) OUTPUT CLEANING + RESCUE (B5)
        # ------------------------------------------------------------------
        try:
            cleaned = rescue_json(content)
            # print("DEBUG CLEANED =", cleaned)
            parsed = json.loads(cleaned)
        except Exception:
            logger.exception("llm_json_parse_error; returning fallback review")
            return {
                "event_id": event_id,
                "repo": repo,
                "findings": [],
                "suggested_patches": {},
                "summary": f"LLM parse error; raw: {content[:300]}",
                "metadata": {
                    "llm_model": getattr(self.llm, "model", None),
                    "prompt_cap": cap_meta,
                    "llm_raw": content,
                    "llm_latency_ms": latency_ms,
                    "llm_usage": usage,
                },
                "ts": datetime.now(timezone.utc).isoformat(),
            }

        # ---------------------------------------------------------
        # 6) Strict schema guard & healing (C1, C2)
        # ---------------------------------------------------------
        healed, guard_meta = validate_and_heal_review_json(parsed, repo=repo)

        # Cross-validate reviewer output against extractor files (Step 5.5.D)
        healed_after_validator, validator_meta = cross_validate(
            healed=healed,
            prompt_files=prompt_files,
            cap_meta=cap_meta,
            repo=repo,
        )

        # weave validator_meta into guard/meta blocks
        # merge patch_safety and validator dropped info
        guard_meta["validator"] = validator_meta
        # update healed and patch_safety forwarded
        healed = healed_after_validator
        patch_safety = guard_meta.get("patch_safety", {})
        # ensure "patch_safety" in metadata includes dropped patches flagged by validator
        if "dropped_patches" in validator_meta:
            for k, v in validator_meta["dropped_patches"].items():
                # ensure patch_safety records the same key (if not present)
                patch_safety.setdefault(k, v if isinstance(v, dict) else {"ok": False, "reason": v})

        # ---------------------------------------------------------
        # 7) Patch safety metadata (already in guard_meta)
        # ---------------------------------------------------------
        patch_safety = guard_meta.get("patch_safety", {})

        # ---------------------------------------------------------
        # 8) Normalize parsed content
        # ---------------------------------------------------------
        if isinstance(healed, list):
            # If LLM returned a list of findings, wrap them properly
            if all(isinstance(el, dict) for el in healed):
                healed = {"findings": healed}
            else:
                # fallback: take first dict-like element
                picked = None
                for el in healed:
                    if isinstance(el, dict):
                        picked = el
                        break
                healed = picked or {}

        # final enforcement
        if not isinstance(healed, dict):
            healed = {}

        findings = healed.get("findings") or []
        suggested_patches = healed.get("suggested_patches") or {}
        # print("LLM Suggested Patches:", suggested_patches)
        summary = healed.get("summary") or healed.get("summary_text") or ""

        # ensure finding ids present
        norm_findings = []
        for i, f in enumerate(findings):
            fid = f.get("id") or _next_finding_id(repo, i + 1)
            norm_findings.append({
                "id": fid,
                "category": str(f.get("category", "style")),
                "severity": str(f.get("severity", "low")),
                "message": str(f.get("message", "")[:1000]),
                "explanation": str(f.get("explanation", "")[:4000]),
                "confidence": _safe_confidence(f.get("confidence", 0.5)),
            })

        # 9) Estimate cost (C4/C5). Best-effort: if usage has prompt_tokens/completion_tokens
        cost_estimate = None
        try:
            if usage and isinstance(usage, dict):
                prompt_t = int(usage.get("prompt_tokens") or 0)
                comp_t = int(usage.get("completion_tokens") or 0)
                total_t = prompt_t + comp_t
                # simple rate table (USD per 1k tokens) — configurable via env
                rate = float(os.environ.get("MACRS_LLM_COST_PER_1K", "0.002"))  # tiny default
                cost_estimate = round(total_t / 1000.0 * rate, 6) # type: ignore[arg-type]
        except Exception:
            cost_estimate = None

        # 10) Logging for observability (C4)
        logger.info("review_completed", repo=repo, event_id=event_id,
                    llm_model=getattr(self.llm, "model", None),
                    latency_ms=latency_ms,
                    findings_count=len(norm_findings),
                    patches_count=len(suggested_patches or {}))

        # 11) Build metadata block
        metadata = {
            "llm_model": getattr(self.llm, "model", None),
            "prompt_cap": cap_meta,
            "guard": guard_meta,
            "patch_safety": patch_safety,
            "llm_usage": usage,
            "llm_latency_ms": latency_ms,
            "llm_cost_estimate_usd": cost_estimate,
            # NEW:
            "file_hashes": file_hashes,  # dict filename -> sha256 hex
            "repo_hash": repo_hash,  # combined repo checksum
            "precheck_recovery": recover_results,
        }

        # ============================================================
        # STEP-6.1 PATCH-APPLICATION + DIFF + ARTIFACT INTEGRATION
        # ============================================================
        review_output = {
            "event_id": event_id,
            "repo": repo,
            "findings": norm_findings,
            "suggested_patches": suggested_patches,
            "summary": summary,
            "metadata": metadata,
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        # === EARLY EXIT: NO PATCHES PROVIDED BY LLM ===
        if apply_patches_flag and not suggested_patches:
            artifact_path = write_final_artifact(
                event_id=event_id,
                repo=repo,
                findings=norm_findings,
                applied_patches={},
                applied_diffs={},
                summary="No valid patches to apply.",
            )
            review_output["applied_patches"] = {}
            review_output["applied_diffs"] = {}
            review_output["artifact_path"] = artifact_path
            return review_output

        # ------------------------------------------------------------
        # If user did NOT request patch application → return cleanly
        # ------------------------------------------------------------
        if not apply_patches_flag:
            return review_output

        # ------------------------------------------------------------
        # Prepare a temporary repo where patches will be applied
        # ------------------------------------------------------------
        repo_root = tempfile.mkdtemp(prefix=f"macrs_apply_{event_id}_")

        # Write the original files into the temp folder
        # print(f"original_files: {original_files}")
        for fname, text in original_files.items():
            fpath = os.path.join(repo_root, fname)
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            with open(fpath, "w", encoding="utf-8") as fh:
                fh.write(text)

        # ------------------------------------------------------------
        # DRY-RUN patch validation (safety)
        # ------------------------------------------------------------
        # dry = apply_patches(event_id, repo_root, suggested_patches, dry_run=True)
        #
        # if len(dry.get("dropped", {})) == len(suggested_patches):
        #     # no patches survived → still write a final artifact (important)
        #     artifact_path = write_final_artifact(
        #         event_id=event_id,
        #         repo=repo,
        #         findings=norm_findings,
        #         applied_patches={},
        #         applied_diffs={},
        #         summary="No valid patches to apply.",
        #     )
        #     review_output["applied_patches"] = {}
        #     review_output["applied_diffs"] = {}
        #     review_output["artifact_path"] = artifact_path
        #     return review_output

        # ------------------------------------------------------------
        # STEP-6.3 — Patch Mapping + Dry-Run + Real Apply + Artifact
        # ------------------------------------------------------------

        # 1) Map suggested_patches keys → filenames
        filename_patches: Dict[str, str] = {}
        for key, diff_text in suggested_patches.items():
            extracted_filename = None
            for line in diff_text.splitlines():
                if line.startswith("--- "):
                    # e.g. "--- a/code.py"
                    fn = line[4:].strip()
                    if fn.startswith("a/") or fn.startswith("b/"):
                        fn = fn[2:]
                    extracted_filename = fn
                    break

            if extracted_filename:
                filename_patches[extracted_filename] = diff_text
            else:
                # fallback: if only 1 file, map to it
                if len(files) == 1:
                    filename_patches[list(files.keys())[0]] = diff_text
                else:
                    logger.info("patch_mapping_ambiguous", event_id=event_id, key=key)

        # 2) DRY-RUN safety validation
        # dry = apply_patches(event_id, repo_root, filename_patches, dry_run=True)
        # print(f"dry: {dry}")
        #
        # if len(dry.get("dropped", {})) == len(filename_patches):
        #     # No patch survived → still emit artifact
        #     artifact_path = write_final_artifact(
        #         event_id=event_id,
        #         repo=repo,
        #         findings=norm_findings,
        #         applied_patches={},   # unified diffs — none here
        #         applied_diffs={},
        #         summary="No valid patches to apply.",
        #     )
        #     review_output["applied_patches"] = {}
        #     review_output["applied_diffs"] = {}
        #     review_output["artifact_path"] = artifact_path
        #     review_output["metadata"]["journal_path"] = dry.get("journal")
        #     return review_output

        # 3) Real apply
        # print(f"filename_patches: {filename_patches}")
        final = apply_patches(event_id, repo_root, filename_patches, dry_run=False)
        # print(f"final: {final}")
        applied: Dict[str, str] = final.get("applied", {})            # patched content
        original_texts: Dict[str, str] = final.get("original_texts", {})
        journal_path = final.get("journal")

        # ------------------------------------------------------------
        # Generate unified diffs after application
        # ------------------------------------------------------------
        diffs = generate_repo_diff_from_applied(
            repo_root=repo_root,
            applied_map=applied,
            original_texts=original_texts,
        )

        # ------------------------------------------------------------
        # Write FINAL unified artifact (demo-grade)
        # ------------------------------------------------------------
        # print(f"applied: {applied}")
        artifact_path = write_final_artifact(
            event_id=event_id,
            repo=repo,
            findings=norm_findings,
            applied_patches=diffs,   # artifact stores unified diffs (human-facing)
            applied_diffs=diffs,     # explicit duplicate (API clarity)
            summary="Patches applied successfully.",
        )

        # ------------------------------------------------------------
        # Attach patch application info to output
        # ------------------------------------------------------------
        # print(f"applied: {applied}")
        review_output["applied_patches"] = applied
        review_output["applied_diffs"] = diffs
        review_output["artifact_path"] = artifact_path
        review_output["metadata"]["journal_path"] = journal_path

        return review_output
