# utils/prompt_builder.py
from __future__ import annotations
from typing import Dict
from sanitizers.prompt_cap import PROMPT_TRUNCATION_KEY

SAFE_SEP = "\n\n# ===== FILE BOUNDARY =====\n\n"

def build_prompt_string(files: Dict[str, str]) -> str:
    """
    Convert prompt_files dict produced by apply_prompt_cap() into a single
    deterministic LLM-safe string.

    Rules:
      - sorted by filename, except PROMPT_TRUNCATION_KEY goes last
      - each file enclosed with a filename header
      - safe separators between files
      - avoids accidental injections or merging
    """
    normal_files = []
    note_section = ""

    for fn, content in files.items():
        if fn == PROMPT_TRUNCATION_KEY:
            note_section = f"\n\n[TRUNCATION_NOTE]\n{content}\n"
        else:
            normal_files.append((fn, content))

    # deterministic ordering
    normal_files_sorted = sorted(normal_files, key=lambda x: x[0])

    parts = []
    for fn, content in normal_files_sorted:
        parts.append(f"[FILE: {fn}]\n{content}")

    final = SAFE_SEP.join(parts)

    if note_section:
        final += SAFE_SEP + note_section

    return final