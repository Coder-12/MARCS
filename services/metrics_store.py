# services/metrics_store.py
from typing import Dict, Any
from core.storage import get_storage
from core.logging import get_logger

logger = get_logger("services.metrics_store")
_storage = get_storage()

async def record_evaluation_result(result: Dict[str, Any]):
    """
    Persist evaluation result to storage + keep a small in-memory snapshot as before.
    """
    case_id = result.get("event_id") or result.get("case_id") or "unknown_case"
    await _storage.record_eval_metrics(case_id, result)
    logger.info("metrics_recorded", case_id=case_id, result=result)

async def record_case_result(case_id: str, ok: bool, findings_count: int, patches_count: int):
    payload = {
        "case_id": case_id,
        "ok": ok,
        "tp": findings_count,
        "fp": 0,
        "fn": 0,
        "precision": 1.0 if ok else 0.0,
        "recall": 1.0 if ok else 0.0,
        "f1": 1.0 if ok else 0.0,
        "findings_count": findings_count,
        "patches_count": patches_count,
    }
    await _storage.record_eval_metrics(case_id, payload)

# For compatibility with existing endpoints that expect metrics.snapshot() style:
class _InMemorySnapshot:
    """
    Compatibility layer converting every backend output into:
    {
        "cases": { case_id: payload },
        "summary": { total, passed, failed }
    }
    """
    async def snapshot(self):
        raw = await _storage.get_metrics_snapshot()

        # InMemoryStorage already returns correct shape
        if "cases" in raw:
            return raw

        # FileStorage / SQLite / Redis: {"latest": [...]}
        latest = raw.get("latest", [])
        cases = {}
        passed = failed = 0

        for entry in latest:
            cid = entry.get("case_id", "unknown_case")
            cases[cid] = entry

            if entry.get("ok"):
                passed += 1
            else:
                failed += 1

        summary = {
            "total": len(latest),
            "passed": passed,
            "failed": failed,
        }

        return {
            "cases": cases,
            "summary": summary,
        }

    async def recent_results(self):
        snap = await self.snapshot()
        return list(snap["cases"].values())

metrics = _InMemorySnapshot()