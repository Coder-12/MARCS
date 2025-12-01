# services/finding_normalizer.py
from __future__ import annotations
from typing import Dict, Any
import hashlib
import json
from agents.results import SynthesizedReview, AgentFinding, Evidence
from core.logging import get_logger

logger = get_logger("services.finding_normalizer")


def _evidence_to_canonical(e: Evidence) -> Dict[str, Any]:
    # produce stable representation for hashing
    return {
        "source": e.source,
        "line": e.line or 0,
        "rule": e.rule or "",
        "snippet": (e.snippet or "")[:200],  # truncate long snippet
        "score": float(e.score) if e.score is not None else 0.0,
    }


def compute_fingerprint(finding: AgentFinding) -> str:
    """
    Deterministically compute fingerprint for a finding.
    Uses category + normalized message + location + evidence canonicalization.
    Returns short hex (first 12 chars).
    """
    parts = [
        (finding.category or "").strip().lower(),
        (finding.message or "").strip(),
        (finding.location or "").strip(),
    ]
    evs = finding.evidence or []
    ev_canon = [json.dumps(_evidence_to_canonical(Evidence(**(e.model_dump() if hasattr(e,'model_dump') else e))), sort_keys=True, ensure_ascii=False) for e in evs]
    key = "|".join(parts + ev_canon)
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12] # type: ignore[arg-type]
    return h


def normalize_finding(finding: AgentFinding) -> AgentFinding:
    # ensure evidence list exists
    if finding.evidence is None:
        finding.evidence = []

    # compute simple evidence_score (mean of scores if present)
    scores = [e.score for e in finding.evidence if e.score is not None]
    if scores:
        finding.evidence_score = float(sum(scores)) / len(scores)
    else:
        finding.evidence_score = None

    # compute fingerprint if missing
    if not finding.fingerprint:
        try:
            finding.fingerprint = compute_fingerprint(finding)
        except Exception as e:
            logger.warning("normalize_fingerprint_failed", error=str(e), finding_id=finding.id)
            finding.fingerprint = None

    # ensure linked_patches exists
    if finding.linked_patches is None:
        finding.linked_patches = []

    # normalize category to lowercase short token
    if finding.category:
        finding.category = finding.category.strip().lower()

    return finding


def normalize_review(review: SynthesizedReview) -> SynthesizedReview:
    # apply normalization to all findings and generate an index in metadata
    fp_index = {}
    for i, f in enumerate(review.findings):
        review.findings[i] = normalize_finding(f)        # type: ignore[arg-type]
        if review.findings[i].fingerprint:               # type: ignore[arg-type]
            fp_index.setdefault(review.findings[i].fingerprint, []).append(review.findings[i].id) # type: ignore[arg-type]
    review.metadata.setdefault("fingerprint_index", fp_index)
    return review