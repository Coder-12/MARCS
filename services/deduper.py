# services/deduper.py
from __future__ import annotations
from typing import Dict, Any, Tuple, List
from core.logging import get_logger
from agents.results import SynthesizedReview, AgentFinding

logger = get_logger("services.deduper")


def _merge_two_findings(primary: AgentFinding, secondary: AgentFinding) -> AgentFinding:
    """
    Merge secondary into primary deterministically:
    - keep higher confidence numeric
    - combine evidence lists (unique order-preserving)
    - keep earliest non-null explanation
    - merge suggested_patch (prefer primary but append if different)
    - merge metadata (primary wins, then secondary for missing keys)
    """
    # confidence: keep max
    try:
        primary.confidence = max(float(primary.confidence or 0.0), float(secondary.confidence or 0.0))
    except Exception:
        # fallback
        primary.confidence = primary.confidence or secondary.confidence

    # evidence: unique, preserve order (primary first)
    ev_seen = set()
    ev_out = []
    for e in (getattr(primary, "evidence", []) or []):
        if e not in ev_seen:
            ev_seen.add(e); ev_out.append(e)
    for e in (getattr(secondary, "evidence", []) or []):
        if e not in ev_seen:
            ev_seen.add(e); ev_out.append(e)
    primary.evidence = ev_out

    # explanation: prefer primary, else secondary
    if not getattr(primary, "explanation", None) and getattr(secondary, "explanation", None):
        primary.explanation = secondary.explanation

    # suggested_patch: if same skip, else concatenate with separator
    p_sp = getattr(primary, "suggested_patch", None)
    s_sp = getattr(secondary, "suggested_patch", None)
    if p_sp and s_sp:
        if p_sp.strip() != s_sp.strip():
            # deterministic concatenation (primary first)
            primary.suggested_patch = p_sp.strip() + "\n\n# MERGED_PATCH\n" + s_sp.strip()
    elif not p_sp and s_sp:
        primary.suggested_patch = s_sp

    # metadata: primary preferences, then secondary keys fill missing
    md = dict(getattr(primary, "metadata", {}) or {})
    for k, v in (getattr(secondary, "metadata", {}) or {}).items():
        if k not in md:
            md[k] = v
    primary.metadata = md

    # keep location from primary unless missing
    if not getattr(primary, "location", None) and getattr(secondary, "location", None):
        primary.location = secondary.location

    return primary


def dedupe_findings(review: SynthesizedReview) -> Tuple[SynthesizedReview, Dict[str, List[str]]]:
    """
    Deduplicate findings in a review based on fingerprint.
    Returns (review, mapping) where mapping maps fingerprint -> list of merged finding ids.
    Behavior:
      - For each fingerprint, pick a primary candidate deterministically (highest confidence, tie-breaker: smallest id)
      - Merge others into primary via _merge_two_findings
      - Return review with unique fingerprints and updated finding list
    """
    by_fp = {}
    # group
    for f in (review.findings or []):
        fp = getattr(f, "fingerprint", None) or getattr(f, "id", None)
        by_fp.setdefault(fp, []).append(f)

    mapping = {}
    new_findings = []

    for fp, flist in by_fp.items():
        if len(flist) == 1:
            new_findings.append(flist[0])
            mapping[fp] = [flist[0].id]
            continue

        # choose primary: highest confidence, then lexicographically smallest id
        flist_sorted = sorted(flist, key=lambda x: (-float(x.confidence or 0.0), str(x.id)))
        primary = flist_sorted[0]
        merged_ids = [primary.id]

        for sec in flist_sorted[1:]:
            primary = _merge_two_findings(primary, sec)
            merged_ids.append(sec.id)

        # ensure fingerprint remains set to fp
        primary.fingerprint = fp
        new_findings.append(primary)
        mapping[fp] = merged_ids

        logger.info("dedupe_merged", fingerprint=fp, merged=merged_ids)

    # attach and return
    review.findings = new_findings
    # also build fingerprint index into review.metadata.fingerprint_index
    if not getattr(review, "metadata", None):
        review.metadata = {}
    idx = review.metadata.get("fingerprint_index", {}) or {}
    for fp, ids in mapping.items():
        idx[fp] = ids
    review.metadata["fingerprint_index"] = idx

    return review, mapping