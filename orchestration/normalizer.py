# orchestration/normalizer.py
from typing import Dict, Any, Optional
from orchestration.types import InternalEvent
from datetime import datetime
from core.tracing import get_trace_id, get_span_id


def _safe_get(d: Dict[str, Any], *keys, default=None):
    """Safe nested getter."""
    cur = d
    try:
        for k in keys:
            if cur is None:
                return default
            cur = cur.get(k)
        return cur if cur is not None else default
    except Exception:
        return default


def normalize_github_event(
    event_type: str,
    delivery_id: Optional[str],
    payload: Dict[str, Any],
) -> InternalEvent:
    """
    Convert a GitHub webhook payload into InternalEvent.
    Supports: ping, push, pull_request, others -> unknown.
    This function is intentionally conservative (defensive parsing).
    """

    ev = event_type or "unknown"
    ev = ev.lower()

    # Base metadata
    repo_full_name = _safe_get(payload, "repository", "full_name")
    sender = _safe_get(payload, "sender", "login") or _safe_get(payload, "pusher", "name")

    # Build base InternalEvent
    internal = InternalEvent(
        event_type=ev,
        action=_safe_get(payload, "action"),
        repo_full_name=repo_full_name,
        sender=sender,
        delivery_id=delivery_id,
        timestamp=datetime.utcnow(),
        raw_payload=payload,
        normalized={},  # fill below per-event
        trace_id=get_trace_id(),
        span_id=get_span_id(),
    )

    # Per-type normalization
    if ev == "ping":
        internal.normalized = {
            "zen": payload.get("zen"),
            "hook_id": payload.get("hook_id"),
        }
        internal.priority = "low"

    elif ev == "push":
        # push event fields
        ref = payload.get("ref")
        after = payload.get("after")
        before = payload.get("before")
        commits = payload.get("commits") or []
        head_commit = payload.get("head_commit") or {}
        internal.commit_sha = after or head_commit.get("id")
        internal.normalized = {
            "ref": ref,
            "before": before,
            "after": after,
            "commit_count": len(commits),
            "head_commit_message": head_commit.get("message"),
        }
        internal.priority = "normal"

    elif ev == "pull_request":
        pr = payload.get("pull_request") or {}
        pr_number = _safe_get(payload, "number") or _safe_get(pr, "number") or _safe_get(payload, "pull_request", "number")
        state = _safe_get(pr, "state") or _safe_get(payload, "state")
        head = _safe_get(pr, "head", "sha") or _safe_get(payload, "after")
        internal.pr_number = int(pr_number) if pr_number is not None else None
        internal.commit_sha = head
        internal.normalized = {
            "pr_title": _safe_get(pr, "title"),
            "pr_state": state,
            "pr_number": internal.pr_number,
            "merged": _safe_get(pr, "merged"),
            "head_ref": _safe_get(pr, "head", "ref"),
            "head_sha": head,
        }
        # PRs that are opened/edited/closed usually higher priority
        if internal.action in ("opened", "reopened", "synchronize"):
            internal.priority = "high"
        else:
            internal.priority = "normal"

    else:
        # Unknown/other events -> minimal normalization
        internal.normalized = {
            "summary_keys": list(payload.keys())[:20]
        }
        internal.priority = "low"

    return internal