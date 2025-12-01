# tests/test_patch_api_endpoints.py
from fastapi.testclient import TestClient
from services.main_api import app

client = TestClient(app)


def test_score_api():
    resp = client.post("/eval/patch/score", json={
        "finding": {"message": "long line", "severity": "low", "category": "style"},
        "patch_text": "wrap(long)"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "score" in data
    assert data["score"] >= 0.0


def test_rank_api():
    resp = client.post("/eval/patch/rank", json={
        "finding": {"message": "long line", "severity": "low"},
        "patches": {"p1": "wrap()", "p2": "something else"},
    })
    assert resp.status_code == 200
    assert "ranked" in resp.json()


def test_preview_api():
    resp = client.post("/eval/patch/preview", json={
        "review": {
            "event_id": "evt", "repo": "r",
            "findings": [{"id": "f1", "severity": "low", "message": "m"}],
            "suggested_patches": {"p1": "wrap(m)"}
        }
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "preview" in data