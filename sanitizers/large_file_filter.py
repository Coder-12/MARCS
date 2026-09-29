# sanitizers/large_file_filter.py
from __future__ import annotations
import os
import logging
import math
from typing import Dict, Tuple, Any

logger = logging.getLogger("sanitizers.large_file_filter")

# Configurable via env
# DEFAULT_MAX_CHARS = int(os.environ.get("MACRS_MAX_FILE_CHARS", "20000"))
# If TRUNCATE_TO is set to 0 or empty => skip instead of truncating
# DEFAULT_TRUNCATE_TO = int(os.environ.get("MACRS_LARGE_FILE_TRUNCATE_TO", "5000"))
# Binary detection: proportion of non-printable characters above threshold -> treat as binary
BINARY_NONPRINTABLE_RATIO = float(os.environ.get("MACRS_BINARY_NONPRINTABLE_RATIO", "0.30"))

# Public API:
# filter_files(files: Dict[str,str], max_chars=None, truncate_to=None) -> (filtered_files, metadata)
#
# metadata: Dict[filename, {skipped: bool, truncated: bool, reason: str, original_len:int, final_len:int}]

def _get_default_truncate_to() -> int:
    try:
        return int(os.environ.get("MACRS_LARGE_FILE_TRUNCATE_TO", "5000"))
    except:
        return 5000

def _get_default_max_chars() -> int:
    try:
        return int(os.environ.get("MACRS_MAX_FILE_CHARS", "20000"))
    except:
        return 20000

def _is_mostly_binary(s: str) -> bool:
    if not s:
        return False

    # --- NEW: fast binary checks ---
    # 1) Any NUL byte → definitely binary
    if "\x00" in s:
        return True

    # 2) Count high-byte characters (Unicode >= 128)
    high_bytes = sum(1 for ch in s if ord(ch) >= 128)
    if high_bytes > 0 and (high_bytes / len(s)) > 0.30:
        return True

    # --- EXISTING heuristic (unchanged) ---
    nonprint = 0
    total = len(s)
    for ch in s:
        oc = ord(ch)
        if oc < 32 and ch not in ("\n", "\r", "\t"):
            nonprint += 1

    ratio = nonprint / total
    return ratio >= BINARY_NONPRINTABLE_RATIO


def filter_files(files: Dict[str, str],
                 max_chars: int | None = None,
                 truncate_to: int | None = None
                 ) -> Tuple[Dict[str, str], Dict[str, Dict[str, Any]]]:
    """
    Apply large-file rules to a map of filename->text.

    Returns:
        (filtered_files, metadata)

    Behavior:
      - If string appears binary (heuristic) => skip (metadata['skipped']=True, reason='binary')
      - If length > max_chars:
          - if truncate_to is None or truncate_to <= 0 -> skip (skipped=True, reason='too_big')
          - else -> keep first `truncate_to` chars and mark truncated=True
      - Otherwise keep as-is.

    Defaults:
      max_chars -> DEFAULT_MAX_CHARS
      truncate_to -> DEFAULT_TRUNCATE_TO
    """
    if max_chars is None:
        max_chars = _get_default_max_chars()

    if truncate_to is None:
        truncate_to = _get_default_truncate_to()


    out: Dict[str, str] = {}
    meta: Dict[str, Dict[str, Any]] = {}

    for fn, txt in files.items():
        try:
            orig_len = len(txt) if txt is not None else 0
            entry = {"skipped": False, "truncated": False, "reason": "", "original_len": orig_len, "final_len": orig_len}

            # Null or empty -> keep
            if not txt:
                out[fn] = txt
                meta[fn] = entry
                continue

            # Binary heuristic
            if _is_mostly_binary(txt):
                entry.update({"skipped": True, "reason": "binary_content", "final_len": 0})
                meta[fn] = entry
                logger.warning("large_file_filter_binary_skipped file=%s original_len=%d",
                               fn,
                               orig_len)
                continue

            # Too large
            if max_chars is not None and orig_len > max_chars:
                if truncate_to is None or truncate_to <= 0:
                    # skip entirely
                    entry.update({"skipped": True, "reason": "too_big", "final_len": 0})
                    meta[fn] = entry
                    logger.warning(
                        "large_file_filter_too_big_skipped file=%s original_len=%d max_chars=%d",
                        fn, orig_len, max_chars
                    )
                    continue
                else:
                    # truncate
                    truncated_text = txt[:truncate_to]
                    out[fn] = truncated_text
                    entry.update({"skipped": False, "truncated": True, "reason": "truncated", "final_len": len(truncated_text)})
                    meta[fn] = entry
                    logger.warning(
                        "large_file_filter_truncated file=%s original_len=%d truncate_to=%d",
                        fn, orig_len, truncate_to
                    )
                    continue

            # normal keep
            out[fn] = txt
            meta[fn] = entry

        except Exception as e:
            # fail-safe: skip on unexpected errors
            meta[fn] = {"skipped": True, "truncated": False, "reason": f"error:{str(e)}", "original_len": len(txt or ""), "final_len": 0}
            logger.exception("large_file_filter_error file=%s, error=%s",
                             fn,
                             str(e))

    return out, meta