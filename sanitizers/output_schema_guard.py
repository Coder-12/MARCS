# sanitizers/output_schema_guard.py
from __future__ import annotations
import re
import json
import logging
from typing import Any, Dict, Tuple, List, Optional

logger = logging.getLogger("sanitizers.output_schema_guard")

# Defaults & limits (tunable via env later if needed)
MAX_PATCH_CHARS = int(__import__("os").environ.get("MACRS_MAX_PATCH_CHARS", "5000"))
MAX_PATCH_LINES = int(__import__("os").environ.get("MACRS_MAX_PATCH_LINES", "1000"))
ALLOWED_TOP_KEYS = {"findings", "suggested_patches", "summary", "metadata"}

# small helpers
_severity_map = {
    "critical": "high",
    "high": "high",
    "warn": "medium",
    "warning": "medium",
    "medium": "medium",
    "med": "medium",
    "low": "low",
    "info": "low",
}

def _normalize_severity(val: Any) -> str:
    if isinstance(val, str):
        v = val.strip().lower()
        return _severity_map.get(v, "low")
    if isinstance(val, (int, float)):
        # map numeric to buckets
        try:
            n = float(val)
            if n >= 0.75:
                return "high"
            if n >= 0.4:
                return "medium"
            return "low"
        except Exception:
            return "low"
    return "low"

def _normalize_confidence(val: Any) -> float:
    if isinstance(val, (int, float)):
        try:
            f = float(val)
            return max(0.0, min(1.0, f))
        except Exception:
            return 0.5
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("high", "h"):
            return 0.9
        if s in ("medium", "m", "mid"):
            return 0.6
        if s in ("low", "l"):
            return 0.3
        try:
            f = float(s)
            return max(0.0, min(1.0, f))
        except Exception:
            return 0.5
    return 0.5

def _safe_string(val: Any, max_len: int = 4000) -> str:
    if val is None:
        return ""
    if not isinstance(val, str):
        try:
            val = json.dumps(val, ensure_ascii=False)
        except Exception:
            val = str(val)
    # ensure no control NULs
    val = val.replace("\x00", "")
    # trim
    if len(val) > max_len:
        return val[:max_len] + " …[TRUNCATED]"
    return val

# ---------- patch safety checks ----------
# Very conservative checks: disallow obviously dangerous content or huge diffs.
_dangerous_patterns = [
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\brm\s+-r\b", re.IGNORECASE),
    re.compile(r":\s*/\s*$"),  # attempts to reference root
    re.compile(r"(?i)\bshutdown\b"),
    re.compile(r"(?i)\breboot\b"),
    # large binary blobs (base64-looking long contiguous chars > 200)
    re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"),
]

def _check_patch_safety(patch_text: str) -> Tuple[bool, str]:
    if not isinstance(patch_text, str) or len(patch_text) == 0:
        return False, "empty"
    if len(patch_text) > MAX_PATCH_CHARS:
        return False, "too_long"
    lines = patch_text.splitlines()
    if len(lines) > MAX_PATCH_LINES:
        return False, "too_many_lines"
    # detect dangerous commands / patterns
    for pat in _dangerous_patterns:
        if pat.search(patch_text):
            return False, "dangerous_content"
    # Disallow full-file replacements that look like >80% new file with no diff markers
    # (heuristic)
    added_lines = sum(1 for l in lines if l.startswith("+") or l.startswith(">") or l.startswith(">>>"))
    if added_lines > 0 and added_lines / max(1, len(lines)) > 0.95 and len(lines) > 200:
        return False, "large_full_replacement"
    return True, "ok"

