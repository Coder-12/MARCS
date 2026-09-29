# services/patch_linker.py
from __future__ import annotations
from typing import Dict, Any, List
from agents.results import SynthesizedReview, AgentFinding
from core.logging import get_logger

logger = get_logger("services.patch_linker")


def _patch_matches_finding(patch_text: str, finding: AgentFinding) -> bool:
    """
    Heuristic rules:
     - if patch contains substring of finding.message OR
     - if patch contains evidence.rule OR evidence.snippet substring.
    """
    txt = (patch_text or "").lower()
    if not txt:
        return False

    if finding.message and finding.message.lower()[:80] in txt:
        return True

    evs = finding.evidence or []
    for e in evs:
        if e.rule and e.rule.lower() in txt:
            return True
        if e.snippet and e.snippet.strip() and e.snippet.lower()[:40] in txt:
            return True

    # fallback: category keyword match (e.g., 'secret', 'vault', 'env') for security
    if finding.category:
        if finding.category == "security":
            if "secret" in txt or "vault" in txt or "env" in txt:
                return True
        if finding.category == "style":
            if "line" in txt or "docstring" in txt or "wrap" in txt:
                return True
    return False


def link_patches(review: SynthesizedReview) -> SynthesizedReview:
    fp_index = (review.metadata or {}).get("fingerprint_index", {}) or {}
    sp = getattr(review, "suggested_patches", {}) or {}

    # ensure linked_patches initialized
    for f in review.findings:
        if f.linked_patches is None:
            f.linked_patches = []

    # Primary linking: fingerprint or direct ID
    for key, patch in sp.items():
        linked_any = False

        # 1) fingerprint key
        if key in fp_index:
            for fid in fp_index[key]:
                for f in review.findings:
                    if f.id == fid:
                        f.linked_patches.append(key)
                        linked_any = True

        # 2) direct id match
        for f in review.findings:
            if f.id == key:
                f.linked_patches.append(key)
                linked_any = True

        # 3) fallback semantic match (ALWAYS executed)
        for f in review.findings:
            if _patch_matches_finding(patch, f):
                f.linked_patches.append(key)

    return review