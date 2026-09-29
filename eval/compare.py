# eval/compare.py
from typing import Dict, Any, List, Tuple

def compare_review(review: Dict[str, Any], expected) -> Tuple[bool, List[str]]:
    """
    Compare a review dict with ExpectedResult (pydantic).
    Returns (ok, errors).
    """
    findings = review.get("findings", []) or []
    patches = review.get("suggested_patches", {}) or {}

    ok = True
    errors = []

    # findings count
    if len(findings) < expected.min_findings:
        ok = False
        errors.append(f"Too few findings: got {len(findings)}, expected >= {expected.min_findings}")

    if expected.max_findings is not None and len(findings) > expected.max_findings and not expected.allow_extra_findings:
        ok = False
        errors.append(f"Too many findings: got {len(findings)}, expected <= {expected.max_findings}")

    # category checks
    if expected.categories:
        actual_cats = {f.get("category") for f in findings}
        for c in expected.categories:
            if c not in actual_cats:
                ok = False
                errors.append(f"Missing expected category: {c}")

    # patch checks
    if len(patches) < expected.min_patches:
        ok = False
        errors.append(f"Too few patches: got {len(patches)}, expected >= {expected.min_patches}")

    return ok, errors