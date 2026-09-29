from fastapi.testclient import TestClient
from services.main_api import app

client = TestClient(app)

def test_feature_endpoint():
    resp = client.get("/eval/patch/features/p123?text=wrap(m)&category=style&message=m")
    assert resp.status_code == 200
    data = resp.json()
    print(f"_DATA: {data}")
    assert data["patch_id"] == "p123"
    assert "features" in data
    assert "score" in data