# services/patch_applier.py
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import difflib
import json
from typing import Dict, Any, List, Tuple, Optional

from journals import journal
from datetime import datetime, timezone

# Safety limits (env override)
MAX_PATCH_CHARS = int(os.environ.get("MACRS_MAX_PATCH_CHARS", "20000"))
MAX_HUNKS_PER_FILE = int(os.environ.get("MACRS_MAX_HUNKS_PER_FILE", "100"))
ARTIFACT_DIR = os.environ.get("MACRS_ARTIFACT_DIR", "artifacts")

# Dangerous patterns (again; conservative)
_DANGEROUS_RE = re.compile(r"\brm\s+-rf\b|\brm\s+-r\b|:\s*/\s*$", re.IGNORECASE)

# at top
def get_backup_dir() -> str:
    return os.environ.get("MACRS_BACKUP_DIR", "backups")  # BACKUP_DIR is fallback default


class PatchApplyError(Exception):
    pass

# -------------------------
# Unified diff parser
# -------------------------
HUNK_HDR_RE = re.compile(r"@@\s*-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@")

def parse_unified_diff(diff_text: str) -> Dict[str, Any]:
    """
    Parse a unified diff into file->hunks mapping.

    Returns:
        {
            "files": {
                "path": {
                    "hunks": [
                        {"old_start": int, "old_len": int, "new_start": int, "new_len": int, "lines": [str] }
                    ],
                    "fromfile": "a/...", "tofile": "b/..."
                },
                ...
            }
        }
    """
    if not diff_text or len(diff_text) > MAX_PATCH_CHARS:
        raise PatchApplyError("patch_too_large_or_empty")

    lines = diff_text.splitlines()
    files: Dict[str, Any] = {}
    cur_file: Optional[str] = None
    cur_hunk: Optional[Dict[str, Any]] = None

    i = 0
    while i < len(lines):
        ln = lines[i]
        # header lines: --- a/path   +++ b/path
        if ln.startswith("--- "):
            fromfile = ln[4:].strip()
            # expect next line to be +++
            i += 1
            if i < len(lines) and lines[i].startswith("+++ "):
                tofile = lines[i][4:].strip()
            else:
                tofile = None
            # derive a canonical path from tofile or fromfile
            path = tofile or fromfile
            if path is None:
                i += 1
                continue
            # strip leading a/ or b/
            path = path[2:] if (path.startswith("a/") or path.startswith("b/")) else path
            cur_file = path
            files[cur_file] = {"fromfile": fromfile, "tofile": tofile, "hunks": []}
            cur_hunk = None
            i += 1
            continue

        # allow leading whitespace or BOM or stray chars
        clean_ln = ln.lstrip("\ufeff \t")
        m = HUNK_HDR_RE.match(clean_ln)
        if m and cur_file is not None:
            old_start = int(m.group(1))
            old_len = int(m.group(2) or "1")
            new_start = int(m.group(3))
            new_len = int(m.group(4) or "1")
            cur_hunk = {"old_start": old_start, "old_len": old_len,
                        "new_start": new_start, "new_len": new_len,
                        "lines": []}
            files[cur_file]["hunks"].append(cur_hunk)
            i += 1
            # gather hunk body lines until next hunk or file header
            while i < len(lines):
                ln2 = lines[i]

                # print(f"ln2: {ln2}")

                # new hunk header
                if ln2.startswith("@@"):
                    break

                # stop when we hit start of next file's diff header
                # next file header
                if ln2.startswith("--- ") and (i + 1 < len(lines) and lines[i + 1].startswith("+++ ")):
                    break

                # accept ONLY valid hunk body lines
                # Strip ONLY BOM, preserve leading whitespace prefixes
                # line = ln2.lstrip("\ufeff \t")
                line = ln2[1:] if ln2.startswith("\ufeff") else ln2
                if len(line) and line[0] in (" ", "+", "-"): # type: ignore[arg-type]
                    # print(f"ln2: {line}")
                    cur_hunk["lines"].append(line)
                    i += 1
                    continue

                # ignore blank lines inside hunk (valid in diffs)
                if ln2 == "":
                    cur_hunk["lines"].append(" ")  # treat as context line
                    i += 1
                    continue

                # otherwise stop (safety)
                break
            continue

        i += 1

    # enforce hunk limits
    for p, info in list(files.items()):
        if len(info["hunks"]) > MAX_HUNKS_PER_FILE:
            raise PatchApplyError("too_many_hunks")
    return {"files": files}


