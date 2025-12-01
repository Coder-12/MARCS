import os
import shutil
import json
import asyncio
import tempfile
import pytest

from core.storage import FileStorage


@pytest.mark.asyncio
async def test_file_storage_basic_save_and_get():
    tmp = tempfile.mkdtemp(prefix="test_filestore_")
    try:
        st = FileStorage(tmp)

        review = {"event_id": "evt1", "repo": "me/r", "findings": [1,2,3]}
        await st.save_review("evt1", review)

        path = os.path.join(tmp, "evt1.json")
        assert os.path.exists(path)

        loaded = await st.get_review("evt1")
        assert loaded == review

    finally:
        shutil.rmtree(tmp)


@pytest.mark.asyncio
async def test_file_storage_list_reviews():
    tmp = tempfile.mkdtemp(prefix="test_filestore_")
    try:
        st = FileStorage(tmp)

        await st.save_review("a", {"x": 1})
        await st.save_review("b", {"y": 2})

        allr = await st.list_reviews()
        assert "a" in allr
        assert "b" in allr
        assert allr["a"]["x"] == 1
        assert allr["b"]["y"] == 2

    finally:
        shutil.rmtree(tmp)


@pytest.mark.asyncio
async def test_file_storage_atomic_write():
    """
    Ensures tmp file is removed and final file is valid JSON
    """
    tmp = tempfile.mkdtemp(prefix="test_filestore_")
    try:
        st = FileStorage(tmp)
        await st.save_review("evt", {"ok": True})

        # ensure no leftover tmp files
        leftovers = [fn for fn in os.listdir(tmp) if fn.startswith("tmp_")]
        assert leftovers == []

        # ensure valid JSON
        with open(os.path.join(tmp, "evt.json"), "r") as fh:
            data = json.load(fh)
        assert data == {"ok": True}

    finally:
        shutil.rmtree(tmp)


@pytest.mark.asyncio
async def test_file_storage_metrics():
    tmp = tempfile.mkdtemp(prefix="test_filestore_")
    try:
        st = FileStorage(tmp)

        await st.record_eval_metrics("case1", {"ok": True, "tp": 1})
        await st.record_eval_metrics("case2", {"ok": False, "fn": 2})

        snap = await st.get_metrics_snapshot()
        assert "latest" in snap
        assert len(snap["latest"]) == 2

        assert snap["latest"][0]["case_id"] == "case2"
        assert snap["latest"][1]["case_id"] == "case1"

    finally:
        shutil.rmtree(tmp)