# eval/run_eval.py (improved)
import os
import sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import time
import json
from eval.loader import load_golden_cases
from eval.compare import compare_review
from fastapi.testclient import TestClient
from api.app import app
from services.review_store import get_review

client = TestClient(app)

def wait_for_review(delivery_id: str, timeout: float = 5.0, poll: float = 0.1):
    import asyncio

    deadline = time.time() + timeout
    while time.time() < deadline:

        # ⬅️ NEW: run one worker tick per poll
        try:
            asyncio.run(run_worker_once())
        except RuntimeError:
            # event loop already running → create a new loop
            loop = asyncio.new_event_loop()
            loop.run_until_complete(run_worker_once())
            loop.close()

        # Try public API first
        r = client.get(f"/review/{delivery_id}")
        if r.status_code == 200:
            return r.json()

        # Direct backend check (in-memory)
        try:
            review = get_review(delivery_id)
            if hasattr(review, "__await__"):
                review = asyncio.run(review)
            if review:
                return review.model_dump()
        except Exception:
            pass

        time.sleep(poll)

    return None

def run_single(case):
    print(f"\n=== Running {case.id}: {case.description} ===")
    # 1. Feed webhook
    r = client.post("/webhook", headers=case.headers, json=case.payload)
    if r.status_code != 200:
        return False, [f"webhook_post_failed:{r.status_code}"]

    delivery_id = case.headers.get("X-GitHub-Delivery")
    review = wait_for_review(delivery_id, timeout=6.0)
    if not review:
        return False, ["review_not_found"]

    ok, errs = compare_review(review, case.expected)
    return ok, errs

async def run_worker_once():
    from orchestration.queue import dequeue_event
    from worker.worker import process_event

    evt = dequeue_event()
    if evt:
        await process_event(evt)

def main():
    cases = load_golden_cases()
    results = {}
    for c in cases:
        ok, errs = run_single(c)
        results[c.id] = {"ok": ok, "errors": errs}
    return results

if __name__ == "__main__":
    print(json.dumps(main(), indent=2))