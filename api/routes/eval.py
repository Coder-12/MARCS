# api/routes/eval.py
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Dict, Any
from core.logging import get_logger
from eval.loader import load_golden_cases
from services.evaluator import evaluate_goldencase_result, evaluate_all_golden, evaluate_review_against_golden
from eval.generator import generate_cases
from services.metrics_store import metrics
from services.review_store import get_review
import asyncio

# NEW imports
from services.golden_registry import golden_registry
from services.fast_eval_cache import fast_eval_cache
# ============================
# Patch Evaluation Sub-API
# ============================

from pydantic import BaseModel

class Finding(BaseModel):
    message: str
    severity: str = "low"
    category: str | None = None

class ScoreReq(BaseModel):
    finding: Finding
    patch_text: str

class RankReq(BaseModel):
    finding: Finding
    patches: Dict[str, str]

class PreviewReq(BaseModel):
    review: Dict[str, Any]

router = APIRouter(prefix="/eval")
logger = get_logger("api.eval")

@router.get("/run")
async def run_eval() -> Dict[str, Any]:
    """
    Run GoldenCase-style evaluation: load all eval/golden/*.json (GoldenCase),
    for each case fetch review from review_store (by header delivery id), compare and return per-case results.
    """
    cases = load_golden_cases()
    results = {}
    for case in cases:
        # delivery id expected to be in headers (X-GitHub-Delivery) or case.id
        delivery_id = case.headers.get("X-GitHub-Delivery") or case.id
        review = await get_review(delivery_id)
        if review is None:
            results[case.id] = {"ok": False, "errors": ["review_not_found"]}
            continue
        review_dict = review.model_dump() if hasattr(review, "model_dump") else dict(review)
        ok, errs = await evaluate_goldencase_result(review_dict, case)
        results[case.id] = {"ok": ok, "errors": errs}
    # include metrics snapshot
    m = await metrics.snapshot()
    results["_metrics"] = m
    return JSONResponse(results)

@router.get("/dashboard")
async def dashboard():
    html = """
    <html><body>
    <h2>MACRS Phase-0 Evaluation Dashboard</h2>
    <p>GoldenCase pass/fail table (and metrics snapshot)</p>
    <script>
    async function load() {
        const res = await fetch('/eval/run');
        const data = await res.json();
        let html = "<table border='1' cellpadding='6'><tr><th>ID</th><th>Status</th><th>Errors</th></tr>";
        for (const k in data) {
            if (k === '_metrics') continue;
            const r = data[k];
            html += `<tr>
                <td>${k}</td>
                <td style="color:${r.ok ? 'green' : 'red'}">${r.ok ? 'PASS' : 'FAIL'}</td>
                <td>${(r.errors || []).join("<br>")}</td>
            </tr>`;
        }
        html += "</table>";
        const m = data._metrics || {};
        html += "<h3>Metrics Snapshot</h3>";
        html += "<pre>" + JSON.stringify(m, null, 2) + "</pre>";
        document.getElementById("out").innerHTML = html;
    }
    load();
    </script>
    <div id="out"></div>
    </body></html>
    """
    return HTMLResponse(html)

@router.get("/review/{event_id}")
async def eval_single(event_id: str) -> Dict[str, Any]:
    """
    Run the TP/FP/FN evaluator for a saved review (if an event-centered golden mapping exists).
    """
    review = await get_review(event_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review_not_found")
    res = await evaluate_review_against_golden(review)
    return {"status": "ok", "result": res}

@router.get("/golden/run")
async def golden_batch():
    """
    Run the advanced per-event golden evaluator over ALL golden entries and saved reviews.
    """
    summary = await evaluate_all_golden()
    return {"status": "ok", "summary": summary}

@router.get("/metrics")
async def get_metrics():
    snap = await metrics.snapshot()
    return {"status": "ok", "metrics": snap}

@router.get("/recent")
async def recent_results():
    recent = await metrics.recent_results()
    return {"status": "ok", "recent": recent}

@router.post("/generate")
async def generate(style: int = 0, security: int = 0, mixed: int = 0):
    """
    Auto-generate synthetic golden cases.
    """
    res = generate_cases(style, security, mixed)
    return {"status": "ok", "generated_ids": res["generated"]}

@router.post("/cache/clear")
async def clear_cache():
    """
    Clear the fast-eval cache (all entries).
    """
    await fast_eval_cache.clear()
    return {"status": "ok", "cleared": True}

@router.post("/golden/reload")
async def reload_golden():
    """
    Reload golden cases from disk (uses eval.loader.load_golden_cases).
    Useful after you write new JSON files to data/golden/vX/.
    """
    try:
        golden_registry.reload()
    except Exception as e:
        logger.exception("golden_reload_failed", error=str(e))
        raise HTTPException(status_code=500, detail="reload_failed")
    # clear cache as golden set changed
    await fast_eval_cache.clear()
    return {"status": "ok", "reloaded_at": golden_registry.loaded_at()}


@router.post("/patch/score")
async def score_patch(req: ScoreReq):
    """
    Lightweight deterministic scoring used by test suite.
    """
    patch = req.patch_text or ""
    base = 0.5
    sev_bonus = {"low": 0.0, "medium": 0.1, "high": 0.2}.get(req.finding.severity.lower(), 0.0)
    length_pen = min(0.4, len(patch) / 200.0)
    score = max(0.0, min(1.0, base + sev_bonus - length_pen))
    return {"ok": True, "score": round(score, 3), "reason": "heuristic"}


@router.post("/patch/rank")
async def rank_patches(req: RankReq):
    """
    Rank patches by the same scoring heuristic; deterministic.
    """
    scores = {}
    for pid, txt in (req.patches or {}).items():
        sev = req.finding.severity.lower()
        sev_bonus = {"low": 0.0, "medium": 0.1, "high": 0.2}.get(sev, 0.0)
        length_pen = min(0.4, len(txt)/200.0)
        score = max(0.0, min(1.0, 0.5 + sev_bonus - length_pen))
        scores[pid] = round(score, 3)
    ranked = dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True))
    return {"ok": True, "ranked": ranked}


@router.post("/patch/preview")
async def preview_patch(req: PreviewReq):
    """
    Returns a short preview (truncated) of each suggested patch.
    """
    review = req.review or {}
    previews = {}
    sp = review.get("suggested_patches") or {}
    for pid, txt in sp.items():
        previews[pid] = txt[:80] + ("…" if len(txt) > 80 else "")
    return {"ok": True, "preview": previews, "review": review}


@router.get("/patch/features/{patch_id}")
async def patch_features(
    patch_id: str,
    text: str,
    category: str | None = None,
    message: str | None = None,
):
    """
    Very simple feature extractor for test-suite compatibility.
    """
    length = len(text)
    tokens = len(text.split())
    simple_score = round(max(0, min(1, 1 - (length / 500.0))), 3) # type: ignore[arg-type]
    return {
        "ok": True,
        "patch_id": patch_id,
        "score": simple_score,
        "features": {
            "patch_id": patch_id,
            "length": length,
            "tokens": tokens,
            "category": category or "",
            "message": (message or "")[:100],
            "simple_score": simple_score,
        }
    }