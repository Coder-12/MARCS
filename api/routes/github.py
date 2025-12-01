# api/routes/github.py

from fastapi import APIRouter, Request
from core.logging import get_logger
from orchestration.normalizer import normalize_github_event
from orchestration.queue import enqueue_event
from core.tracing import get_trace_id, get_span_id

router = APIRouter()
logger = get_logger("github.webhook")


@router.post("/webhook")
async def github_webhook(request: Request):
    """
    Phase 0: GitHub webhook receiver with normalization layer.
    - Accept any GitHub event
    - Parse headers + JSON body
    - Normalize into InternalEvent
    - Return normalized event for now (Phase 1 will enqueue)
    """
    try:
        # GitHub may include many event types: pull_request, push, ping, etc.
        event = request.headers.get("X-GitHub-Event", "unknown")
        delivery_id = request.headers.get("X-GitHub-Delivery", "unknown")

        # Read payload safely
        try:
            payload = await request.json()
        except Exception as e:
            payload = {}
            logger.warning(
                event="webhook_invalid_json",
                delivery_id=str(delivery_id),
            )

        # Safe payload summary extraction
        payload_summary = payload.get("action") if isinstance(payload, dict) else None

        # Normalize into InternalEvent
        internal_event = normalize_github_event(event, delivery_id, payload)

        # Log normalized data
        logger.info(
            "normalized_event",
            event_type=internal_event.event_type,
            delivery_id=internal_event.delivery_id,
            repo=internal_event.repo_full_name,
            pr_number=str(internal_event.pr_number) if internal_event.pr_number else None,
            commit_sha=internal_event.commit_sha,
            priority=internal_event.priority,
            trace_id=internal_event.trace_id or get_trace_id(),
            span_id=internal_event.span_id or get_span_id(),
        )

        # Phase 0 queue stub
        enqueue_event(internal_event)

        # Phase 1: will enqueue to worker
        # enqueue_internal_event(internal_event)

        # Phase 0: do nothing; pipeline comes in Phase 1
        return {
            "status": "ok",
            "event_type": internal_event.event_type,
            "delivery_id": internal_event.delivery_id,
            "normalized": internal_event.normalized,
            "summary": payload_summary,
            "priority": internal_event.priority,
            "trace_id": internal_event.trace_id or get_trace_id(),
            "span_id": internal_event.span_id or get_span_id(),
        }

    except Exception as e:
        logger.error(
            event="webhook_handler_error",
            error=str(e),
        )
        return {"status": "error", "detail": str(e)}

# @router.post("/webhook")
# async def github_webhook(request: Request):
#     logger.info("webhook_raw_headers", headers=dict(request.headers))
#
#     body_bytes = await request.body()
#     logger.info("webhook_raw_body", body=body_bytes.decode("utf-8", errors="ignore"))
#
#     # Try to parse JSON
#     try:
#         payload = await request.json()
#     except Exception as e:
#         logger.error("webhook_json_error", error=str(e))
#         payload = {}
#
#     return {"status": "ok"}

