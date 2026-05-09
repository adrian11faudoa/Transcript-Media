"""
Redis connection management for pub/sub and caching.
"""
import logging
from typing import Optional

import aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis: Optional[aioredis.Redis] = None


async def init_redis():
    """Initialize the Redis connection pool."""
    global _redis
    _redis = await aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=50,
    )
    logger.info("Redis connection established.")


async def close_redis():
    global _redis
    if _redis:
        await _redis.close()
        _redis = None


def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialized.")
    return _redis