# -------------------------
# Apply hunk algorithm (best-effort exact-match)
# -------------------------
def _apply_hunks_to_lines(orig_lines: List[str], hunks: List[Dict[str, Any]]) -> Tuple[List[str], bool]:
    """
    Apply hunks to orig_lines.
    Now supports new-file creation patches (old_start = 0, old_len = 0).
    """
    # NEW FILE CASE: if all hunks are new-file style
    if all(h["old_start"] == 0 and h["old_len"] == 0 for h in hunks):
        new_lines: List[str] = []
        for h in hunks:
            for ln in h["lines"]:
                if ln.startswith("+"):
                    new_lines.append(ln[1:])
        return new_lines, True

    # ---------------------------------------------------
    # NORMAL PATCH APPLY (original implementation)
    # ---------------------------------------------------
    patched = orig_lines[:]
    offset = 0

    for h in hunks:
        old_start = h["old_start"] - 1
        idx = old_start + offset

        # expected old lines
        expected_old = [ln[1:] for ln in h["lines"] if ln.startswith(" ") or ln.startswith("-")]
        new_section = [ln[1:] for ln in h["lines"] if ln.startswith(" ") or ln.startswith("+")]

        if idx < 0 or idx + len(expected_old) > len(patched):
            return orig_lines, False

        slice_len = len(expected_old)
        existing_slice = patched[idx: idx + slice_len]

        # ------------------------------------------------------------
        # Robust context matcher (newline + whitespace tolerant)
        # ------------------------------------------------------------
        norm_existing = [x.rstrip() for x in existing_slice]
        norm_expected = [x.rstrip() for x in expected_old]

        # print(f"norm_existing: {norm_existing}")
        # print(f"norm_expected: {norm_expected}")

        # Direct normalized match
        if norm_existing != norm_expected:
            # allow len==1 context fuzz
            if len(norm_expected) == 1 and norm_expected[0] in norm_existing:
                pass
            else:
                sm = difflib.SequenceMatcher(
                    a="\n".join(norm_existing),
                    b="\n".join(norm_expected)
                )
                if sm.ratio() < 0.8:
                    return orig_lines, False

        patched[idx: idx + slice_len] = new_section
        offset += len(new_section) - slice_len

    return patched, True


