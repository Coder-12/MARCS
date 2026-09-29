# core/storage.py
from __future__ import annotations
import os
import sqlite3
import json
import asyncio
import tempfile
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from core.config import STORAGE_BACKEND, SQLITE_PATH, REDIS_URL
from core.logging import get_logger

logger = get_logger("core.storage")

# ------------------------------------------------------------
# Abstract Interface
# ------------------------------------------------------------

class StorageBackend:
    async def save_review(self, event_id: str, review_dict: Dict[str, Any]) -> None:
        raise NotImplementedError

    async def get_review(self, event_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def list_reviews(self) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError

    async def record_eval_metrics(self, case_id: str, payload: Dict[str, Any]) -> None:
        raise NotImplementedError

    async def get_metrics_snapshot(self) -> Dict[str, Any]:
        raise NotImplementedError

# ------------------------------------------------------------
# In-Memory Backend (unchanged)
# ------------------------------------------------------------

class InMemoryStorage(StorageBackend):
    def __init__(self):
        self._reviews: Dict[str, Dict[str, Any]] = {}
        self._metrics = {"cases": {}, "summary": {"total": 0, "passed": 0, "failed": 0}}

    async def save_review(self, event_id: str, review_dict: Dict[str, Any]) -> None:
        self._reviews[event_id] = review_dict

    async def get_review(self, event_id: str) -> Optional[Dict[str, Any]]:
        return self._reviews.get(event_id)

    async def list_reviews(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._reviews)

    async def record_eval_metrics(self, case_id: str, payload: Dict[str, Any]) -> None:
        self._metrics["cases"][case_id] = payload
        self._metrics["summary"]["total"] += 1
        if payload.get("ok"):
            self._metrics["summary"]["passed"] += 1
        else:
            self._metrics["summary"]["failed"] += 1

    async def get_metrics_snapshot(self) -> Dict[str, Any]:
        return dict(self._metrics)

# ------------------------------------------------------------
# SQLite Backend (unchanged)
# ------------------------------------------------------------

class SQLiteStorage(StorageBackend):
    def __init__(self, path: str = SQLITE_PATH):
        self.path = path
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.close()

    def _get_conn(self):
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    async def save_review(self, event_id: str, review_dict: Dict[str, Any]) -> None:
        def _save():
            conn = self._get_conn()
            ts = int(time.time())
            payload = json.dumps(review_dict, default=str)
            conn.execute(
                "INSERT OR REPLACE INTO reviews(event_id, repo, review_json, ts) VALUES(?,?,?,?)",
                (event_id, review_dict.get("repo"), payload, ts),
            )
            conn.commit()
            conn.close()
        await asyncio.to_thread(_save)

    async def get_review(self, event_id: str) -> Optional[Dict[str, Any]]:
        def _get():
            conn = self._get_conn()
            cur = conn.execute("SELECT review_json FROM reviews WHERE event_id = ?", (event_id,))
            row = cur.fetchone()
            conn.close()
            if not row:
                return None
            return json.loads(row["review_json"])
        return await asyncio.to_thread(_get)

    async def list_reviews(self) -> Dict[str, Dict[str, Any]]:
        def _list():
            conn = self._get_conn()
            cur = conn.execute("SELECT event_id, review_json FROM reviews ORDER BY ts DESC")
            out = {}
            for r in cur.fetchall():
                out[r["event_id"]] = json.loads(r["review_json"])
            conn.close()
            return out
        return await asyncio.to_thread(_list)

    async def record_eval_metrics(self, case_id: str, payload: Dict[str, Any]) -> None:
        def _rec():
            conn = self._get_conn()
            ts = int(time.time())
            conn.execute(
                "INSERT INTO metrics_evals(case_id, ok, tp, fp, fn, precision, recall, f1, ts, raw) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (case_id, bool(payload.get("ok")), payload.get("tp",0), payload.get("fp",0),
                 payload.get("fn",0), payload.get("precision",0.0), payload.get("recall",0.0),
                 payload.get("f1",0.0), ts, json.dumps(payload)),
            )
            conn.commit()
            conn.close()
        await asyncio.to_thread(_rec)

    async def get_metrics_snapshot(self) -> Dict[str, Any]:
        def _snapshot():
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT case_id, ok, tp, fp, fn, precision, recall, f1, datetime(ts,'unixepoch') as ts "
                "FROM metrics_evals ORDER BY ts DESC LIMIT 100"
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return {"latest": rows}
        return await asyncio.to_thread(_snapshot)

# ------------------------------------------------------------
# Redis Backend (unchanged)
# ------------------------------------------------------------

try:
    import aioredis
except Exception:
    aioredis = None

