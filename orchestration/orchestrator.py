# orchestration/orchestrator.py
from __future__ import annotations
import os
import json
import logging
import tempfile
from typing import Dict, Any, Optional, List, Tuple

from services import reviewer as reviewer_module
from services.patch_applier import apply_patches
from journals import journal as journal_mod
from core.logging import get_logger

from core.orchestrator_logging import configure_orchestrator_logger
import time
import uuid
import re

from core.report import generate_html_report  # new import

logger = get_logger("orchestrator") if hasattr(__import__("core.logging"), "get_logger") else logging.getLogger("orchestrator")

# Safety defaults
DEFAULT_ARTIFACT_DIR = os.environ.get("MACRS_ARTIFACT_DIR", "artifacts")
MAX_PATCH_CHARS = int(os.environ.get("MACRS_MAX_PATCH_CHARS", "20000"))

# Safety thresholds (easy to tune via env if desired)
LARGE_PATCH_LINE_THRESHOLD = int(os.environ.get("MACRS_LARGE_PATCH_LINES", "200"))
SENSITIVE_FILENAMES = {"requirements.txt", "pyproject.toml", "setup.py", "package.json"}

class DemoOrchestrator:
    """
    Orchestrator that runs a review cycle for a repo/event:
      - Optionally run reviewer to create a review (calling services.reviewer.Reviewer)
      - Present suggested patches (preview)
      - Optionally apply selected patches using services.patch_applier.apply_patches
      - Write artifacts (review.json, applied.json) and return summary
    """
    def __init__(self, artifact_dir: str | None = None, logger: Optional[logging.Logger] = None):
        self.session_id = str(uuid.uuid4())

        self.artifact_dir = artifact_dir or DEFAULT_ARTIFACT_DIR
        os.makedirs(self.artifact_dir, exist_ok=True)

        # configure orchestrator logger (idempotent)
        # Allow override via env MACRS_ORCH_LOG_PATH
        configure_orchestrator_logger()  # uses default or env path

        # FIXED: use provided logger OR module-level logger
        # self.logger = logger if logger is not None else get_logger("orchestrator")
        self.logger = logger if logger is not None else logging.getLogger("orchestrator")

        self._latest_safety_map = {}

        # small internal state
        self._start_ts = None

    def log_event(self, event: str, event_id: str | None = None, **fields: Any) -> None:
        """
        Helper to emit a structured log entry to orchestrator logger.
        fields will be attached as record attributes so JSONFormatter captures them.
        """
        entry = {"event": event, "session_id": self.session_id}
        if event_id:
            entry["event_id"] = event_id
        entry.update(fields)
        # attach elapsed if we started timer
        if self._start_ts is not None:
            entry["_elapsed_ms"] = int((time.time() - self._start_ts) * 1000)
        # use logger.info with extra to ensure JSONFormatter records fields
        try:
            self.logger.info(json.dumps(entry, ensure_ascii=False), extra=fields)
        except Exception:
            # fallback simple logging
            self.logger.info(f"{event} {entry}")

    # -----------------------
    # NEW: safety checks
    # -----------------------
    def run_safety_checks(self, filename: str, patch_text: str) -> List[str]:
        """
        Run lightweight safety checks on a patch. Returns list of warning codes (strings).
        Non-blocking; caller should log or surface warnings.
        Checks performed (minimal Mode-B):
          - large_patch: > LINES threshold (inserts + deletes)
          - sensitive_file: filename is one of SENSITIVE_FILENAMES
          - dangerous_code: inserted lines contain suspicious patterns (os.system, exec, eval,
                            subprocess.run(..., shell=True))
        """
        warnings: List[str] = []

        # 1) sensitive filename
        base = os.path.basename(filename or "")
        if base in SENSITIVE_FILENAMES:
            warnings.append("sensitive_file")

        # Count inserted/deleted lines and hunks
        added = 0
        removed = 0
        hunk_count = 0

        # Normalize lines for simple checks
        lines = patch_text.splitlines()
        for ln in lines:
            # count hunks (unified diff hunks start with @@)
            if ln.startswith("@@"):
                hunk_count += 1
            # ignore triple-prefix lines (+++ / ---) when counting +/- for content
            if ln.startswith("+++ ") or ln.startswith("--- "):
                continue
            if ln.startswith("+") and not ln.startswith("+++"):
                added += 1
            elif ln.startswith("-") and not ln.startswith("---"):
                removed += 1

        total_changed = added + removed
        if total_changed >= LARGE_PATCH_LINE_THRESHOLD:
            warnings.append("large_patch")

        # 3) dangerous code detections: scan added lines for suspicious patterns
        added_lines = [ln[1:] for ln in lines if ln.startswith("+") and not ln.startswith("+++")]
        added_blob = "\n".join(added_lines)

        # common suspicious patterns
        if re.search(r"\bos\.system\s*\(", added_blob):
            warnings.append("dangerous_code:os.system")
        if re.search(r"\bexec\s*\(", added_blob):
            warnings.append("dangerous_code:exec")
        if re.search(r"\beval\s*\(", added_blob):
            warnings.append("dangerous_code:eval")
        # subprocess.run with shell=True (naive check: both appear)
        if "subprocess.run" in added_blob and "shell=True" in added_blob:
            warnings.append("dangerous_code:subprocess_shell")

        # compact reasons: if any dangerous_code:* present, collapse to generic for summary logs
        dangerous_reasons = [r for r in warnings if r.startswith("dangerous_code")]
        if dangerous_reasons and "dangerous_code" not in warnings:
            warnings.append("dangerous_code")

        # Also provide some telemetry-style summary info (not part of warnings)
        # log patch metrics (size, hunks, added/removed)
        try:
            self.log_event("patch_metrics", event_id=None, patch=filename,
                           lines_changed=total_changed, added=added, removed=removed, hunks=hunk_count)
        except Exception:
            # best-effort, do not raise
            pass

        return warnings

    async def run_review(self, event: Dict[str, Any], llm_client: Optional[Any] = None, reviewer: Optional[Any] = None) -> Dict[str, Any]:
        """
        Run reviewer on `event`. Returns review dict. If `reviewer` instance provided, it will be used.
        The reviewer must expose an async method `review(event)` OR `reviewer.review_event(event)`; we attempt plausible calls.
        """
        # Start timer if not started
        if self._start_ts is None:
            self._start_ts = time.time()
            self.log_event("demo_start", event_id=event.get("event_id"), repo_root=event.get("repo_root"))
            self.log_event("review_requested", event_id=event.get("event_id"))
            self.log_event("review_phase_started", event_id=event.get("event_id"))

        # If review supplied in event, skip running LLM
        if event.get("review"):
            self.log_event("review_loaded_from_event", event_id=event.get("event_id"), num_findings=len(event["review"].get("findings", [])))
            self.log_event("review_phase_finished", event_id=event.get("event_id"))
            return event["review"]

        # Acquire reviewer instance
        rv = reviewer or getattr(reviewer_module, "Reviewer", None)
        if rv is None:
            raise RuntimeError("No reviewer available (services.reviewer.Reviewer missing)")

        # instantiate if class
        if isinstance(rv, type):
            rv_instance = rv(llm_client=llm_client) if llm_client is not None else rv()
        else:
            rv_instance = rv

        self.log_event("review_phase_finished", event_id=event.get("event_id"))

        # call the likely async API (try a few names)
        if hasattr(rv_instance, "run_review"):
            review = await rv_instance.run_review(event)
        elif hasattr(rv_instance, "review_event"):
            review = await rv_instance.review_event(event)
        elif hasattr(rv_instance, "review"):
            maybe = rv_instance.review(event)
            if getattr(maybe, "__await__", None):
                review = await maybe
            else:
                review = maybe
        else:
            raise RuntimeError("Reviewer instance has no supported review method")

        return review

    def _summarize_patches(self, review: Dict[str, Any]) -> List[Tuple[str, str]]:
        """
        Extract suggested patches and auto-detect filenames from patch headers.
        Returns list of (filename, patch_text).
        """
        sp = review.get("suggested_patches") or {}
        out = []

        for patch_id, patch_text in sp.items():
            if not isinstance(patch_text, str):
                continue

            # autodetect: look for lines starting with '--- ' and '+++ '
            filename = None
            for line in patch_text.splitlines():
                line = line.strip()
                if line.startswith("+++ "):
                    # format: +++ b/filename.py  OR +++ filename.py
                    part = line[4:].strip()
                    # remove leading a/ or b/
                    if part.startswith("a/") or part.startswith("b/"):
                        part = part[2:]
                    filename = part
                    break

            if not filename:
                # fallback: assume patch_id is filename
                filename = patch_id

            out.append((filename, patch_text))

            warnings = self.run_safety_checks(filename, patch_text)
            if warnings:
                self.log_event(
                    "patch_safety_warnings",
                    patch=filename,
                    warnings=warnings,
                )

        return out

    def render_patch_preview(self, patches: List[Tuple[str, str]]) -> str:
        out = []
        for pid, txt in patches:
            short = txt if len(txt) < 400 else txt[:400] + "\n... (truncated)"
            out.append(f"PATCH {pid}:\n{short}\n---\n")
        return "\n".join(out)

    def write_artifacts(self, event_id: str, review: Dict[str, Any], apply_result: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """
        Write JSON artifacts under artifact_dir/<event_id>/
        """
        d = os.path.join(self.artifact_dir, event_id)
        os.makedirs(d, exist_ok=True)

        # Optionally embed safety warnings
        if hasattr(self, "_latest_safety_map"):
            review["_safety_warnings"] = self._latest_safety_map

        review_path = os.path.join(d, "review.json")
        with open(review_path, "w", encoding="utf-8") as fh:
            json.dump(review, fh, indent=2, ensure_ascii=False)

        out = {"review": review_path}

        if apply_result is not None:
            applied_path = os.path.join(d, "apply_result.json")
            with open(applied_path, "w", encoding="utf-8") as fh:
                json.dump(apply_result, fh, indent=2, ensure_ascii=False)
            out["apply_result"] = applied_path

        # Generate human-friendly HTML report (best-effort; errors are non-fatal)
        try:
            report_path = generate_html_report(event_id, review, apply_result or {}, self.artifact_dir)
            out["report"] = report_path
            # also log event
            self.log_event("report_generated", event_id=event_id, report=report_path)
        except Exception as exc:
            # do not fail orchestrator for report generation issues
            self.log_event("report_generation_failed", event_id=event_id, error=str(exc))

        return out

    def validate_patch_sizes(self, patches: List[Tuple[str, str]]):
        for pid, txt in patches:
            if not isinstance(txt, str):
                raise ValueError(f"patch {pid} not a string")
            if len(txt) > MAX_PATCH_CHARS:
                raise ValueError(f"patch {pid} exceeds MAX_PATCH_CHARS ({MAX_PATCH_CHARS})")

    def apply_selected(self, event_id: str, repo_root: str, selected_patches: Dict[str, str], dry_run: bool = True) -> Dict[str, Any]:
        """
        Use existing apply_patches to apply selected patches. Returns the result dict from apply_patches.
        By default, dry_run=True to be safe; caller may pass dry_run=False to actually apply.
        """
        # safety: ensure event id exists
        if not event_id:
            raise ValueError("event_id required")
        if not selected_patches:
            return {"ok": True, "reason": "no_patches_selected", "applied": {}}

        # run apply_patches (this will journal)
        res = apply_patches(event_id, repo_root, selected_patches, dry_run=dry_run)

        # apply success / error telemetry
        if res.get("applied"):
            self.log_event("apply_success", event_id=event_id, applied=list(res["applied"].keys()))

        if res.get("dropped"):
            self.log_event("apply_conflict", event_id=event_id, dropped=res["dropped"])

        if res.get("errors"):
            self.log_event("apply_error", event_id=event_id, errors=res["errors"])
        return res

    def interactive_choose(self, patches: List[Tuple[str, str]]) -> Dict[str, str]:
        """
        Simple textual interactive chooser (local CLI). Returns mapping patch_id -> patch_text selected to apply.
        """
        chosen: Dict[str, str] = {}
        if not patches:
            return chosen

        print("Detected suggested patches:")
        for i, (pid, txt) in enumerate(patches, start=1):
            print(f"\n[{i}] patch id: {pid}\n---\n{txt[:1000]}\n---\n")
            while True:
                ans = input("Apply this patch? [y/n/a(all)/q(quit)]: ").strip().lower()
                if ans in ("y", "yes"):
                    chosen[pid] = txt
                    break
                if ans in ("n", "no"):
                    break
                if ans in ("a", "all"):
                    # add this and all remaining
                    for pid2, txt2 in patches[i-1:]:
                        chosen[pid2] = txt2
                    return chosen
                if ans in ("q", "quit"):
                    return chosen
                print("Please answer y/n/a/q.")
        return chosen