# -------------------------
# High-level apply helpers
# -------------------------
def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def _write_atomic(path: str, data: str):
    _ensure_dir(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.replace(tmp, path)


# where you _ensure_dir(BACKUP_DIR) -> replace with get_backup_dir()
def _backup_file(event_id: str, src_path: str) -> str:
    bdir = get_backup_dir()
    _ensure_dir(bdir)
    event_backup_dir = os.path.join(bdir, event_id)
    _ensure_dir(event_backup_dir)
    base = os.path.basename(src_path)
    dest = os.path.join(event_backup_dir, base + ".bak")
    shutil.copy2(src_path, dest)
    return dest


def apply_patches(
    event_id: str,
    repo_root: str,
    suggested_patches: Dict[str, str],
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Attempt to apply suggested_patches (mapping filename->diff_text) to files under repo_root.
    Returns a dict with:
      - applied: mapping filename->diff (if applied)
      - dropped: mapping filename->reason (if dropped)
      - backups: mapping filename->backup_path
      - journal_id: event_id (journaling path is journals/<event_id>.jsonl)
    """
    journal.clear_journal(event_id)
    journal.append_entry(event_id, "start", {"repo_root": repo_root, "num_patches": len(suggested_patches)})
    result = {"applied": {}, "dropped": {}, "backups": {}, "journal": journal.journal_path(event_id), "errors": []}

    # safety checks & parse
    parsed_by_file = {}
    original_texts = {}  # ADD THIS at the top of apply_patches
    for fname, patch_text in suggested_patches.items():
        if not isinstance(patch_text, str) or not patch_text.strip():
            result["dropped"][fname] = "empty"
            journal.append_entry(event_id, "dropped_patch", {"file": fname, "reason": "empty"})
            continue
        if len(patch_text) > MAX_PATCH_CHARS:
            result["dropped"][fname] = "too_large"
            journal.append_entry(event_id, "dropped_patch", {"file": fname, "reason": "too_large"})
            continue
        if _DANGEROUS_RE.search(patch_text):
            result["dropped"][fname] = "dangerous_content"
            journal.append_entry(event_id, "dropped_patch", {"file": fname, "reason": "dangerous_content"})
            continue
        try:
            # print(f"_patch_text: {patch_text}")
            parsed = parse_unified_diff(patch_text)
            # print(f"_parsed: {parsed}")
            # ensure that parsed refers to the expected filename; otherwise keep the patch but map it
            parsed_by_file[fname] = parsed
            journal.append_entry(event_id, "parsed_patch", {"file": fname, "ok": True})
        except Exception as e:
            result["dropped"][fname] = "parse_error"
            journal.append_entry(event_id, "dropped_patch", {"file": fname, "reason": "parse_error"})
            continue

    # Validate availability of target files and dry-run apply
    to_apply = []
    print(f"_parsed_by_file: {parsed_by_file}")
    for fname, parsed in parsed_by_file.items():
        target_path = os.path.join(repo_root, fname)
        if not os.path.exists(target_path):
            # If patch intends to add new file, ok: treat original as empty
            orig_text = ""
        else:
            with open(target_path, "r", encoding="utf-8", errors="ignore") as fh:
                orig_text = fh.read()

        # remove BOMs and DO NOT allow leading empty line
        print(f"_orig_text: {orig_text}")
        cleaned = orig_text.lstrip("\ufeff")
        # remove ONLY ONE accidental leading newline, not all
        if cleaned.startswith("\n"):
            cleaned = cleaned[1:]
        orig_lines = cleaned.rstrip("\n").split("\n")

        # For each parsed file candidate within parsed, find hunks for the matched file name
        # parsed has "files" mapping that may include different path keys (we choose first)
        files_map = parsed.get("files", {})
        # find the parsed entry that best matches fname (basename match)
        candidate_key = None
        for pk in files_map.keys():
            if os.path.basename(pk) == os.path.basename(fname) or pk.endswith(fname):
                candidate_key = pk
                break
        if candidate_key is None:
            # fallback to first parsed file
            candidate_key = next(iter(files_map.keys()))
        file_entry = files_map[candidate_key]
        hunks = file_entry.get("hunks", [])
        if not hunks:
            # nothing to do
            result["dropped"][fname] = "no_hunks"
            journal.append_entry(event_id, "dropped_patch", {"file": fname, "reason": "no_hunks"})
            continue

        # dry-run application
        # print(f"_orig_lines: {orig_lines}")
        # print(f"_hunks: {hunks}")
        patched_lines, ok = _apply_hunks_to_lines(orig_lines, hunks)
        if not ok:
            result["dropped"][fname] = "conflict"
            journal.append_entry(event_id, "dropped_patch", {"file": fname, "reason": "conflict"})
            continue

        # pass validation, mark for apply
        # print(f"_patched_lines: {patched_lines}")
        patched_text = "\n".join(patched_lines)
        if patched_text and not patched_text.endswith("\n"):
            patched_text += "\n"
        to_apply.append((fname, target_path, patched_text))

    # If only dry-run requested -> return validation summary
    if dry_run:
        journal.append_entry(event_id, "dry_run_complete", {"apply_count": len(to_apply), "dropped": list(result["dropped"].keys())})
        result["dry_run"] = True
        return result

    # Apply sequence: backup -> write patched file -> atomic replace -> journal
    applied = {}
    backups = {}
    try:
        for fname, target_path, patched_text in to_apply:
            # if original exists -> backup
            if os.path.exists(target_path):
                bkp = _backup_file(event_id, target_path)
                backups[fname] = bkp
                # NEW — capture original BEFORE overwrite
                with open(target_path, "r", encoding="utf-8") as fh:
                    original_texts[fname] = fh.read()
                journal.append_entry(event_id, "backup_created", {"file": fname, "backup": bkp})
            else:
                original_texts[fname] = ""  # new file case
                # ensure directory exists for new file
                os.makedirs(os.path.dirname(target_path) or repo_root, exist_ok=True)
                bkp = None
            # write patched text to temp in same dir then atomic replace
            _write_atomic(target_path, patched_text)
            journal.append_entry(event_id, "file_replaced", {"file": fname, "path": target_path})
            applied[fname] = patched_text
        result["applied"] = applied
        result["backups"] = backups
        result["original_texts"] = original_texts  # NEW
        journal.append_entry(event_id, "commit", {"applied_files": list(applied.keys())})
        return result
    except Exception as e:
        # on any error -> attempt rollback for replaced files
        journal.append_entry(event_id, "error", {"error": str(e)})
        # rollback replaced files using backups if available; otherwise remove written file
        for fname in list(applied.keys()):
            target_path = os.path.join(repo_root, fname)
            bkp = backups.get(fname)
            if bkp and os.path.exists(bkp):
                shutil.copy2(bkp, target_path)
                journal.append_entry(event_id, "rollback_replaced", {"file": fname, "restored_from": bkp})
            else:
                # remove file if there was no backup
                if os.path.exists(target_path):
                    os.remove(target_path)
                    journal.append_entry(event_id, "rollback_removed", {"file": fname})
        result["errors"].append(str(e))
        result["applied"] = applied
        result["backups"] = backups
        return result