class RedisStorage(StorageBackend):
    def __init__(self, url: str = REDIS_URL):
        if aioredis is None:
            raise RuntimeError("aioredis not installed")
        self.url = url
        self._redis = None

    async def _ensure(self):
        if self._redis is None:
            self._redis = await aioredis.from_url(self.url)

    async def save_review(self, event_id: str, review_dict: Dict[str, Any]) -> None:
        await self._ensure()
        await self._redis.hset("reviews", event_id, json.dumps(review_dict))

    async def get_review(self, event_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure()
        v = await self._redis.hget("reviews", event_id)
        if not v:
            return None
        return json.loads(v)

    async def list_reviews(self) -> Dict[str, Dict[str, Any]]:
        await self._ensure()
        allkv = await self._redis.hgetall("reviews")
        return {k.decode(): json.loads(v) for k, v in allkv.items()}

    async def record_eval_metrics(self, case_id: str, payload: Dict[str, Any]) -> None:
        await self._ensure()
        await self._redis.lpush("metrics_evals", json.dumps({"case_id": case_id, **payload}))

    async def get_metrics_snapshot(self) -> Dict[str, Any]:
        await self._ensure()
        arr = await self._redis.lrange("metrics_evals", 0, 99)
        return {"latest": [json.loads(x) for x in arr]}

# ------------------------------------------------------------
# NEW: FileStorage Backend (Step 0.32)
# ------------------------------------------------------------

class FileStorage(StorageBackend):
    """
    Atomic, crash-safe file storage.
    Stores each review as <dir>/<event_id>.json.
    Metrics stored in <dir>/_metrics.json.
    """
    def __init__(self, base_dir: str = "data/reviews"):
        self.base_dir = os.path.abspath(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        self.metrics_path = os.path.join(self.base_dir, "_metrics.json")

    # ---------- reviews ----------
    def _path_for(self, event_id: str) -> str:
        safe = str(event_id).replace("/", "_")
        return os.path.join(self.base_dir, f"{safe}.json")

    async def save_review(self, event_id: str, review_dict: Dict[str, Any]) -> None:
        await asyncio.to_thread(self._atomic_write_json, self._path_for(event_id), review_dict)

    async def get_review(self, event_id: str) -> Optional[Dict[str, Any]]:
        path = self._path_for(event_id)
        if not os.path.exists(path):
            return None
        return await asyncio.to_thread(self._read_json, path)

    async def list_reviews(self) -> Dict[str, Dict[str, Any]]:
        def _list() -> Dict[str, Dict[str, Any]]:
            out = {}
            for fn in sorted(os.listdir(self.base_dir)):
                if not fn.endswith(".json") or fn == "_metrics.json":
                    continue
                ev = fn[:-5]
                try:
                    out[ev] = self._read_json(os.path.join(self.base_dir, fn))
                except Exception:
                    continue
            return out
        return await asyncio.to_thread(_list)

    # ---------- metrics ----------
    async def record_eval_metrics(self, case_id: str, payload: Dict[str, Any]) -> None:
        data = await asyncio.to_thread(self._read_json, self.metrics_path) if os.path.exists(self.metrics_path) else {"latest": []}
        data["latest"].insert(0, {"case_id": case_id, **payload})
        data["latest"] = data["latest"][:100]
        await asyncio.to_thread(self._atomic_write_json, self.metrics_path, data)

    async def get_metrics_snapshot(self) -> Dict[str, Any]:
        if not os.path.exists(self.metrics_path):
            return {"latest": []}
        return await asyncio.to_thread(self._read_json, self.metrics_path)

    # ---------- helpers ----------
    def _atomic_write_json(self, path: str, data: Dict[str, Any]) -> None:
        tmp_fd, tmp = tempfile.mkstemp(prefix="tmp_", dir=self.base_dir)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(
                    data,
                    fh, # type: ignore[arg-type]
                    default=str,
                    ensure_ascii=False,
                    indent=2
                )
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except:
                    pass

    def _read_json(self, path: str):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

# ------------------------------------------------------------
# STORAGE FACTORY (FINAL)
# ------------------------------------------------------------

_storage_singleton: Optional[StorageBackend] = None

def get_storage() -> StorageBackend:
    global _storage_singleton
    if _storage_singleton is not None:
        return _storage_singleton

    backend = STORAGE_BACKEND.lower().strip()

    if backend == "sqlite":
        logger.info("storage_backend", backend="sqlite", path=SQLITE_PATH)
        _storage_singleton = SQLiteStorage(SQLITE_PATH)

    elif backend == "redis":
        if aioredis is None:
            logger.error("redis_backend_missing", msg="aioredis not installed; falling back to memory")
            _storage_singleton = InMemoryStorage()
        else:
            logger.info("storage_backend", backend="redis", url=REDIS_URL)
            _storage_singleton = RedisStorage(REDIS_URL)

    elif backend == "file":
        base = os.environ.get("MACRS_REVIEW_DIR", "data/reviews")
        logger.info("storage_backend", backend="file", dir=base)
        _storage_singleton = FileStorage(base)

    else:
        logger.info("storage_backend", backend="memory")
        _storage_singleton = InMemoryStorage()

    return _storage_singleton