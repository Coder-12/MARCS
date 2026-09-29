# tests/test_api_endpoints.py
import os
import pytest
from httpx import AsyncClient, ASGITransport

from services.main_api import app


@pytest.mark.asyncio
async def test_api_health_and_ready(monkeypatch):
    monkeypatch.setenv("MACRS_VERSION", "api-test")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:

        # NOTE: Mounted under /health
        resp1 = await ac.get("/health/health")
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["status"] == "ok"
        assert d1["version"] == "api-test"

        resp2 = await ac.get("/health/ready")
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert d2["status"] == "ready"
        assert "details" in d2


@pytest.mark.asyncio
async def test_api_metadata_global(monkeypatch):
    monkeypatch.setenv("MACRS_VERSION", "api-meta-v1")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:

        resp = await ac.get("/metadata/metadata")
        assert resp.status_code == 200
        d = resp.json()
        assert d["status"] == "ok"
        assert d["version"] == "api-meta-v1"
        assert "config" in d


@pytest.mark.asyncio
async def test_api_metadata_file(tmp_path):
    f = tmp_path / "api.txt"
    f.write_text("hello api")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:

        resp = await ac.get(f"/metadata/metadata?file={f}")
        assert resp.status_code == 200
        d = resp.json()
        assert d["file"]["exists"] is True
        assert d["file"]["bytes"] > 0
        assert len(d["file"]["sha256"]) == 64