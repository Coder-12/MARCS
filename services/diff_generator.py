# services/diff_generator.py
from __future__ import annotations
import os
import difflib
import json
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

# Configurable limits via env
MAX_DIFF_CHARS = int(os.environ.get("MACRS_MAX_DIFF_CHARS", "100000"))
ARTIFACT_DIR = os.environ.get("MACRS_ARTIFACT_DIR", "artifacts")
DEFAULT_CONTEXT_LINES = int(os.environ.get("MACRS_DIFF_CONTEXT_LINES", "3"))

def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def _normalize_lines(s: str) -> Tuple[list[str], bool]:
    """
    Normalize text into a list of lines WITHOUT trailing newlines removed.
    Returns (lines, is_binary_flag).
    We treat high-control or NUL presence as 'binary' for safety.
    """
    if s is None:
        s = ""
    # quick binary heuristic: NUL or very long non-newline runs
    if "\x00" in s:
        return ([], True)
    # normalize CRLF -> LF, splitlines keeps no trailing \n; we will re-add '\n' for unified_diff
    lines = s.replace("\r\n", "\n").split("\n")
    # when original had trailing newline, split will produce last element '', preserve pattern by re-adding '\n' later
    return lines, False

def generate_unified_diff(
    orig_text: Optional[str],
    new_text: Optional[str],
    path: str,
    context: int = DEFAULT_CONTEXT_LINES,
    from_prefix: str = "a/",
    to_prefix: str = "b/",
) -> str:
    """
    Produce a unified diff between orig_text and new_text for the file `path`.
    Returns a string that starts with --- a/path and +++ b/path and contains @@ hunks.
    If orig_text is None or empty, treat as new file (/dev/null style).
    If binary detected -> return a short message indicating binary diff not available.
    Enforces MAX_DIFF_CHARS; truncates with a comment if exceeded.
    """
    orig_text = orig_text or ""
    new_text = new_text or ""

    orig_lines, orig_bin = _normalize_lines(orig_text)
    new_lines, new_bin = _normalize_lines(new_text)

    # Binary or unknown -> short stub
    if orig_bin or new_bin:
        stub = f"--- {from_prefix}{path}\n+++ {to_prefix}{path}\n" \
               f"@@ BINARY FILE CHANGED @@\n"
        return stub if len(stub) <= MAX_DIFF_CHARS else (stub[:MAX_DIFF_CHARS] + "\n/* TRUNCATED */")

    # Build header paths
    fromfile = f"{from_prefix}{path}" if orig_text else "/dev/null"
    tofile = f"{to_prefix}{path}" if new_text else "/dev/null"
    # difflib.unified_diff expects lines WITH newline chars
    a_lines = [ln + ("\n" if i < len(orig_lines) - 1 or orig_text.endswith("\n") else "\n") for i, ln in enumerate(orig_lines)]
    b_lines = [ln + ("\n" if i < len(new_lines) - 1 or new_text.endswith("\n") else "\n") for i, ln in enumerate(new_lines)]

    diff_iter = difflib.unified_diff(
        a_lines,
        b_lines,
        fromfile=fromfile,
        tofile=tofile,
        lineterm="",
        n=context,
    )
    diff_text = "\n".join(list(diff_iter))

    # Enforce max size
    if len(diff_text) > MAX_DIFF_CHARS:
        diff_text = diff_text[:MAX_DIFF_CHARS] + "\n/* DIFF TRUNCATED */"

    return diff_text

def generate_repo_diff_from_applied(
    repo_root: str,
    applied_map: Dict[str, str],
    original_texts: Dict[str, str] = None
) -> Dict[str, str]:
    """
    Given a mapping filename -> patched_text (the text after applying patch),
    produce a mapping filename -> unified_diff (orig vs patched).
    If file did not exist before, orig_text is empty and diff will use /dev/null header.
    """
    # backward-compatible fallback
    if original_texts is None:
        original_texts = {}
        for fname in applied_map.keys():
            fpath = os.path.join(repo_root, fname)
            if os.path.exists(fpath):
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                    original_texts[fname] = fh.read()
            else:
                original_texts[fname] = ""

    diffs: Dict[str, str] = {}
    for fname, patched_text in applied_map.items():
        orig_text = original_texts.get(fname, "")
        # generate diff comparing orig -> patched_text
        diff = generate_unified_diff(orig_text, patched_text, path=fname)
        diffs[fname] = diff
    return diffs

def write_final_artifact(
    event_id: str,
    repo: str,
    findings: list,
    applied_patches: Dict[str, str] = None,
    applied_diffs: Dict[str, str] = None,
    summary: str = "",
    artifacts_dir: Optional[str] = None,
) -> str:
    """
    Write a final JSON artifact file summarizing the review and applied diffs.
    Returns path to artifact written (JSON).
    Artifact contains applied_patches mapping filename->diff (strings).
    """
    applied_patches = applied_patches or {}
    applied_diffs = applied_diffs or {}

    # --- BACKWARD COMPATIBILITY FIX ---
    # Old tests expect applied_patches to mirror applied_diffs keys.
    if not applied_patches and applied_diffs:
        applied_patches = {fname: "" for fname in applied_diffs.keys()}

    out_dir = artifacts_dir or ARTIFACT_DIR
    _ensure_dir(out_dir)
    ts = datetime.now(timezone.utc).isoformat()

    artifact = {
        "event_id": event_id,
        "repo": repo,
        "patches_applied": bool(applied_diffs),
        "applied_patches": applied_patches,
        "applied_diffs": applied_diffs,  # also preserve diffs
        "findings": findings,
        "summary": summary,
        "ts": ts,
    }

    filename = f"{event_id}.artifact.json"
    path = os.path.join(out_dir, filename)

    # atomic write
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, ensure_ascii=False, indent=2) # type: ignore[arg-type]
    os.replace(tmp, path)
    return path