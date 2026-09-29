# agents/pipeline.py
from __future__ import annotations
from typing import List, Dict, Any
from agents.router_agent import RouterAgent
from agents.results import SynthesizedReview, AgentResult, AgentFinding
from core.logging import get_logger
from core.tracing import start_span, get_trace_id

logger = get_logger("agents.pipeline")


def split_into_hunks(internal_event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Basic hunk splitter for Phase-0. Treat whole change as single hunk.
    Accepts InternalEvent (pydantic) or dict.
    """
    if hasattr(internal_event, "model_dump"):
        ev = internal_event.model_dump()
    elif isinstance(internal_event, dict):
        ev = internal_event
    else:
        ev = {}

    payload = ev.get("raw_payload", {}) or ev.get("payload", {}) or {}
    code_blob = None

    # prefer explicit diff/patch keys
    for k in ("patch", "diff", "files", "changes"):
        if payload.get(k):
            code_blob = payload.get(k)
            break

    # fallback to PR body or commit message
    if code_blob is None:
        code_blob = payload.get("body") or payload.get("head_commit", {}).get("message") or ""

    hunk = {
        "event_type": ev.get("event_type") or ev.get("eventType"),
        "delivery_id": ev.get("delivery_id") or ev.get("deliveryId"),
        "repo_full_name": ev.get("repo_full_name") or ev.get("repo"),
        "pr_number": ev.get("pr_number"),
        "commit_sha": ev.get("commit_sha"),
        "code": code_blob,
        "raw_payload": payload,
    }
    return [hunk]


async def run_pipeline(internal_event, router: RouterAgent) -> SynthesizedReview:
    with start_span("pipeline.run"):
        trace = get_trace_id()
        hunks = split_into_hunks(internal_event)
        agent_results: List[AgentResult] = await router.route(hunks)

        review = SynthesizedReview.from_results(
            event_id=str(internal_event.delivery_id),
            repo=(internal_event.repo_full_name if hasattr(internal_event, "repo_full_name") else internal_event.get("repo_full_name") or ""),
            results=agent_results,
            trace_id=trace
        )

        logger.info("pipeline_complete", event_id=review.event_id, findings=len(review.findings), trace_id=trace)
        return review