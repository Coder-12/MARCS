import os
import pytest
from httpx import AsyncClient, ASGITransport

from services.metadata import app


@pytest.mark.asyncio
async def test_metadata_global():
    os.environ["MACRS_VERSION"] = "test-meta-v1"

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        resp = await ac.get("/metadata")
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "test-meta-v1"
        assert "config" in data
        assert "prompt_max_chars" in data["config"]


@pytest.mark.asyncio
async def test_metadata_for_file(tmp_path):
    # Create temporary file
    f = tmp_path / "sample.txt"
    f.write_text("hello\nworld")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        resp = await ac.get(f"/metadata?file={f}")
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"] == "ok"
        info = data["file"]

        assert info["exists"] is True
        assert info["lines"] == 2
        assert info["bytes"] > 0
        assert len(info["sha256"]) == 64


@pytest.mark.asyncio
async def test_metadata_nonexistent_file():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        resp = await ac.get("/metadata?file=/this/does/not/exist.txt")
        data = resp.json()

        assert data["file"]["exists"] is False
        assert data["file"]["sha256"] is None