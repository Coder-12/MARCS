import pytest
import os

from httpx import AsyncClient, ASGITransport
from services.health import app


@pytest.mark.asyncio
async def test_health_and_ready():
    os.environ["MACRS_VERSION"] = "unit-test-1"

    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/health")
        assert r.status_code == 200
        j = r.json()
        assert j["status"] == "ok"
        assert j["version"] == "unit-test-1"

        r2 = await ac.get("/ready")
        assert r2.status_code == 200
        jr = r2.json()
        assert jr["status"] == "ready"
        assert "prompt_max_chars" in jr["details"]