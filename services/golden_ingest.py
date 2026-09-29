# services/golden_ingest.py
from __future__ import annotations
import json
import sys
import os
import glob
import uuid
import logging
import asyncio
from types import SimpleNamespace
from typing import Dict, Any, Optional, Iterable

from core.logging import get_logger
from worker.worker import process_event
from services.review_store import get_review
from core.logging_quiet import silence_all_logs_if_ingest

# Optional quiet mode (used by verification script)
if os.environ.get("MACRS_QUIET", "0") == "1":
    # mute all structlog prints
    logging.getLogger().handlers.clear()

# silence global logging BEFORE importing anything else
silence_all_logs_if_ingest()

logger = get_logger("services.golden_ingest")

# Minimal helper to make an "Event-like" object expected by worker.process_event.
def _make_event_from_case(case_id: str, payload: Dict[str, Any], headers: Optional[Dict[str,str]] = None) -> SimpleNamespace:
    # delivery_id must match golden case id (so review.event_id aligns)
    # worker.process_event expects attributes: delivery_id, event_type, repo_full_name, payload, trace_id, span_id, priority
    repo_full_name = None
    # try various payload shapes
    try:
        repo_full_name = payload.get("repository", {}).get("full_name") or payload.get("repo") or payload.get("repository", {}).get("full_name")
    except Exception:
        repo_full_name = None

    if not repo_full_name:
        repo_full_name = "me/repo"

    evt = SimpleNamespace(
        delivery_id=case_id,
        event_type=(headers or {}).get("X-GitHub-Event", "push"),
        repo_full_name=repo_full_name,
        payload=payload,
        trace_id=str(uuid.uuid4().hex)[:16],
        span_id=str(uuid.uuid4().hex)[:16],
        priority=1,
    )
    return evt

async def ingest_one_async(path_or_dict: Any) -> Dict[str, Any]:
    """
    Ingest a single golden file (path) or a dict (already loaded).
    Returns a dict { id: ..., ok: bool, note: ... }
    """
    if isinstance(path_or_dict, str) and os.path.exists(path_or_dict):
        with open(path_or_dict, "r", encoding="utf-8") as fh:
            case = json.load(fh)
    elif isinstance(path_or_dict, dict):
        case = path_or_dict
    else:
        raise ValueError("ingest_one_async expects a path or dict")

    case_id = case.get("id") or case.get("event_id") or case.get("payload", {}).get("id") or os.path.splitext(os.path.basename(path_or_dict))[0]
    payload = case.get("payload") or case.get("review") or case.get("review", {}) or case.get("payload", {})

    evt = _make_event_from_case(case_id, payload, case.get("headers", {}))
    # call worker.process_event
    await process_event(evt)

    # check saved review
    rev = await get_review(str(case_id))
    ok = rev is not None
    notes = []
    if not ok:
        notes.append("review_missing")
    return {"id": case_id, "ok": ok, "notes": notes, "filename": path_or_dict if isinstance(path_or_dict, str) else None}

def ingest_dir_sync(dirname: str) -> Dict[str, Any]:
    """
    Synchronous helper to ingest all JSON files under dirname (non-recursive by default).
    Returns summary dict.
    """
    files = sorted(glob.glob(os.path.join(dirname, "*.json")))
    results = []
    for fp in files:
        try:
            res = asyncio.run(ingest_one_async(fp))
        except Exception as e:
            logger.error("ingest_one_failed", path=fp, error=str(e))
            res = {"id": os.path.splitext(os.path.basename(fp))[0], "ok": False, "notes": [f"error:{e}"], "filename": fp}
        results.append(res)
    summary = {"count": len(results), "results": results}
    return summary

def ingest_one_sync(path_or_dict: Any) -> Dict[str, Any]:
    return asyncio.run(ingest_one_async(path_or_dict))

# CLI
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m services.golden_ingest <path-to-golden-json-or-dir>", file=sys.stderr)
        sys.exit(2)
    src = sys.argv[1]
    if os.path.isdir(src):
        out = ingest_dir_sync(src)
        print(json.dumps(out, indent=2))
    else:
        out = ingest_one_sync(src)
        print(json.dumps(out, indent=2))