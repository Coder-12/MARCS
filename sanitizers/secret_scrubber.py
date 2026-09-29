# sanitizers/secret_scrubber.py
from __future__ import annotations
import re
import hashlib
import math
from typing import Tuple, List, Dict

# Conservative patterns
_PATTERNS = {
    "pem_private": re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
        flags=re.MULTILINE,
    ),
    "pem_private_loose": re.compile(
        r"-----BEGIN PRIVATE KEY-----[\s\S]+?END PRIVATE KEY-----",
        flags=re.MULTILINE,
    ),
    "github_pat": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,255}\b"),
    "aws_access_key_id": re.compile(r"\bAKIA[0-9A-Z]{16,32}\b"),
    "gcp_api_key": re.compile(r"\bAIza[0-9A-Za-z\-\._]{35,}\b"),
    "slack_token": re.compile(r"\b(xox[pboa]-[0-9A-Za-z\-._]{10,})\b"),
    "jwt": re.compile(r"\b([A-Za-z0-9-_]+\.[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+)\b"),
    # generic long base64-ish token (>=40 chars)
    "long_base64": re.compile(r"\b([A-Za-z0-9+/]{40,}={0,2})\b"),
    # env-like secret names (heuristic; not the secret value)
    "env_like_secret": re.compile(r"\b[A-Z0-9_]*SECRET[A-Z0-9_]*\b"),
}

REDACT_PREFIX = "<REDACTED:"
REDACT_SUFFIX = ">"
ENTROPY_MIN_LEN = 40
ENTROPY_THRESHOLD = 4.0


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _redact_with_hash(tok: str) -> str:
    h = _sha256_hex(tok)
    return f"{REDACT_PREFIX}{h[:12]}{REDACT_SUFFIX}"


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    length = len(s)
    ent = 0.0
    for v in freq.values():
        p = v / length
        ent -= p * math.log2(p) if p > 0 else 0.0
    return ent


def scrub_text(text: str) -> Tuple[str, List[Dict[str, str]]]:
    """
    Robust secret scrubber.
    Returns (scrubbed_text, findings).
    Each finding: {type, sample (prefix), hash}
    """
    if not text:
        return text, []

    findings: List[Dict[str, str]] = []
    scrubbed = text

    # Helper to create a re.sub replacement function that records findings
    def make_repl_fn(kind: str):
        def repl(m: re.Match):
            tok = m.group(0)
            h = _sha256_hex(tok)
            findings.append({"type": kind, "sample": tok[:40], "hash": h})
            return _redact_with_hash(tok)
        return repl

    # 1) Replace PEM blocks first (multiline) using re.sub with function
    for pem_kind in ("pem_private", "pem_private_loose"):
        pat = _PATTERNS.get(pem_kind)
        if pat is None:
            continue
        repl_fn = make_repl_fn("pem")
        scrubbed = pat.sub(repl_fn, scrubbed)

    # 2) Replace explicit tokens (github_pat, aws, gcp, slack, env-like, jwt)
    for kind in ("github_pat", "aws_access_key_id", "gcp_api_key", "slack_token", "env_like_secret", "jwt"):
        pat = _PATTERNS.get(kind)
        if not pat:
            continue
        # jwt is tricky: only accept if three parts (simple guard)
        if kind == "jwt":
            def jwt_repl(m: re.Match):
                tok = m.group(0)
                if tok.count(".") != 2:
                    return tok  # skip non-jwt matches
                h = _sha256_hex(tok)
                findings.append({"type": "jwt", "sample": tok[:40], "hash": h})
                return _redact_with_hash(tok)
            scrubbed = pat.sub(jwt_repl, scrubbed)
        else:
            repl_fn = make_repl_fn(kind)
            scrubbed = pat.sub(repl_fn, scrubbed)

    # 3) Find long base64-ish sequences and high-entropy long tokens (extra safety)
    for m in _PATTERNS["long_base64"].finditer(scrubbed):
        tok = m.group(0)
        if REDACT_PREFIX in tok or len(tok) < ENTROPY_MIN_LEN:
            continue
        ent = _shannon_entropy(tok)
        if ent >= ENTROPY_THRESHOLD:
            h = _sha256_hex(tok)
            findings.append({"type": "long_base64_high_entropy", "sample": tok[:40], "hash": h, "entropy": f"{ent:.2f}"})
            scrubbed = scrubbed.replace(tok, _redact_with_hash(tok))

    # Another pass: generic tokenization to catch 40+ char high-entropy tokens
    tokens = re.findall(r"\b[A-Za-z0-9\-\._+/=]{40,}\b", scrubbed)
    for tok in tokens:
        if REDACT_PREFIX in tok:
            continue
        if len(tok) < ENTROPY_MIN_LEN:
            continue
        ent = _shannon_entropy(tok)
        if ent >= ENTROPY_THRESHOLD:
            h = _sha256_hex(tok)
            findings.append({"type": "high_entropy_token", "sample": tok[:40], "hash": h, "entropy": f"{ent:.2f}"})
            scrubbed = scrubbed.replace(tok, _redact_with_hash(tok))

    # sanitize control characters
    scrubbed = scrubbed.replace("\x00", "")

    # deduplicate findings by hash
    seen = set()
    final_findings = []
    for f in findings:
        key = f.get("hash")
        if key and key not in seen:
            seen.add(key)
            final_findings.append(f)

    return scrubbed, final_findings


def scrub_files(files: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, List[Dict[str, str]]]]:
    """
    Scrub multiple files. Return (sanitized_map, findings_per_file).
    """
    result: Dict[str, str] = {}
    findings_map: Dict[str, List[Dict[str, str]]] = {}
    for fn, txt in files.items():
        try:
            s, f = scrub_text(txt or "")
            result[fn] = s
            if f:
                findings_map[fn] = f
        except Exception:
            # fail-safe: return original content if scrub fails
            result[fn] = txt
    return result, findings_map