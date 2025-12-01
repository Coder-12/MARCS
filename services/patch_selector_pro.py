# services/patch_selector_pro.py
from __future__ import annotations
from typing import List, Dict, Optional, Any
from core.logging import get_logger

from agents.results import SynthesizedReview, AgentFinding
from services.patch_scorer import score_all_patches, PatchScore

logger = get_logger("services.patch_selector")


def _dedupe_selected(patches: List[PatchScore]) -> List[PatchScore]:
    """
    Deduplicate identical patch text by keeping the highest scored one.
    """
    seen = {}
    out = []

    for ps in patches:
        key = ps.patch_id
        # dedupe by patch_id only (text-duplication handled by scorer via novelty)
        if key not in seen:
            seen[key] = ps
            out.append(ps)
        else:
            # if duplicate patch_id appears, keep the highest score
            if ps.score > seen[key].score:
                seen[key] = ps
    return out


def _best_k_per_finding(
    scores: List[PatchScore],
    findings: Dict[str, AgentFinding],
    top_k: int,
    min_score: float
) -> Dict[str, List[PatchScore]]:
    """
    Group PatchScore objects by finding_id and return top K above threshold.
    """
    buckets: Dict[str, List[PatchScore]] = {}
    for ps in scores:
        if ps.score < min_score:
            continue
        fid = ps.finding_id
        if fid not in buckets:
            buckets[fid] = []
        buckets[fid].append(ps)

    # sort each bucket
    for fid, items in buckets.items():
        items.sort(key=lambda x: x.score, reverse=True)
        buckets[fid] = items[:top_k]

    return buckets


def _apply_security_boost(selected: Dict[str, List[PatchScore]], findings: Dict[str, AgentFinding]):
    """
    If a finding is 'high severity' and category 'security',
    boost top patch by multiplying score by 1.10 (capped at 1.0).
    """
    for fid, items in selected.items():
        f = findings.get(fid)
        if not f:
            continue
        severity = (f.severity or "").lower()
        category = (f.category or "").lower()

        if severity == "high" and category == "security":
            if items:
                top = items[0]
                boosted = min(1.0, top.score * 1.10)
                top.score = boosted


def select_patches_for_review(
    review: SynthesizedReview,
    *,
    top_k: int = 2,
    min_score: float = 0.25,
    boost_security: bool = True
):
    """
    Primary API:
    - Score all patches
    - Select high-value patches using policy
    - Update review.metadata["selected_patches"]
    Returns dict: {finding_id -> [ {patch_id, score, ...}, ... ]}
    """

    logger.info("patch_selector_start", event_id=review.event_id)

    # Step 1: score all patches
    scores = score_all_patches(review)
    if not scores:
        logger.info("patch_selector_no_patches", event_id=review.event_id)
        review.metadata["selected_patches"] = {}
        return {}

    # Step 2: group by finding
    findings = {str(f.id): f for f in review.findings}
    grouped = _best_k_per_finding(scores, findings, top_k, min_score)

    # Step 3: boost rules (only if enabled)
    if boost_security:
        _apply_security_boost(grouped, findings)

    # Step 4: dedupe across all buckets
    for fid in list(grouped.keys()):
        grouped[fid] = _dedupe_selected(grouped[fid])

    # Step 5: sort deterministically
    for fid in grouped:
        grouped[fid].sort(key=lambda x: (-x.score, x.patch_id))

    # Step 6: store into review.metadata
    out = {}
    for fid, items in grouped.items():
        out[fid] = [ps.to_dict() for ps in items]

    review.metadata["selected_patches"] = out
    logger.info("patch_selector_complete", event_id=review.event_id, selected_count=sum(len(v) for v in out.values()))

    return out

# ==============================================================
# v2 APIs for patch_eval.py (API expects these exact signatures)
# ==============================================================

def rank_patches_for_finding_v2(
    finding: AgentFinding,
    patches: Dict[str, str],
    *,
    top_k: int = 5,
    min_score: float = 0.0,
):
    """
    API CONTRACT:
    rank_patches_for_finding_v2(finding, patches) -> list of {patch_id, score}

    This wrapper builds a temporary SynthesizedReview so that
    score_all_patches() works correctly.
    """

    # Build synthetic review
    review = SynthesizedReview(
        event_id="rank-v2",
        repo="local",
        findings=[finding],
        suggested_patches=patches,
        metadata={"fingerprint_index": {}},
    )

    all_scores = score_all_patches(review)

    # filter scores belonging to this finding only
    filtered = [ps for ps in all_scores if ps.finding_id == str(finding.id)]

    # min threshold
    filtered = [ps for ps in filtered if ps.score >= min_score]

    # sort
    filtered.sort(key=lambda x: (-x.score, x.patch_id))

    # limit
    filtered = filtered[:top_k]

    return [{"patch_id": ps.patch_id, "score": float(ps.score)} for ps in filtered]


def preview_selector_pipeline_v2(review_dict: Dict[str, Any]):
    """
    API CONTRACT:
    preview_selector_pipeline_v2(review_dict) -> { scores:..., selected:... }

    NOTE: This must not require SynthesizedReview as input.
    """

    # convert dict -> model
    try:
        review = SynthesizedReview.model_validate(review_dict)
    except Exception:
        review = SynthesizedReview(**review_dict)

    # score all patches
    scores = score_all_patches(review)
    score_list = [ps.to_dict() for ps in scores]

    # apply selection
    select_patches_for_review(review)

    return {
        "scores": score_list,
        "selected": review.metadata.get("selected_patches", {}),
    }