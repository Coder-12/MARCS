# utils/file_hash.py
from __future__ import annotations
import hashlib
from typing import Dict, Tuple

def sha256_of_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()

def sha256_of_text(s: str, encoding: str = "utf-8") -> str:
    return sha256_of_bytes(s.encode(encoding))

def file_dict_hashes(files: Dict[str, str]) -> Tuple[Dict[str, str], str]:
    """
    Compute per-file SHA-256 (hex) and a combined repo checksum.
    Combined checksum computed by hashing the deterministic concatenation:
      for each filename sorted lexicographically:
        filename + ":" + filehash + "\n"
    Returns (per_file_hashes, combined_hash)
    """
    per_file: Dict[str, str] = {}
    for fname, content in files.items():
        per_file[fname] = sha256_of_text(content or "")

    # deterministic combined fingerprint
    items = sorted(per_file.items(), key=lambda kv: kv[0])
    combined_builder = "".join(f"{k}:{v}\n" for k, v in items)
    combined_hash = sha256_of_text(combined_builder)
    return per_file, combined_hash