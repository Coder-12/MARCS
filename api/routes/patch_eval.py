# api/routes/patch_eval.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional, List

from core.logging import get_logger

logger = get_logger("api.patch_eval")
router = APIRouter(prefix="/eval/patch", tags=["patch-eval"])

# Try to import the "pro" selector helpers if present; otherwise fall back
# to the selector/scorer functions we know exist.
try:
    from services.patch_selector_pro import (
        rank_patches_for_finding_v2,
        preview_selector_pipeline_v2,
    )
except Exception:
    rank_patches_for_finding_v2 = None
    preview_selector_pipeline_v2 = None

from services.patch_scorer import score_patch_for_finding, score_all_patches, PatchScore
# primary (phase-0/phase-1) selector
try:
    from services.patch_selector import select_patches_for_review
except Exception:
    select_patches_for_review = None

# Agents models
from agents.results import AgentFinding, SynthesizedReview


# -----------------------------
# Request/response schemas
# -----------------------------
class FindingSchema(BaseModel):
    id: Optional[str] = None
    message: Optional[str] = ""
    category: Optional[str] = None
    severity: Optional[str] = None
    location: Optional[str] = None
    metadata: Dict[str, Any] = {}
    confidence: float = 0.0


class PatchScoreRequest(BaseModel):
    finding: FindingSchema
    patch_text: str


class PatchRankRequest(BaseModel):
    finding: FindingSchema
    patches: Dict[str, str]


class PatchPreviewRequest(BaseModel):
    review: Dict[str, Any]


class PatchExplainRequest(BaseModel):
    finding: FindingSchema
    patch_text: str


# -----------------------------
# Helpers
# -----------------------------
def _make_agent_finding(f: FindingSchema) -> AgentFinding:
    """Convert FindingSchema -> AgentFinding with safe defaults for required fields."""
    severity = (f.severity or "low").lower()
    # AgentFinding requires a severity and message in your models; ensure safe values.
    msg = f.message or ""
    # Build a dict and try to instantiate via Pydantic helpers (supporting v1/v2)
    payload = {
        "id": f.id,
        "severity": severity,
        "message": msg,
        "confidence": float(f.confidence or 0.0),
        "category": f.category,
        "location": f.location,
        "metadata": f.metadata or {},
    }
    try:
        # pydantic v2 name
        return AgentFinding.model_validate(payload)
    except Exception:
        try:
            return AgentFinding(**payload)
        except Exception as e:
            logger.error("make_agent_finding_error", error=str(e))
            raise


def _make_minimal_review() -> SynthesizedReview:
    """Create a minimal empty SynthesizedReview object for scoring single patches."""
    payload = {"event_id": "preview", "repo": "preview", "findings": [], "suggested_patches": {}}
    try:
        return SynthesizedReview.model_validate(payload)
    except Exception:
        return SynthesizedReview(**payload)


def _make_review_from_dict(d: Dict[str, Any]) -> SynthesizedReview:
    """Try to construct a SynthesizedReview from incoming dict payload (v2/v1 safe)."""
    try:
        return SynthesizedReview.model_validate(d)
    except Exception:
        try:
            return SynthesizedReview(**d)
        except Exception as e:
            logger.error("make_review_error", error=str(e))
            raise HTTPException(status_code=400, detail=f"invalid review payload: {e}")


# -----------------------------
# Endpoints
# -----------------------------
@router.post("/score")
def score_single_patch(payload: PatchScoreRequest):
    """
    Score a single patch against a finding.
    Uses services.patch_scorer.score_patch_for_finding under the hood.
    """
    finding_model = _make_agent_finding(payload.finding)
    # score_patch_for_finding requires a review object; pass a minimal one
    review = _make_minimal_review()
    ps = score_patch_for_finding(review, finding_model, patch_id="tmp", patch_text=payload.patch_text)
    return {"score": float(ps.score), "breakdown": ps.breakdown}


