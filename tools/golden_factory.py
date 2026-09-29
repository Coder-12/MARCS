# tools/golden_factory.py
"""Deterministic Golden Case Factory.

Generates golden JSON cases for style/security/mix/empty/bigdiff.
Each case includes metadata + expected result schema.

Usage programmatically from generate_golden.py CLI.
"""
from __future__ import annotations
import json
import os
import hashlib
import random
from typing import Dict, Any, List, Optional
from datetime import datetime

# Canonical directories
DEFAULT_OUT = os.path.join("data", "golden")

# Versions: generator writes to data/golden/v{n}

STYLE_SNIPPETS = [
    {"desc": "long line", "body": "# long line example\n'" + "x"*200 + "'\n"},
    {"desc": "missing docstring", "body": "def foo():\n    pass\n"},
]

SECURITY_SNIPPETS = [
    {"desc": "secret in commit", "body": "token = 'abcd1234'\nprint(token)\n"},
    {"desc": "hardcoded password", "body": "PASSWORD = 'hunter2'\n"},
]

MIX_SNIPPETS = [
    {"desc": "style + security", "body": "# long line\n" + "y"*150 + "\nSECRET='s'\n"},
]

BIG_DIFF_EXAMPLE = """
*** Begin Patch
*** Update File: big_module.py
@@
-def old_fn():
-    pass
+def old_fn():
+    # replaced implementation
+    for i in range(100):
+        pass
@@
+def new_fn():
+    # new function added
+    return True
*** End Patch
"""


def _stable_id(prefix: str, seed: Optional[int], payload: Dict[str, Any]) -> str:
    """Create a deterministic id based on seed + payload summary."""
    s = f"{prefix}:{seed}:{json.dumps(payload, sort_keys=True)}"
    h = hashlib.sha1(s.encode()).hexdigest()[:12]
    return f"{prefix}_{h}"