# ---------- top-level validation ----------
def validate_and_heal_review_json(parsed: Any, repo: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    STRICT schema guard & healer.

    Inputs:
      - parsed: raw parsed JSON (could be dict, list, etc.)
      - repo: repo name for deterministic id generation

    Returns:
      - healed: dict enforced to only contain expected keys with normalized contents
      - meta: diagnostics about repairs and patch safety
    """
    meta: Dict[str, Any] = {
        "repaired": [],
        "patch_safety": {},
        "original_type": type(parsed).__name__,
    }

    # If we got a list of findings (common case), wrap it.
    if isinstance(parsed, list):
        # If list of dicts -> assume findings list
        if all(isinstance(el, dict) for el in parsed):
            parsed = {"findings": parsed}
            meta["repaired"].append("wrapped_list_into_findings")
        else:
            # take first dict-like element if available
            picked = None
            for el in parsed:
                if isinstance(el, dict):
                    picked = el
                    break
            parsed = picked or {}
            meta["repaired"].append("picked_first_dict_from_list")

    if not isinstance(parsed, dict):
        meta["repaired"].append("coerced_to_empty_dict")
        parsed = {}

    # Strict top-level: only keep allowed keys
    kept = {}
    for k in ("findings", "suggested_patches", "summary"):
        if k in parsed:
            kept[k] = parsed[k]
    # if "findings" absent, ensure empty list
    if "findings" not in kept:
        kept["findings"] = []
        meta["repaired"].append("added_missing_findings")
    if "suggested_patches" not in kept:
        kept["suggested_patches"] = {}
        meta["repaired"].append("added_missing_suggested_patches")
    if "summary" not in kept:
        kept["summary"] = ""
        meta["repaired"].append("added_missing_summary")

    # Normalize findings: enforce list of dicts with keys
    norm_findings: List[Dict[str, Any]] = []
    for i, f in enumerate(kept.get("findings") or []):
        if not isinstance(f, dict):
            meta["repaired"].append(f"skipped_non_dict_finding_index_{i}")
            continue
        fid = f.get("id") or f"{repo.replace('/', '_')}-f{i+1}"
        category = _safe_string(f.get("category", "style"), max_len=100)
        severity = _normalize_severity(f.get("severity", "low"))
        message = _safe_string(f.get("message", ""), max_len=1000)
        explanation = _safe_string(f.get("explanation", ""), max_len=4000)
        confidence = _normalize_confidence(f.get("confidence", 0.5))
        norm_findings.append({
            "id": fid,
            "category": category,
            "severity": severity,
            "message": message,
            "explanation": explanation,
            "confidence": confidence,
        })

    kept["findings"] = norm_findings

    # suggested_patches: ensure mapping filename|finding_id -> string patch,
    # also check patch safety and drop unsafe patches (record reason)
    norm_patches: Dict[str, str] = {}
    patch_meta: Dict[str, Dict[str, str]] = {}
    sp = kept.get("suggested_patches") or {}
    if isinstance(sp, dict):
        for k, v in sp.items():
            txt = v if isinstance(v, str) else (_safe_string(v, max_len=MAX_PATCH_CHARS))
            ok, reason = _check_patch_safety(txt)
            if ok:
                # Keep, but trim to limit
                if len(txt) > MAX_PATCH_CHARS:
                    txt = txt[:MAX_PATCH_CHARS] + "\n/* TRUNCATED */"
                    patch_meta[k] = {"ok": False, "reason": "truncated"}
                else:
                    patch_meta[k] = {"ok": True, "reason": "ok"}
                norm_patches[k] = txt
            else:
                patch_meta[k] = {"ok": False, "reason": reason}
                # OMIT unsafe patch
                meta["repaired"].append(f"dropped_patch_{k}_{reason}")
    else:
        # if suggested_patches was not a dict, ignore it
        meta["repaired"].append("coerced_non_dict_suggested_patches_to_empty")

    kept["suggested_patches"] = norm_patches

    # Summary must be a short string
    kept["summary"] = _safe_string(kept.get("summary", ""), max_len=1000)

    meta["patch_safety"] = patch_meta

    # Final healed object
    healed = {
        "findings": kept["findings"],
        "suggested_patches": kept["suggested_patches"],
        "summary": kept["summary"],
    }

    return healed, meta