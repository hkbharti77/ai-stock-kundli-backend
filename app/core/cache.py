"""
Redis Cache Layer — Premium caching client with graceful offline fallbacks.
"""

import json
import logging
from typing import Any
import redis.asyncio as aioredis
from app.core.config import get_settings

logger = logging.getLogger("app.cache")
settings = get_settings()


import asyncio

class RedisCache:
    """Async Redis caching client."""

    def __init__(self) -> None:
        self.redis_url = settings.REDIS_URL
        self._clients: dict[int, aioredis.Redis] = {}

    @property
    def client(self) -> aioredis.Redis:
        """Lazy initializer for async Redis client, specific to the current event loop."""
        try:
            loop = asyncio.get_running_loop()
            loop_id = id(loop)
        except RuntimeError:
            loop_id = 0
            
        if loop_id not in self._clients:
            self._clients[loop_id] = aioredis.Redis.from_url(
                self.redis_url, decode_responses=True
            )
        return self._clients[loop_id]

    async def get(self, key: str) -> Any | None:
        """Retrieve key value, deserialize from JSON, fall back on failure."""
        try:
            val = await self.client.get(key)
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"[CACHE ERROR] Failed to GET key '{key}': {e}")
        return None

    async def set(self, key: str, value: Any, ttl_seconds: int = 900) -> bool:
        """Serialize value to JSON and cache with TTL, fall back on failure."""
        try:
            payload = json.dumps(value)
            await self.client.set(key, payload, ex=ttl_seconds)
            return True
        except Exception as e:
            logger.error(f"[CACHE ERROR] Failed to SET key '{key}': {e}")
            return False

    async def delete(self, key: str) -> bool:
        """Invalidate single key, fall back on failure."""
        try:
            await self.client.delete(key)
            return True
        except Exception as e:
            logger.error(f"[CACHE ERROR] Failed to DELETE key '{key}': {e}")
            return False


# Singleton instance
cache = RedisCache()
