# eval/generator.py

from __future__ import annotations
import os, json, random, string
from typing import Dict, Any
from datetime import datetime

from eval.schema import GoldenCase, ExpectedResult

GOLDEN_DIR = "data/golden"

def _rand(n=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=n)) # type: ignore[arg-type]


# ---- Synthetic Style Finding Case ----
def generate_style_case() -> Dict[str, Any]:
    delivery_id = f"auto_style_{_rand()}"
    return {
        "id": delivery_id,
        "description": "Auto-generated style issue",
        "headers": {
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": delivery_id
        },
        "payload": {
            "ref": "refs/heads/main",
            "after": _rand(),
            "head_commit": {
                "id": _rand(),
                "message": "Refactor: long lines, missing docstring"
            },
            "repository": {"full_name": "me/repo"}
        },
        "expected": {
            "min_findings": 1,
            "categories": ["style"],
            "min_patches": 0,
            "allow_extra_findings": True
        },
        "expected_findings": [
            {
                "id": "style-auto",
                "category": "style",
                "message": "long line",
                "suggested_patch": None
            }
        ]
    }


# ---- Synthetic Security Case ----
def generate_security_case() -> Dict[str, Any]:
    delivery_id = f"auto_sec_{_rand()}"
    token = _rand(12)

    return {
        "id": delivery_id,
        "description": "Auto-generated security issue",
        "headers": {
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": delivery_id
        },
        "payload": {
            "ref": "refs/heads/main",
            "after": _rand(),
            "head_commit": {
                "id": _rand(),
                "message": f"Add API key = {token}"
            },
            "repository": {"full_name": "me/repo"}
        },
        "expected": {
            "min_findings": 1,
            "categories": ["security"],
            "min_patches": 1,
            "allow_extra_findings": True
        }
    }


# ---- Mixed / Random Case ----
def generate_mixed_case() -> Dict[str, Any]:
    delivery_id = f"auto_mix_{_rand()}"
    message: str = random.choice([ # type: ignore[arg-type]
        "Fix long lines and add token abc123",
        "Refactor + remove leaked password pass007",
        "Improve style and fix logging"
    ])

    return {
        "id": delivery_id,
        "description": "Auto-generated mixed case",
        "headers": {
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": delivery_id
        },
        "payload": {
            "ref": "refs/heads/main",
            "after": _rand(),
            "head_commit": {
                "id": _rand(),
                "message": message
            },
            "repository": {"full_name": "me/repo"}
        },
        "expected": {
            "min_findings": 1,
            "min_patches": 0,
            "allow_extra_findings": True,
            "categories": []
        }
    }


# ---- Save to eval/golden ----
def save_case(case: Dict[str, Any]):
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    fname = f"{case['id']}.json"
    with open(os.path.join(GOLDEN_DIR, fname), "w", encoding="utf-8") as f:
        json.dump(case, f, indent=2)


# ---- Batch Generator ----
def generate_cases(style_n: int = 0, sec_n: int = 0, mixed_n: int = 0) -> Dict[str, Any]:
    out = {"generated": []}

    for _ in range(style_n): # type: ignore[arg-type]
        c = generate_style_case()
        save_case(c)
        out["generated"].append(c["id"])

    for _ in range(sec_n): # type: ignore[arg-type]
        c = generate_security_case()
        save_case(c)
        out["generated"].append(c["id"])

    for _ in range(mixed_n): # type: ignore[arg-type]
        c = generate_mixed_case()
        save_case(c)
        out["generated"].append(c["id"])

    return out