"""Redis client -- delegates to w2v_common."""

from w2v_common.cache.redis_client import (
    RedisClient,
    close_redis,
    get_redis_client,
    init_redis,
)

__all__ = [
    "RedisClient",
    "close_redis",
    "get_redis_client",
    "init_redis",
]
