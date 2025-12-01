# api/routes/health.py
from fastapi import APIRouter
from core.redis_client import redis_healthcheck
from core.db import get_db_pool

router = APIRouter(prefix="", tags=["health"])


@router.get("/health")
async def health_check():
    """
    Basic health check — API is running.
    """
    return {"status": "ok"}


@router.get("/full")
async def health_full():
    # API is always healthy if this route is reached
    api_ok = True

    # Postgres health with fail-safe
    try:
        from core.db import db_healthcheck
        postgres_ok = await db_healthcheck()
    except Exception:
        postgres_ok = False

    # Redis health with fail-safe
    try:
        from core.redis_client import redis_healthcheck
        redis_ok = await redis_healthcheck()
    except Exception:
        redis_ok = False

    return {
        "api": api_ok,
        "postgres": postgres_ok,
        "redis": redis_ok,
    }
