"""
Vorte Database Connection Management
=====================================
Async engine, session factory, and connection pool management
using SQLAlchemy 2.0 async API. Supports primary + read replica routing.
"""

from __future__ import annotations

import asyncio
import random
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool
from sqlalchemy.sql import text

from vorte.core.config import DatabaseConfig


class ConnectionManager:
    """
    Manages async database connections for SQLAlchemy 2.0.

    Handles primary and read-replica connection pooling, session lifecycle,
    and provides health-check and connectivity utilities.

    Usage::

        cm = ConnectionManager(config)
        await cm.initialize()

        async with cm.session() as session:
            result = await session.execute(text("SELECT 1"))

        await cm.close()
    """

    def __init__(
        self,
        config: Optional[DatabaseConfig] = None,
        *,
        url: Optional[str] = None,
        pool_size: Optional[int] = None,
        max_overflow: Optional[int] = None,
        echo: bool = False,
        read_replica_urls: Optional[List[str]] = None,
    ):
        self._config = config
        self._url = url or (config.url if config else "sqlite+aiosqlite:///vorte.db")
        self._pool_size = pool_size or (config.pool_size if config else 20)
        self._max_overflow = max_overflow or (config.max_overflow if config else 10)
        self._echo = echo or (config.echo if config else False)
        self._read_replica_urls = read_replica_urls or (
            config.read_replica_urls if config else []
        )

        self._engine: Optional[AsyncEngine] = None
        self._read_engines: List[AsyncEngine] = []
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None
        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create the async engine(s) and session factory."""
        if self._initialized:
            return

        engine_kwargs = {
            "pool_pre_ping": True,
            "pool_recycle": 1800, # Optimized connection recycle
            "echo": self._echo,
        }
        
        # Auto-configure StaticPool for SQLite to prevent memory isolation issues across threads
        if "sqlite" in self._url:
            from sqlalchemy.pool import StaticPool
            engine_kwargs["poolclass"] = StaticPool
        else:
            engine_kwargs["pool_timeout"] = 30   # Optimized pool timeout
            engine_kwargs["pool_size"] = self._pool_size
            engine_kwargs["max_overflow"] = self._max_overflow

        self._engine = create_async_engine(self._url, **engine_kwargs)

        # Create read-replica engines (if configured)
        for replica_url in self._read_replica_urls:
            replica_kwargs = {
                "pool_pre_ping": True,
                "pool_recycle": 1800,
                "echo": self._echo,
            }
            if "sqlite" in replica_url:
                from sqlalchemy.pool import StaticPool
                replica_kwargs["poolclass"] = StaticPool
            else:
                replica_kwargs["pool_timeout"] = 30
                replica_kwargs["pool_size"] = self._pool_size
                replica_kwargs["max_overflow"] = self._max_overflow

            replica_engine = create_async_engine(replica_url, **replica_kwargs)
            self._read_engines.append(replica_engine)

        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        self._initialized = True

    async def close(self) -> None:
        """Dispose all engine connection pools."""
        if self._read_engines:
            for engine in self._read_engines:
                await engine.dispose()
            self._read_engines.clear()

        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            self._initialized = False

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def session(
        self,
        *,
        read_only: bool = False,
        begin: bool = True,
    ) -> AsyncGenerator[AsyncSession, None]:
        """
        Context manager that yields an :class:`AsyncSession`.

        Args:
            read_only: If *True* and read replicas are configured, the session
                will bind to a randomly-selected replica engine.
            begin: If *True*, immediately begin a transaction block.

        Yields:
            An async session connected to the primary or a replica.
        """
        if not self._initialized:
            self.initialize()

        assert self._session_factory is not None

        if read_only and self._read_engines:
            engine = random.choice(self._read_engines)
            factory = async_sessionmaker(
                bind=engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
            )
        else:
            factory = self._session_factory

        session: AsyncSession = factory()
        try:
            if begin:
                async with session.begin():
                    yield session
            else:
                yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    def get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Return the underlying session maker (for DI frameworks)."""
        if not self._initialized:
            raise RuntimeError("ConnectionManager has not been initialized.")
        assert self._session_factory is not None
        return self._session_factory

    # ------------------------------------------------------------------
    # Raw connection helpers
    # ------------------------------------------------------------------

    async def execute_raw(self, statement: str, params: Optional[Dict] = None) -> Any:
        """Execute raw SQL on the primary engine within a session."""
        async with self.session() as session:
            result = await session.execute(text(statement), params or {})
            return result

    async def execute_on_replica(
        self, statement: str, params: Optional[Dict] = None
    ) -> Any:
        """Execute raw SQL on a random read replica (if configured)."""
        async with self.session(read_only=True) as session:
            result = await session.execute(text(statement), params or {})
            return result

    # ------------------------------------------------------------------
    # Health / diagnostics
    # ------------------------------------------------------------------

    def get_pool_metrics(self) -> Dict[str, Any]:
        """Get connection pooling metrics for the primary engine."""
        if not self._initialized or self._engine is None:
            return {"status": "uninitialized"}

        pool = self._engine.pool
        metrics = {
            "driver": self._engine.driver,
            "pool_class": pool.__class__.__name__,
        }
        
        # Safe attribute/method checks on the pool object
        if hasattr(pool, "size"):
            metrics["pool_size"] = pool.size()
        else:
            metrics["pool_size"] = 0
            
        if hasattr(pool, "checkedout"):
            metrics["checked_out"] = pool.checkedout()
        else:
            metrics["checked_out"] = 0
            
        if hasattr(pool, "overflow"):
            metrics["overflow"] = pool.overflow()
        else:
            metrics["overflow"] = 0
            
        if hasattr(pool, "max_overflow"):
            metrics["max_overflow"] = pool.max_overflow()
        else:
            metrics["max_overflow"] = 0

        # Calculate utilization percentage
        if metrics["pool_size"] > 0:
            total_capacity = metrics["pool_size"] + max(0, metrics["max_overflow"])
            if total_capacity > 0:
                metrics["utilization_pct"] = round((metrics["checked_out"] / total_capacity) * 100, 2)
            else:
                metrics["utilization_pct"] = 0.0
        else:
            metrics["utilization_pct"] = 0.0
            
        return metrics

    async def health_check(self) -> Dict[str, Any]:
        """Run a lightweight connectivity check against all engines."""
        results: Dict[str, Any] = {"primary": "unknown", "replicas": []}

        # Primary
        try:
            start = time.monotonic()
            async with self.session() as session:
                await session.execute(text("SELECT 1"))
            latency_ms = (time.monotonic() - start) * 1000
            results["primary"] = {
                "status": "healthy",
                "latency_ms": round(latency_ms, 2),
                "url": self._mask_url(self._url),
                "pool": self.get_pool_metrics(),
            }
        except Exception as exc:
            results["primary"] = {"status": "unhealthy", "error": str(exc)}

        # Replicas
        for i, engine in enumerate(self._read_engines):
            try:
                start = time.monotonic()
                async with self.session(read_only=True) as session:
                    await session.execute(text("SELECT 1"))
                latency_ms = (time.monotonic() - start) * 1000
                results["replicas"].append(
                    {
                        "index": i,
                        "status": "healthy",
                        "latency_ms": round(latency_ms, 2),
                    }
                )
            except Exception as exc:
                results["replicas"].append(
                    {"index": i, "status": "unhealthy", "error": str(exc)}
                )

        return results

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("ConnectionManager has not been initialized.")
        return self._engine

    @property
    def read_engines(self) -> List[AsyncEngine]:
        return list(self._read_engines)

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def url(self) -> str:
        return self._url

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _mask_url(url: str) -> str:
        """Hide password portion of a database URL for safe logging."""
        # Handle postgresql+asyncpg, mysql+aiomysql, etc.
        if "://" in url and "@" in url:
            scheme_end = url.index("://") + 3
            at_pos = url.index("@")
            return url[:scheme_end] + "***:***" + url[at_pos:]
        return url
