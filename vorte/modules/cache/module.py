"""
Vorte Cache Module
===================
Cache module for the Vorte Framework. Provides a 4-layer caching system:
L1 (in-process memory), L2 (Redis), L3 (CDN), and L4 (database).

Registers the CacheManager as a singleton dependency in the DI container.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from vorte.core.config import CacheConfig
from vorte.core.module import Module, ModuleMeta, ModulePriority

from .cache import CacheManager

if TYPE_CHECKING:
    from vorte.core.app import Vorte

logger = logging.getLogger("vorte.modules.cache")


class CacheModule(Module):
    """
    Vorte Cache Module — 4-layer caching system.

    Provides fast, multi-layer caching with tag-based invalidation,
    pattern matching, and decorator-based caching.

    Layers:
        - **L1**: In-process memory (dict-based, LRU eviction)
        - **L2**: Distributed Redis (shared across workers)
        - **L3**: Edge CDN (purge-only, for HTTP responses)
        - **L4**: Database (persistent across restarts)

    Configuration (via ``VORTE_`` environment variables or module config):

        ========== ======================== ==============
        Setting    Env Variable             Default
        ========== ======================== ==============
        driver     CACHE_DRIVER             redis
        default_ttl CACHE_TTL              300
        l1_enabled (implicit)              True
        l1_max_size CACHE_L1_MAX_SIZE     1000
        l2_enabled CACHE_L2_ENABLED        True
        l3_cdn_url  CACHE_CDN_URL          (empty)
        l4_db_cache CACHE_DB_ENABLED       False
        ========== ======================== ==============

    Usage:
        app = Vorte()
        app.register(CacheModule())

        # In your route:
        @app.get("/users/{user_id}")
        async def get_user(user_id: int, cache = Depends(CacheManager)):
            data = await cache.get(f"user:{user_id}")
            if data is None:
                data = await db.fetch_user(user_id)
                await cache.set(f"user:{user_id}", data, ttl=300, tags=["users"])
            return data
    """

    meta = ModuleMeta(
        name="cache",
        version="1.0.0",
        description="4-layer caching system (memory, Redis, CDN, database)",
        priority=ModulePriority.CACHE,
        dependencies=[],
    )

    def __init__(self, **config: Any):
        super().__init__(**config)
        self._cache_config = CacheConfig(**{
            k: v for k, v in config.items()
            if k in CacheConfig.__dataclass_fields__
        })
        self._manager: Optional[CacheManager] = None

    @property
    def manager(self) -> CacheManager:
        """Get the cache manager instance."""
        if self._manager is None:
            raise RuntimeError("CacheModule has not been initialized yet")
        return self._manager

    def register(self, app: "Vorte") -> None:
        """
        Register the cache module with the application.

        Creates the CacheManager and registers it as a singleton
        in the DI container so other modules and routes can inject it.
        """
        self._manager = CacheManager(self._cache_config)
        app.container.register_instance(CacheManager, self._manager)

        logger.debug("CacheModule registered (driver=%s, default_ttl=%ds)",
                      self._cache_config.driver, self._cache_config.default_ttl)

    async def on_startup(self) -> None:
        """Initialize cache layers (connect to Redis, DB, etc.)."""
        if self._manager is not None:
            await self._manager.initialize()

    async def on_shutdown(self) -> None:
        """Shutdown cache layers gracefully."""
        if self._manager is not None:
            await self._manager.shutdown()

    async def health_check(self) -> Dict[str, Any]:
        """Check health of all cache layers."""
        base = await super().health_check()
        if self._manager is not None:
            base["layers"] = await self._manager.health_check()
            base["stats"] = self._manager.stats()
        return base
