# sanitizers/reviewer_extractor_validator.py
from __future__ import annotations
import re
import hashlib
import json
from typing import Dict, Any, Tuple, List, Optional

# heuristics
_UNIFIED_HUNK_RE = re.compile(r"^@@\s+-[0-9]+(?:,[0-9]+)?\s+\+[0-9]+(?:,[0-9]+)?\s+@@", re.MULTILINE)
_DIFF_FILE_MARKER_RE = re.compile(r"^(--- |\+\+\+ )", re.MULTILINE)
_PLUS_LINE_RE = re.compile(r"^\+", re.MULTILINE)

def _sha256_text(s: str) -> str:
    h = hashlib.sha256()
    h.update(s.encode("utf-8"))
    return h.hexdigest()

def _looks_like_diff(patch_text: str) -> bool:
    if not patch_text or not isinstance(patch_text, str):
        return False
    if _UNIFIED_HUNK_RE.search(patch_text):
        return True
    if _DIFF_FILE_MARKER_RE.search(patch_text):
        return True
    # fallback - any added-line markers
    if _PLUS_LINE_RE.search(patch_text):
        return True
    return False

def cross_validate(
    healed: Dict[str, Any],
    prompt_files: Dict[str, str],
    cap_meta: Dict[str, Any],
    repo: str = "",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Cross-validate reviewer output (healed) against extractor output (prompt_files).
    Returns (healed_out, validator_meta).

    validator_meta contains:
      - file_hashes: {filename: sha256}
      - dropped_patches: {k: reason}
      - kept_patches: [k,...]
      - finding_file_mismatches: [...]
      - prompt_cap: cap_meta (forwarded)
    """
    validator_meta: Dict[str, Any] = {
        "file_hashes": {},
        "dropped_patches": {},
        "kept_patches": [],
        "finding_file_mismatches": [],
        "prompt_cap": cap_meta,
        "original_keys": list((healed.get("suggested_patches") or {}).keys()),
    }

    # compute file hashes for prompt_files (the files actually reviewed)
    for fn, txt in (prompt_files or {}).items():
        try:
            validator_meta["file_hashes"][fn] = _sha256_text(txt or "")
        except Exception:
            validator_meta["file_hashes"][fn] = None

    # normalize patches container
    sp: Dict[str, str] = {}
    raw_sp = healed.get("suggested_patches") or {}
    if not isinstance(raw_sp, dict):
        raw_sp = {}

    # collect finding ids
    findings_by_id = {}
    for f in healed.get("findings") or []:
        fid = f.get("id")
        if fid:
            findings_by_id[str(fid)] = f

    # Validate each patch entry
    for k, v in raw_sp.items():
        reason = None
        ok = True
        # If key matches a filename or exact path in prompt_files -> treat as file patch
        if k in prompt_files:
            # ensure patch has some hunks / markers
            if not _looks_like_diff(v):
                ok = False
                reason = "no_hunk"
        elif k in findings_by_id:
            # patch keyed by finding id -> ensure patch contains target file path or hunks
            if not _looks_like_diff(v):
                ok = False
                reason = "no_hunk"
            else:
                # optional: try to detect file target mentioned in patch headers
                # if none, still accept but note heuristic
                if not (_DIFF_FILE_MARKER_RE.search(v) or "=== " in v):
                    # keep but note "no_file_marker"
                    reason = "no_file_marker"
        else:
            # key not recognized: it might be a file path not present, or malformed id
            ok = False
            reason = "missing_target"

        # check length limits (defensive)
        if ok and isinstance(v, str) and len(v) > 20000:
            ok = False
            reason = "too_long"

        if ok:
            sp[k] = v
            validator_meta["kept_patches"].append(k)
            # if reason present but ok (like no_file_marker), still record it
            if reason:
                validator_meta["dropped_patches"][k] = {"ok": True, "reason": reason}
        else:
            validator_meta["dropped_patches"][k] = {"ok": False, "reason": reason}

    # Replace suggested_patches with validated set
    healed_out = dict(healed)
    healed_out["suggested_patches"] = sp

    # Check findings referencing files: if finding claims "file" key but file not present -> record
    for f in healed_out.get("findings") or []:
        file_claim = f.get("file")
        if file_claim and file_claim not in prompt_files:
            validator_meta["finding_file_mismatches"].append({
                "id": f.get("id"),
                "claimed_file": file_claim,
                "reason": "missing_in_extracted_files"
            })

    return healed_out, validator_meta