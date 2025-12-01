# services/patch_linker_v2.py
from __future__ import annotations
from typing import Dict, Any, List
from core.logging import get_logger
from agents.results import SynthesizedReview, AgentFinding

logger = get_logger("services.patch_linker_v2")


# -----------------------------------------------------------
# Utility semantic matchers (more robust than v1)
# -----------------------------------------------------------

def _normalize(s: str) -> str:
    return (s or "").strip().lower()


def _semantic_hit(patch: str, finding: AgentFinding) -> bool:
    """
    Stronger signal-based semantic matching:
      - keyword match in message
      - rule/snippet match
      - category-based signals
      - fingerprint-based weak matching
    """
    p = _normalize(patch)
    if not p:
        return False

    # message substring
    msg = _normalize(finding.message)
    if msg and msg[:80] in p:
        return True

    # metadata rule / pattern
    md = finding.metadata or {}
    for key in ("rule", "pattern", "match"):
        if md.get(key) and str(md[key]).lower() in p:
            return True

    # snippet matching
    for ev in finding.evidence or []:
        snippet = _normalize(ev.get("snippet") if isinstance(ev, dict) else getattr(ev, "snippet", None))
        if snippet and snippet[:40] in p:
            return True
        rule = _normalize(ev.get("rule") if isinstance(ev, dict) else getattr(ev, "rule", None))
        if rule and rule in p:
            return True

    # category-based signals
    cat = _normalize(finding.category)
    if cat == "security":
        if any(k in p for k in ("secret", "vault", "env", "rotate", "token", "kms")):
            return True
    if cat == "style":
        if any(k in p for k in ("line", "wrap", "docstring", "format")):
            return True

    return False


# -----------------------------------------------------------
# v2 Patch Linking Core Logic
# -----------------------------------------------------------

def link_patches_v2(review: SynthesizedReview) -> SynthesizedReview:
    """
    Improved patch linking with:
      - explicit reliance on selected_patches (post-ranking)
      - fallback semantic linking
      - multi-match safe aggregation
    """

    selected = review.metadata.get("selected_patches") or {}
    fp_index = (review.metadata or {}).get("fingerprint_index", {}) or {}

    # Convert findings to lookup
    f_map: Dict[str, AgentFinding] = {str(f.id): f for f in review.findings}

    # Ensure linked_patches always exists
    for f in review.findings:
        if f.linked_patches is None:
            f.linked_patches = []

    # -------------------------------------------------------
    # 1) DIRECT MAPPING based on selected_patches
    #    selected_patches == {finding_id: [{patch_id, score, ...}, ...]}
    # -------------------------------------------------------
    for fid, plist in selected.items():
        f = f_map.get(fid)
        if not f:
            continue
        for ps in plist:
            pid = ps["patch_id"]
            if pid not in f.linked_patches:
                f.linked_patches.append(pid)

    # -------------------------------------------------------
    # 2) FINGERPRINT-BASED MAPPING
    # -------------------------------------------------------
    for pid, finding_ids in fp_index.items():
        for fid in finding_ids:
            f = f_map.get(fid)
            if f and pid not in f.linked_patches:
                f.linked_patches.append(pid)

    # -------------------------------------------------------
    # 3) SEMANTIC MATCH (fallback for remaining patch_ids)
    # -------------------------------------------------------
    selected_patch_ids = set()
    for fid, plist in selected.items():
        for ps in plist:
            selected_patch_ids.add(ps["patch_id"])

    # Extract actual patch text (top-level suggested_patches holds selected set)
    sp = getattr(review, "suggested_patches", {}) or {}

    for pid, patch_text in sp.items():
        for f in review.findings:
            if _semantic_hit(patch_text, f):
                if pid not in f.linked_patches:
                    f.linked_patches.append(pid)

    # -------------------------------------------------------
    # 4) Ensure determinism: sort patch IDs
    # -------------------------------------------------------
    for f in review.findings:
        f.linked_patches = sorted(set(f.linked_patches))

    logger.info("patch_linker_v2_done", event_id=review.event_id)
    return review