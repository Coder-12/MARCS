# tools/golden_linter.py
"""Simple linter/validator for golden JSON files."""
import os
import sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import json
import os
from typing import Tuple

REQUIRED_KEYS = {"id", "description", "headers", "payload", "expected"}


def lint_file(path: str) -> Tuple[bool, str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            j = json.load(fh)
    except Exception as e:
        return False, f"invalid_json:{e}"

    missing = REQUIRED_KEYS - set(j.keys())
    if missing:
        return False, f"missing_keys:{sorted(list(missing))}"

    if not isinstance(j['headers'], dict):
        return False, "headers_not_dict"

    if not isinstance(j['expected'], dict):
        return False, "expected_not_dict"

    # basic expected checks
    if "min_findings" not in j['expected']:
        return False, "expected_missing_min_finding"

    return True, "ok"

if __name__ == "__main__":
    base = os.path.join("data", "golden")
    for f in os.listdir(base):
        if not f.endswith('.json'):
            continue
        ok, msg = lint_file(os.path.join(base, f))
        print(f, ok, msg)
    for version in os.listdir(base):
        p = os.path.join(base, version)
        if not os.path.isdir(p):
            continue
        print("Checking version ", version)
        for f in os.listdir(p):
            if not f.endswith('.json'):
                continue
            ok, msg = lint_file(os.path.join(p, f))
            print(f, ok, msg)


