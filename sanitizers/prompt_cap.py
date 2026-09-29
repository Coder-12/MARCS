# sanitizers/prompt_cap.py
from __future__ import annotations
import os
import logging
from typing import Dict, Tuple, Any, List

logger = logging.getLogger("sanitizers.prompt_cap")

# Env-configurable defaults
def _get_default_max_chars() -> int:
    try:
        return int(os.environ.get("MACRS_PROMPT_MAX_CHARS", "32000"))
    except Exception:
        return 32000

def _get_default_append_note() -> bool:
    v = os.environ.get("MACRS_PROMPT_CAP_APPEND_NOTE", "1")
    return v not in ("0", "false", "False", "no", "off", "")

# Key name to use if we append a sentinel/note into the prompt dictionary
PROMPT_TRUNCATION_KEY = "__PROMPT_TRUNCATED_NOTE__"
DEFAULT_TRUNCATION_NOTE = (
    "...TRUNCATED_DUE_TO_PROMPT_CAP..."
)

def apply_prompt_cap(
    files: Dict[str, str],
    max_chars: int | None = None,
    append_note: bool | None = None,
    note_text: str | None = None,
) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """
    Enforce a global character cap on the set of files to be sent to an LLM.

    Strategy:
      - If total chars <= cap -> return files unchanged.
      - Otherwise: sort files by size ascending (stable) and greedily
        include whole files until next file would overflow the cap.
      - If no file fits (i.e., all files are individually > cap), include
        the smallest file truncated in-place to cap (minus note space if append_note).
      - Optionally append a sentinel note under PROMPT_TRUNCATION_KEY describing truncation.
      - Return (filtered_files, meta) where meta contains:
          - included_files: list[str]
          - dropped_files: list[str]
          - total_chars: int (chars in included files + note if added)
          - cap: int
          - note_added: bool
          - truncated_in_place: bool
          - truncated_bytes: int (if truncated_in_place)
    """
    if max_chars is None:
        max_chars = _get_default_max_chars()
    if append_note is None:
        append_note = _get_default_append_note()
    note_text = DEFAULT_TRUNCATION_NOTE if note_text is None else note_text

    # defensive copy and compute sizes
    items: List[tuple[str, str]] = list(files.items())

    # quick-pass: if nothing to do
    total_all = sum(len(v or "") for _, v in items)
    if total_all <= max_chars:
        meta = {
            "included_files": [k for k, _ in items],
            "dropped_files": [],
            "total_chars": total_all,
            "cap": max_chars,
            "note_added": False,
            "truncated_in_place": False,
            "truncated_bytes": 0,
        }
        return dict(items), meta

    # sort ascending by size, tie-break by filename for determinism
    items_sorted = sorted(items, key=lambda kv: (len(kv[1] or ""), kv[0]))

    included: Dict[str, str] = {}
    included_chars = 0
    dropped: List[str] = []

    # reserve space for note if we will add it
    note_len = len(note_text) if append_note else 0
    effective_cap = max_chars - note_len if note_len > 0 else max_chars
    if effective_cap < 0:
        # weird: cap smaller than note length; don't add note then
        note_len = 0
        effective_cap = max_chars

    for fn, txt in items_sorted:
        ln = len(txt or "")
        if ln == 0:
            # keep empty files (they cost nothing)
            included[fn] = txt
            continue

        if included_chars + ln <= effective_cap:
            included[fn] = txt
            included_chars += ln
        else:
            dropped.append(fn)

    truncated_in_place = False
    truncated_bytes = 0

    # If we dropped everything (i.e., no included file), then include smallest file truncated
    if not included and items_sorted:
        smallest_fn, smallest_txt = items_sorted[0]
        # compute max that can be included
        max_for_file = effective_cap
        if max_for_file <= 0:
            # nothing fits; include zero-length (don't include) and mark dropped
            dropped = [k for k, _ in items_sorted]
            included = {}
            included_chars = 0
        else:
            truncated_text = (smallest_txt or "")[:max_for_file]
            included[smallest_fn] = truncated_text
            included_chars = len(truncated_text)
            truncated_in_place = True
            truncated_bytes = len(smallest_txt) - len(truncated_text) if smallest_txt else 0
            # dropped are all others
            dropped = [k for k, _ in items_sorted if k != smallest_fn]

    # add the note sentinel if requested and if there was any truncation/dropping
    note_added = False
    if append_note and (truncated_in_place or dropped):
        included[PROMPT_TRUNCATION_KEY] = note_text
        included_chars += note_len
        note_added = True

    if dropped or truncated_in_place:
        logger.info(
            f"prompt_cap_applied cap={max_chars} included={len(included)} dropped={len(dropped)} truncated_in_place={truncated_in_place} truncated_bytes={truncated_bytes} note_added={note_added}"
        )

    meta = {
        "included_files": list(included.keys()),
        "dropped_files": dropped,
        "total_chars": included_chars,
        "cap": max_chars,
        "note_added": note_added,
        "truncated_in_place": truncated_in_place,
        "truncated_bytes": truncated_bytes,
    }

    # Keep deterministic ordering in returned dict: included files in same order as items_sorted
    ordered_included: Dict[str, str] = {}
    for fn, _ in items_sorted:
        if fn in included:
            ordered_included[fn] = included[fn]
    # append note at the end (if present)
    if PROMPT_TRUNCATION_KEY in included:
        ordered_included[PROMPT_TRUNCATION_KEY] = included[PROMPT_TRUNCATION_KEY]

    return ordered_included, meta