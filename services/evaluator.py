# services/evaluator.py
import asyncio
import json
import os
from typing import Dict, Any, List, Tuple
from core.logging import get_logger
from eval.loader import load_golden_cases
from eval.compare import compare_review
from services.metrics_store import metrics, record_evaluation_result, record_case_result
from agents.results import SynthesizedReview, AgentFinding

# New imports
from services.golden_registry import golden_registry
from services.fast_eval_cache import fast_eval_cache

logger = get_logger("services.evaluator")

# Single canonical golden file directory (fallback, loader used by registry)
DEFAULT_GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "golden")


def _load_golden_map(path: str | None = None) -> Dict[str, Dict[str, Any]]:
    """
    Backwards-compatible loader: returns mapping id -> golden JSON.
    The primary source should be golden_registry; this loader remains for
    cases where consumers call it directly.
    """
    path = path or DEFAULT_GOLDEN_DIR
    out = {}
    # style A: multiple files
    if os.path.isdir(path):
        for fn in os.listdir(path):
            if fn.endswith(".json"):
                with open(os.path.join(path, fn), "r", encoding="utf-8") as fh:
                    try:
                        j = json.load(fh)
                        out[j.get("id") or fn.replace(".json", "")] = j
                    except Exception:
                        continue
        return out

    # style B: single file (fallback)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            try:
                j = json.load(fh)
                if isinstance(j, dict):
                    return j
            except Exception:
                return {}
    return out


def _review_to_dict(review: SynthesizedReview) -> Dict[str, Any]:
    if hasattr(review, "model_dump"):
        return review.model_dump()
    # fallback naive conversion
    return {
        "event_id": getattr(review, "event_id", None),
        "repo": getattr(review, "repo", None),
        "findings": [f.model_dump() if hasattr(f, "model_dump") else f for f in getattr(review, "findings", [])],
        "suggested_patches": getattr(review, "suggested_patches", {}),
    }


async def evaluate_review_against_golden(review: SynthesizedReview, golden_dir: str | None = None) -> Dict[str, Any]:
    """
    Evaluate one SynthesizedReview vs golden entry (by event_id).
    Uses golden_registry and fast_eval_cache to speed up repeated evaluations.
    """
    event_id = str(review.event_id)

    # 1) fetch golden entry from registry (preferred)
    entry = None
    try:
        entry = golden_registry.get(event_id)
    except Exception:
        entry = None

    # 2) fallback to ad-hoc loader if registry doesn't contain it (backwards compat)
    if not entry:
        map_ = _load_golden_map(golden_dir)
        entry = map_.get(event_id)

    if not entry:
        # no golden entry for this event
        result = {"event_id": review.event_id, "error": "golden_missing"}
        return result

    # check cache (key per event_id — golden case id is event_id in our scheme)
    cache_key = f"{event_id}"
    cached = await fast_eval_cache.get(cache_key)
    if cached:
        # Deliver cached evaluation with an indicator
        out = dict(cached)
        out["from_cache"] = True
        out["event_id"] = event_id
        return out

    # No cache — compute fresh evaluation
    gold_findings = entry.get("expected_findings", [])

    tps = []
    fps = []
    matched_ids = set()
    for f in review.findings:
        matched = False
        for g in gold_findings:
            if g.get("id") in matched_ids:
                continue
            # match by id or category+substring or patch substring
            if (f.id and g.get("id") and f.id == g.get("id")):
                matched = True
            elif (g.get("category") and g.get("message") and g.get("message") in (f.message or "")):
                matched = True
            elif (f.suggested_patch and g.get("suggested_patch") and g.get("suggested_patch") in f.suggested_patch):
                matched = True
            if matched:
                matched_ids.add(g.get("id"))
                tps.append((f, g))
                break
        if not matched:
            fps.append(f)

    fn_count = max(0, len(gold_findings) - len(matched_ids))
    tp_count = len(tps)
    fp_count = len(fps)

    precision = tp_count / (tp_count + fp_count) if (tp_count + fp_count) else 0.0
    recall = tp_count / (tp_count + fn_count) if (tp_count + fn_count) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    res = {
        "event_id": event_id,
        "repo": review.repo,
        "tp": tp_count,
        "fp": fp_count,
        "fn": fn_count,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp_pairs": [{"finding_id": f.id, "gold_id": g.get("id")} for f, g in tps],
        "fp_ids": [f.id for f in fps],
        "gold_count": len(gold_findings),
    }

    # store to fast cache (best-effort)
    try:
        await fast_eval_cache.set(cache_key, res)
    except Exception:
        logger.exception("fast_cache_set_failed", event_id=event_id)

    # async-record metrics to storage (best effort)
    try:
        asyncio.create_task(record_evaluation_result({
            "event_id": event_id,
            "tp": tp_count,
            "fp": fp_count,
            "fn": fn_count,
            "precision": precision,
            "recall": recall,
            "f1": f1
        }))
    except Exception:
        logger.exception("record_metrics_failed", event_id=event_id)

    return res


async def evaluate_all_golden(golden_dir: str | None = None) -> Dict[str, Any]:
    """
    Evaluate all saved reviews that have corresponding golden entries.
    Uses registry for golden set.
    """
    # preferred source: golden_registry
    gold_map = golden_registry.all()
    per_event = []
    for event_id, entry in gold_map.items():
        try:
            from services.review_store import get_review
            review = await get_review(event_id)
        except Exception:
            review = None
        if not review:
            per_event.append({"event_id": event_id, "error": "review_missing"})
            continue
        r = await evaluate_review_against_golden(review, golden_dir)
        per_event.append(r)

    # aggregate micro metrics
    tp = sum(x.get("tp", 0) for x in per_event if isinstance(x, dict))
    fp = sum(x.get("fp", 0) for x in per_event if isinstance(x, dict))
    fn = sum(x.get("fn", 0) for x in per_event if isinstance(x, dict))
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    summary = {
        "num_events": len(per_event),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "per_event": per_event,
    }
    return summary


# Helper used by the golden-case runner (phase-0 style) that compares with GoldenCase.expected
async def evaluate_goldencase_result(review_dict: Dict[str, Any], golden_case) -> Tuple[bool, List[str]]:
    ok, errors = compare_review(review_dict, golden_case.expected)
    # record case-level result (async)
    await record_case_result(golden_case.id, ok, len(review_dict.get("findings", [])), len(review_dict.get("suggested_patches", {})))
    return ok, errors