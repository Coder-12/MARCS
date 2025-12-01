# services/normalizer.py
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, Any
from core.logging import get_logger
from agents.results import SynthesizedReview, AgentFinding

logger = get_logger("services.normalizer")


def _stable_repr_for_fingerprint(f: AgentFinding) -> str:
    """
    Build a deterministic string from relevant fields that should determine
    equivalence of findings across runs.
    Order must be stable. Use JSON with sorted keys for nested dicts.
    """
    parts = {
        "category": f.category or "",
        "message": (f.message or "").strip(),
        "location": f.location or "",
        # metadata may include rule, pattern, match, etc. convert deterministically
        "metadata": json.dumps(f.metadata or {}, sort_keys=True, separators=(",", ":")),
    }
    return json.dumps(parts, sort_keys=True, separators=(",", ":"))


def compute_fingerprint(f: AgentFinding) -> str:
    """
    Return a stable fingerprint string for a finding.
    We use sha1 over _stable_repr_for_fingerprint.
    """
    s = _stable_repr_for_fingerprint(f)
    h = hashlib.sha1(s.encode("utf-8")).hexdigest()[:14]
    return h


def _normalize_evidence(f: AgentFinding):
    """
    Ensure evidence is a normalized list (empty list if None),
    and evidence_score is numeric or None.
    """
    import uuid

    if f.id is None:
        f.id = uuid.uuid4().hex[:12]

    if not f.severity:
        f.severity = "low"
    else:
        f.severity = f.severity.lower() # type: ignore[arg-type]

    ev = getattr(f, "evidence", None)
    if ev is None:
        f.evidence = []
    elif isinstance(ev, (list, tuple)):
        f.evidence = list(ev)
    else:
        # make single-item list
        f.evidence = [ev]

    # convert Evidence object → dict
    converted = []
    for e in f.evidence:
        if hasattr(e, "model_dump"):
            converted.append(e.model_dump())
        else:
            converted.append(e)
    f.evidence = converted

    # normalize confidence to float
    try:
        f.confidence = float(getattr(f, "confidence", 0.0) or 0.0)
    except Exception:
        f.confidence = 0.0


def normalize_review(review: SynthesizedReview) -> SynthesizedReview:
    """
    Mutates and returns a SynthesizedReview with:
    - created_at as ISO Z time if missing
    - normalized findings: evidence list, numeric confidence
    - computed fingerprint for each finding placed in finding.fingerprint
    - suggested_patch normalized to string or None
    """
    # created_at
    if not getattr(review, "created_at", None):
        review.created_at = datetime.now(timezone.utc).isoformat() # type: ignore[arg-type]

    # findings
    for f in (review.findings or []):
        # ensure id exists (fallback)
        if not getattr(f, "id", None):
            # deterministic fallback: sha of message+location
            f.id = hashlib.sha1((str(f.message or "") + str(f.location or "")).encode("utf-8")).hexdigest()[:12] # type: ignore[arg-type]

        _normalize_evidence(f)

        # suggested_patch -> None or str
        sp = getattr(f, "suggested_patch", None)
        if sp is None:
            f.suggested_patch = None
        else:
            # if it's dict or list, convert to compact string
            if not isinstance(sp, str):
                try:
                    f.suggested_patch = json.dumps(sp, sort_keys=True, separators=(",", ":"))
                except Exception:
                    f.suggested_patch = str(sp)

        # ensure metadata is dict
        if getattr(f, "metadata", None) is None:
            f.metadata = {}
        elif not isinstance(f.metadata, dict):
            try:
                f.metadata = dict(f.metadata)
            except Exception:
                f.metadata = {}

        # compute fingerprint and set
        try:
            f.fingerprint = compute_fingerprint(f)
        except Exception as e:
            logger.error("fingerprint_error", id=getattr(f, "id", None), error=str(e))
            f.fingerprint = hashlib.sha1((f.id or "").encode("utf-8")).hexdigest()[:14] # type: ignore[arg-type]

    # ensure suggested_patches top-level exists as dict
    if getattr(review, "suggested_patches", None) is None:
        review.suggested_patches = {}

    return review