class GoldenFactory:
    def __init__(self, version: str = "v1", out_dir: Optional[str] = None, seed: Optional[int] = None):
        self.version = version
        self.seed = seed or 0
        self.out_dir = out_dir or DEFAULT_OUT
        self.target_dir = os.path.join(self.out_dir, version)
        os.makedirs(self.target_dir, exist_ok=True)

    def _write(self, case: Dict[str, Any]):
        fname = f"{case['id']}.json"
        path = os.path.join(self.target_dir, fname)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(case, fh, indent=2, sort_keys=True)
        return path

    def _make_headers(self, delivery_id: str, event: str = "push") -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery_id,
        }

    def make_style_case(self, idx: int = 0) -> Dict[str, Any]:
        payload = {
            "ref": "refs/heads/main",
            "before": "aaa",
            "after": "bbb",
            "repository": {"full_name": "me/repo"},
            "head_commit": {"id": f"c{idx}", "message": "style: long line"},
            "patch": STYLE_SNIPPETS[idx % len(STYLE_SNIPPETS)]["body"],
        }
        case = {
            "id": _stable_id("auto_style", self.seed + idx, payload),
            "description": f"Auto-generated style case {idx}",
            "headers": self._make_headers(delivery_id=_stable_id("dlv", self.seed + idx, payload)),
            "payload": payload,
            "expected": {
                "min_findings": 1,
                "max_findings": 5,
                "categories": ["style"],
                "min_patches": 0,
                "allow_extra_findings": True,
            },
        }
        return case

    def make_security_case(self, idx: int = 0) -> Dict[str, Any]:
        msg = SECURITY_SNIPPETS[idx % len(SECURITY_SNIPPETS)]["body"]
        payload = {
            "ref": "refs/heads/main",
            "after": f"sec{idx}",
            "head_commit": {"id": f"s{idx}", "message": msg},
            "repository": {"full_name": "me/repo"},
        }
        case = {
            "id": _stable_id("auto_sec", self.seed + idx, payload),
            "description": f"Auto-generated security case {idx}",
            "headers": self._make_headers(delivery_id=_stable_id("dlv", self.seed + idx, payload)),
            "payload": payload,
            "expected": {
                "min_findings": 1,
                "categories": ["security"],
                "min_patches": 1,
                "allow_extra_findings": True,
            },
        }
        return case

    def make_mix_case(self, idx: int = 0) -> Dict[str, Any]:
        payload = {
            "ref": "refs/heads/main",
            "after": f"mix{idx}",
            "head_commit": {"id": f"m{idx}", "message": "mix issue"},
            "repository": {"full_name": "me/repo"},
            "patch": MIX_SNIPPETS[idx % len(MIX_SNIPPETS)]["body"],
        }
        case = {
            "id": _stable_id("auto_mix", self.seed + idx, payload),
            "description": f"Auto-generated mixed case {idx}",
            "headers": self._make_headers(delivery_id=_stable_id("dlv", self.seed + idx, payload)),
            "payload": payload,
            "expected": {
                "min_findings": 1,
                "categories": ["style", "security"],
                "min_patches": 0,
                "allow_extra_findings": True,
            },
        }
        return case

    def make_empty_case(self, idx: int = 0) -> Dict[str, Any]:
        payload = {
            "ref": "refs/heads/main",
            "after": f"empty{idx}",
            "head_commit": {"id": f"e{idx}", "message": ""},
            "repository": {"full_name": "me/repo"},
        }
        case = {
            "id": _stable_id("auto_empty", self.seed + idx, payload),
            "description": f"Auto-generated empty case {idx}",
            "headers": self._make_headers(delivery_id=_stable_id("dlv", self.seed + idx, payload)),
            "payload": payload,
            "expected": {
                "min_findings": 0,
                "max_findings": 0,
                "categories": [],
                "min_patches": 0,
                "allow_extra_findings": True,
            },
        }
        return case

    def make_bigdiff_case(self, idx: int = 0) -> Dict[str, Any]:
        payload = {
            "ref": "refs/heads/main",
            "after": f"big{idx}",
            "head_commit": {"id": f"b{idx}", "message": "big diff"},
            "repository": {"full_name": "me/repo"},
            "patch": BIG_DIFF_EXAMPLE,
        }
        case = {
            "id": _stable_id("auto_big", self.seed + idx, payload),
            "description": f"Auto-generated large diff case {idx}",
            "headers": self._make_headers(delivery_id=_stable_id("dlv", self.seed + idx, payload)),
            "payload": payload,
            "expected": {
                "min_findings": 0,
                "max_findings": 50,
                "categories": [],
                "min_patches": 0,
                "allow_extra_findings": True,
            },
        }
        return case

    def generate(self, counts: Dict[str, int]) -> List[str]:
        """Generate counts of cases per category and write to disk.

        counts example: {"style": 3, "security": 3, "mix":2, "empty":1, "big":1}
        Returns list of file paths written.
        """
        out_paths = []
        i = 0
        for t, n in counts.items():
            for j in range(n):
                if t == "style":
                    case = self.make_style_case(idx=j)
                elif t == "security":
                    case = self.make_security_case(idx=j)
                elif t == "mix":
                    case = self.make_mix_case(idx=j)
                elif t == "empty":
                    case = self.make_empty_case(idx=j)
                elif t == "big":
                    case = self.make_bigdiff_case(idx=j)
                else:
                    continue
                p = self._write(case)
                out_paths.append(p)
                i += 1
        # write README for version
        readme = {
            "version": self.version,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "seed": self.seed,
            "counts": counts,
        }
        with open(os.path.join(self.target_dir, "README.json"), "w", encoding="utf-8") as fh:
            json.dump(readme, fh, indent=2, sort_keys=True)

        return out_paths


if __name__ == "__main__":
    # quick demo if run directly
    gf = GoldenFactory(seed=42)
    gf.generate({"style": 2, "security": 2, "mix": 1, "empty": 1, "big": 1})
    print("Generated sample golden cases in:", gf.target_dir)