@router.post("/rank")
def rank_patches(payload: PatchRankRequest):
    """
    Rank multiple patches for a single finding.
    If a "pro" ranker exists (patch_selector_pro), call it; otherwise score each patch
    against the finding and return sorted results.
    """
    finding_model = _make_agent_finding(payload.finding)

    # Prefer pro ranker if available
    if rank_patches_for_finding_v2 is not None:
        try:
            ranked = rank_patches_for_finding_v2(finding_model, payload.patches)
            # expect list of (patch_id, score) or similar; normalize
            out = []
            for entry in ranked:
                if isinstance(entry, tuple) or isinstance(entry, list):
                    pid, sc = entry[0], float(entry[1])
                    out.append({"patch_id": pid, "score": sc})
                elif hasattr(entry, "patch_id") and hasattr(entry, "score"):
                    out.append({"patch_id": entry.patch_id, "score": float(entry.score)})
                else:
                    # fallback: try dict
                    out.append(entry)
            return {"ranked": out}
        except Exception as e:
            logger.error("pro_ranker_error", error=str(e))

    # Fallback (scorer-based)
    review = _make_minimal_review()
    results = []
    for pid, text in (payload.patches or {}).items():
        ps = score_patch_for_finding(review, finding_model, patch_id=pid, patch_text=text)
        results.append(ps)
    # sort descending
    results.sort(key=lambda x: x.score, reverse=True)
    return {"ranked": [{"patch_id": p.patch_id, "score": float(p.score), "breakdown": p.breakdown} for p in results]}


@router.get("/features/{pid}")
def debug_feature_vector(pid: str, text: str, category: Optional[str] = None, message: Optional[str] = None):
    """
    Return internal breakdown/features computed for this patch when scored
    (uses score_patch_for_finding breakdown as 'feature vector').
    """
    schema = FindingSchema(message=message or "", category=category)
    fmodel = _make_agent_finding(schema)
    review = _make_minimal_review()
    ps = score_patch_for_finding(review, fmodel, patch_id=pid, patch_text=text)
    return {"patch_id": pid, "features": ps.breakdown, "score": float(ps.score)}


@router.post("/preview")
def preview_pipeline(payload: PatchPreviewRequest):
    """
    Run a full selector preview over a supplied review dict.
    Prefer the Pro preview function if present; otherwise call Phase-0 selector.
    """
    review_dict = payload.review or {}
    review = _make_review_from_dict(review_dict)

    # Try pro preview first
    if preview_selector_pipeline_v2 is not None:
        try:
            return {"preview": preview_selector_pipeline_v2(review_dict)}
        except Exception as e:
            logger.error("pro_preview_error", error=str(e))

    # Fallback: call Phase-0 selector if available
    if select_patches_for_review is not None:
        try:
            sel = select_patches_for_review(review)
            # return selection metadata for debugging
            return {"preview": {"selected": sel, "metadata": review.metadata}}
        except Exception as e:
            logger.error("fallback_preview_error", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=501, detail="no preview selector available")


@router.post("/explain")
def explain_patch(payload: PatchExplainRequest):
    """
    Return the score + breakdown plus a simple human-friendly explanation
    using the breakdown fields produced by the scorer.
    """
    fmodel = _make_agent_finding(payload.finding)
    review = _make_minimal_review()
    ps = score_patch_for_finding(review, fmodel, patch_id="explain", patch_text=payload.patch_text)
    breakdown = ps.breakdown or {}
    explanation = {
        "message_match": breakdown.get("message_overlap"),
        "rule_match": breakdown.get("rule_match"),
        "length_score": breakdown.get("length"),
        "novelty": breakdown.get("novelty"),
        "security_sensitive": breakdown.get("security_sensitive"),
        "syntactic_quality": breakdown.get("syntactic_quality"),
    }
    return {"score": float(ps.score), "breakdown": breakdown, "explanation": explanation}