# services/golden_runner.py
from __future__ import annotations
import json
import sys
import glob
from types import SimpleNamespace
from typing import Dict, Any, List
import asyncio
import pathlib
from core.logging import get_logger

from worker.worker import process_event
from services.review_store import get_review

logger = get_logger("services.golden_runner")


def load_golden_cases(path_glob: str) -> List[Dict[str, Any]]:
    files = sorted(glob.glob(path_glob))
    out = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                j = json.load(fh)
                j["_filename"] = f
                out.append(j)
        except Exception as e:
            logger.error("golden_load_failed", file=f, error=str(e))
    return out


async def run_case(case: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    """
    Run a single golden case:
      - Build a synthetic event
      - Call worker.process_event(evt)
      - Fetch persisted review (if any) and validate expected fields
    """
    filename = case.get("_filename") or "<unknown>"
    cid = case.get("id") or case.get("event_id") or pathlib.Path(filename).stem
    payload = case.get("payload") or case.get("review") or {}

    # Build event object expected by process_event
    evt = SimpleNamespace(
        delivery_id=cid,
        event_type=(case.get("headers") or {}).get("X-GitHub-Event", "push"),
        repo_full_name=(payload.get("repository") or {}).get("full_name") or (payload.get("repo") or {}).get("repo") or "me/repo",
        payload=payload,
        trace_id=(case.get("headers") or {}).get("X-Trace-Id") or f"trace-{cid}",
        span_id=(case.get("headers") or {}).get("X-Span-Id") or f"span-{cid}",
        priority=case.get("priority", 1),
    )

    result = {"id": cid, "filename": filename, "ok": False, "notes": []}

    # run worker (best-effort)
    try:
        if not dry_run:
            await process_event(evt)
    except Exception as e:
        logger.error("golden_worker_failed", case_id=cid, error=str(e))
        result["notes"].append(f"process_event_error: {e}")
        return result

    # Try to load persisted review with same event_id
    try:
        review = await get_review(str(cid))
    except Exception as e:
        logger.info("golden_no_review", case_id=cid)
        review = None

    expected = case.get("expected") or {}
    # Basic validation rules
    min_findings = expected.get("min_findings")
    max_findings = expected.get("max_findings")
    min_patches = expected.get("min_patches")
    allow_extra_findings = expected.get("allow_extra_findings", True)

    # If review missing, mark as fail unless expected allows missing (default: fail)
    if review is None:
        result["notes"].append("review_missing")
        # keep ok False
        return result

    # count findings and patches
    findings_count = len(getattr(review, "findings", []) or [])
    patches_count = len(getattr(review, "suggested_patches", {}) or {})

    result["findings_count"] = findings_count
    result["patches_count"] = patches_count

    # validate min_findings
    if min_findings is not None and findings_count < min_findings:
        result["notes"].append(f"min_findings_failed: {findings_count} < {min_findings}")
    # validate max_findings
    if max_findings is not None and findings_count > max_findings and not allow_extra_findings:
        result["notes"].append(f"max_findings_failed: {findings_count} > {max_findings}")
    # validate min_patches
    if min_patches is not None and patches_count < min_patches:
        result["notes"].append(f"min_patches_failed: {patches_count} < {min_patches}")

    # success when no notes
    if not result["notes"]:
        result["ok"] = True

    return result


async def run_all(path_glob: str = "data/golden/v1/*.json", dry_run: bool = False) -> List[Dict[str, Any]]:
    cases = load_golden_cases(path_glob)
    results = []
    for c in cases:
        res = await run_case(c, dry_run=dry_run)
        results.append(res)
    return results


def cli(path_glob: str = "data/golden/v1/*.json", dry_run: bool = False):
    loop = asyncio.get_event_loop()
    results = loop.run_until_complete(run_all(path_glob, dry_run=dry_run))
    ok = sum(1 for r in results if r.get("ok"))
    total = len(results)
    print(f"Golden runner: {ok}/{total} passed")
    for r in results:
        print(json.dumps(r, indent=2))
    return 0 if ok == total else 1


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--glob", default="data/golden/v1/*.json")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    sys.exit(cli(args.glob, dry_run=args.dry_run))