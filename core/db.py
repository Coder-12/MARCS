# core/db.py
import asyncio
import logging
from typing import Optional

import asyncpg
from asyncpg import Pool
from async_timeout import timeout

from core.config import settings

logger = logging.getLogger(__name__)

_db_pool: Optional[Pool] = None


async def init_db_pool(retries: int = 5, backoff_seconds: float = 1.0) -> None:
    """
    Initialize a global asyncpg pool.
    Phase 0: If DB is down or unavailable, do NOT raise - continue startup.
    Phase 1+: DB expected to be available.
    """
    global _db_pool
    if _db_pool is not None:
        return

    attempt = 0
    last_exc = None

    dsn = (
        f"postgresql://{settings.postgres_user}:"
        f"{settings.postgres_password}@{settings.postgres_host}:"
        f"{settings.postgres_port}/{settings.postgres_db}"
    )

    while attempt < retries:
        try:
            logger.info("Attempting to create asyncpg pool (attempt=%d)...", attempt + 1)

            _db_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)

            # Quick validation
            async with timeout(5):
                async with _db_pool.acquire() as conn:
                    await conn.execute("SELECT 1")

            logger.info("Database pool created successfully.")
            return

        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Failed to create DB pool (attempt=%d/%d): %s",
                attempt + 1,
                retries,
                exc,
            )
            # exponential backoff
            await asyncio.sleep(backoff_seconds * (2 ** attempt))
            attempt += 1

    # ───────────────────────────────────────────────────────────────
    # IMPORTANT CHANGE:
    # DO NOT RAISE ERROR IN PHASE 0
    # ───────────────────────────────────────────────────────────────
    logger.error(
        "Could not create DB pool after %d attempts. "
        "Continuing without database (Phase 0 mode).", retries
    )

    _db_pool = None     # <-- CRITICAL FIX: ensures health checks don't crash


async def close_db_pool():
    """
    Close the global asyncpg pool. Call during application shutdown.
    """
    global _db_pool
    if _db_pool:
        await _db_pool.close()
        logger.info("DB pool closed.")
    else:
        logger.info("DB pool was not initialized; nothing to close.")



def get_db_pool() -> Pool:
    """
    Return the active asyncpg Pool (synchronous accessor).
    Call only after init_db_pool() was awaited successfully.
    """
    if _db_pool is None:
        raise RuntimeError(
            "Database pool is not initialized. Call init_db_pool() during startup."
        )
    return _db_pool