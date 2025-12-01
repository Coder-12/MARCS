import json
from fastapi.testclient import TestClient
from api import app
from pathlib import Path

client = TestClient(app)

def test_golden_eval_smoke():
    # Load the test golden JSON
    path = Path("data/golden/v1/test_event.json")
    assert path.exists(), "GoldenCase file missing!"

    # Run evaluator API
    resp = client.get("/eval/golden/run")
    assert resp.status_code == 200

    data = resp.json()
    assert "status" in data
    assert "summary" in data

    # Ensure evaluator parsed at least 1 event
    assert data["summary"]["num_events"] >= 1

    # Smoke check for our test event ID presence OR generic fallback
    ids = [ev.get("event_id") for ev in data["summary"]["per_event"]]
    assert any("test_evt_0001" in str(i) for i in ids)