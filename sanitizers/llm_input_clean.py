# sanitizers/llm_input_clean.py
from __future__ import annotations
import re
from typing import Dict

# Zero-width space used to neutralize Markdown code fences
_ZWSP = "\u200b"
# Allowed control characters we keep
_ALLOWED_CTRL = {"\n", "\r", "\t"}
# Unicode separators that cause JSON/prompt issues
_unicode_sep_re = re.compile(r"[\u2028\u2029\u0085]")
# Collapse excessive spaces/tabs
_space_re = re.compile(r"[ \t]{2,}")
# Collapse 3+ newlines into exactly 2
_multinew_re = re.compile(r"\n{3,}")

def clean_text(s: str) -> str:
    """
    Clean a single text block for safe insertion into an LLM prompt.
    - Remove NUL bytes
    - Remove non-printable control chars except \n, \r, \t
    - Normalize Unicode separators
    - Neutralize ``` fences
    - Normalize whitespace
    """
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)

    # Remove NULs
    s = s.replace("\x00", "")

    # Normalize unicode separators → newline
    s = _unicode_sep_re.sub("\n", s)

    # Drop dangerous control chars
    cleaned = []
    for ch in s:
        oc = ord(ch)
        if oc < 32 and ch not in _ALLOWED_CTRL:
            continue
        cleaned.append(ch)
    s = "".join(cleaned)

    # Neutralize triple-backtick Markdown fences
    s = s.replace("```", "```" + _ZWSP)

    # Collapse excessive horizontal whitespace
    s = _space_re.sub(" ", s)

    # Limit blank lines
    s = _multinew_re.sub("\n\n", s)

    # Trim leading & trailing spaces from each line
    lines = [line.strip() for line in s.splitlines()]
    s = "\n".join(lines)

    # remove unnecessary space after '(' and before ')'
    s = s.replace("( ", "(")
    s = s.replace(" )", ")")

    return s.strip()

def clean_files(files: Dict[str, str]) -> Dict[str, str]:
    out = {}
    for fn, txt in (files or {}).items():
        try:
            out[fn] = clean_text(txt)
        except Exception:
            out[fn] = txt or ""
    return out