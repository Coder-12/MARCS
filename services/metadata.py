# services/metadata.py
from __future__ import annotations
import os
import json
from datetime import datetime, timezone
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from utils.file_hash import file_dict_hashes, sha256_of_text

app = FastAPI(title="MACRS Metadata Service")


def _file_metadata(path: str) -> dict:
    """
    Collect metadata for a given file path.
    Intended for local FS usage during demos/tests.
    """
    if not os.path.exists(path):
        return {
            "exists": False,
            "sha256": None,
            "bytes": 0,
            "lines": 0,
            "path": path,
        }

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        data = f.read()

    sha = sha256_of_text(data)
    lines = data.count("\n") + 1 if data else 0
    size = len(data.encode("utf-8"))

    return {
        "exists": True,
        "sha256": sha,
        "bytes": size,
        "lines": lines,
        "path": path,
    }


@app.get("/metadata")
async def metadata_endpoint(
    file: str | None = Query(default=None, description="Optional file path")
):
    """
    Returns:
    - if file is provided → metadata for that file
    - else → global metadata / system info
    """

    ts = datetime.now(timezone.utc).isoformat()
    version = os.environ.get("MACRS_VERSION", "dev")

    if file:
        info = _file_metadata(file)
        return JSONResponse({
            "status": "ok",
            "ts": ts,
            "version": version,
            "file": info,
        })

    # No file → return global metadata
    env_meta = {
        "prompt_max_chars": os.environ.get("MACRS_PROMPT_MAX_CHARS"),
        "max_patch_chars": os.environ.get("MACRS_MAX_PATCH_CHARS"),
        "macrs_version": version,
    }

    return JSONResponse({
        "status": "ok",
        "ts": ts,
        "version": version,
        "config": env_meta,
    })