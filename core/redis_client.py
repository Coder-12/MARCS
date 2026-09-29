# core/redis_client.py
import time
from typing import Optional

import redis
from core.config import settings

# Create a top-level Redis client (synchronous). We keep it simple for Phase 0.
# For high-throughput production we may switch to aioredis and connection pools.
_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=settings.REDIS_HOST,
            port=int(settings.REDIS_PORT),
            db=int(settings.REDIS_DB),
            decode_responses=True,
        )
    return _redis_client


def redis_healthcheck(timeout: float = 2.0) -> bool:
    """
    Quick health check against Redis.
    Returns True if ping succeeds within the timeout, False otherwise.
    """
    client = get_redis_client()
    try:
        start = time.time()
        pong = client.ping()
        end = time.time()
        return pong is True and (end - start) <= timeout
    except Exception:
        return False