"""
Vorte Database Module
======================
Main module entry-point that wires up connection management, the query
builder, migrations, seeders, and the ORM base into the Vorte module system.

Usage::

    app = Vorte()
    app.register(
        DatabaseModule(
            url="postgresql+asyncpg://user:pass@localhost/mydb",
            pool_size=20,
            max_overflow=10,
            echo=False,
        )
    )
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from vorte.core.module import Module, ModuleMeta, ModulePriority
from vorte.core.config import DatabaseConfig
from vorte.modules.database.connection import ConnectionManager
from vorte.modules.database.migrations import MigrationManager
from vorte.modules.database.model import Base, VorteModel
from vorte.modules.database.query import QueryBuilder
from vorte.modules.database.seeders import SeederManager


class DatabaseModule(Module):
    """
    Async-native database module built on SQLAlchemy 2.0.

    Provides:
    - Async engine + session management with read-replica support
    - ORM base model with UUID PK and timestamps
    - Chainable query builder
    - Alembic-based migrations (auto-generate, upgrade, downgrade)
    - Seeder system for populating reference data

    Configuration (passed as keyword arguments at registration)::

        DatabaseModule(
            url="postgresql+asyncpg://...",
            pool_size=20,
            max_overflow=10,
            echo=False,
            read_replica_urls=["postgresql+asyncpg://replica1/..."],
        )

    Or via :class:`DatabaseConfig`::

        DatabaseModule(config=database_config)

    Once registered, the module is accessible via::

        db_module = app.modules.get("database")
        db = db_module.query          # QueryBuilder instance
        conn = db_module.connection   # ConnectionManager instance
        migrations = db_module.migrations
    """

    meta = ModuleMeta(
        name="database",
        version="1.0.0",
        description="Async database module with SQLAlchemy 2.0 ORM, query builder, migrations, and seeders",
        priority=ModulePriority.DATABASE,
    )

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def __init__(
        self,
        *,
        config: Optional[DatabaseConfig] = None,
        url: Optional[str] = None,
        pool_size: Optional[int] = None,
        max_overflow: Optional[int] = None,
        echo: Optional[bool] = None,
        read_replica_urls: Optional[List[str]] = None,
        migrations_dir: str = "migrations",
        seeders_dir: str = "database/seeders",
        auto_run_migrations: bool = False,
        auto_create_tables: bool = True,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)

        self._db_config = config
        self._url = url
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._echo = echo
        self._read_replica_urls = read_replica_urls
        self._migrations_dir = migrations_dir
        self._seeders_dir = seeders_dir
        self._auto_run_migrations = auto_run_migrations
        self._auto_create_tables = auto_create_tables

        # Populated during register()
        self._connection: Optional[ConnectionManager] = None
        self._query: Optional[QueryBuilder] = None
        self._migrations: Optional[MigrationManager] = None
        self._seeders: Optional[SeederManager] = None
        
        # Global cache for compiled SQL plans and prepared statement structures
        self._query_cache: Dict[str, Any] = {}

    def get_cached_query(self, key: str, creator_fn: Callable[[], Any]) -> Any:
        """Get or create a cached query plan, SQL string, or compiled statement."""
        if key not in self._query_cache:
            self._query_cache[key] = creator_fn()
        return self._query_cache[key]

    # ------------------------------------------------------------------
    # Module lifecycle
    # ------------------------------------------------------------------

    def register(self, app: Any) -> None:
        """
        Register the database module with the Vorte application.

        Creates the connection manager, query builder, migration manager,
        and seeder manager. Wires them into the DI container.
        """
        # Merge config: explicit kwargs > DatabaseConfig > env defaults
        db_config = self._db_config
        url = self._url or (db_config.url if db_config else None)
        pool_size = self._pool_size or (db_config.pool_size if db_config else None)
        max_overflow = self._max_overflow or (db_config.max_overflow if db_config else None)
        echo = self._echo if self._echo is not None else (db_config.echo if db_config else False)
        read_replicas = self._read_replica_urls or (
            db_config.read_replica_urls if db_config else []
        )

        # Initialize connection manager
        self._connection = ConnectionManager(
            config=db_config,
            url=url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            echo=echo,
            read_replica_urls=read_replicas,
        )
        self._connection.initialize()

        # Initialize query builder
        self._query = QueryBuilder(self._connection)

        # Initialize migration manager
        self._migrations = MigrationManager(
            self._connection,
            Base.metadata,
            migrations_dir=self._migrations_dir,
        )
        self._migrations.initialize()

        # Initialize seeder manager
        env = getattr(app, "_settings", None)
        env_name = getattr(env, "app_env", "development") if env else "development"
        self._seeders = SeederManager(
            self._connection,
            seeders_dir=self._seeders_dir,
            environment=env_name,
        )

        # Note: Auto-creation of tables is moved to on_startup

        # Register in DI container
        if hasattr(app, "container") and app.container is not None:
            app.container.register_instance(ConnectionManager, self._connection)
            app.container.register_instance(QueryBuilder, self._query)
            app.container.register_instance(MigrationManager, self._migrations)
            app.container.register_instance(SeederManager, self._seeders)

    async def on_startup(self) -> None:
        """Run auto-migrations or table creation if configured."""
        if self._auto_create_tables:
            await self._create_tables()

        if self._auto_run_migrations and self._migrations is not None:
            await self._migrations.upgrade()

    async def on_shutdown(self) -> None:
        """Close all database connection pools."""
        if self._connection is not None:
            await self._connection.close()

    async def drop_tables(self) -> None:
        """
        Drop all tables registered in the metadata.
        WARNING: This is a destructive operation.
        """
        if self._connection is None:
            return

        async with self._connection.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def run_seeders(self) -> None:
        """Run all database seeders."""
        if self._seeders is not None:
            await self._seeders.run()

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> Dict[str, Any]:
        """
        Check database connectivity.

        Returns a dict with connection pool status and latency measurements.
        """
        if self._connection is None:
            return {
                "module": self.meta.name,
                "status": "unhealthy",
                "error": "Connection manager not initialized",
            }

        try:
            health = await self._connection.health_check()
            all_healthy = health.get("primary", {}).get("status") == "healthy"
            replicas_ok = all(
                r.get("status") == "healthy" for r in health.get("replicas", [])
            )
            is_healthy = all_healthy and (not health.get("replicas") or replicas_ok)

            return {
                "module": self.meta.name,
                "status": "healthy" if is_healthy else "degraded",
                "primary": health.get("primary"),
                "replicas": health.get("replicas", []),
                "replica_count": len(self._connection.read_engines) if self._connection else 0,
            }
        except Exception as exc:
            return {
                "module": self.meta.name,
                "status": "unhealthy",
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def connection(self) -> ConnectionManager:
        """The async connection manager."""
        if self._connection is None:
            raise RuntimeError("DatabaseModule has not been registered yet.")
        return self._connection

    @property
    def query(self) -> QueryBuilder:
        """The chainable query builder."""
        if self._query is None:
            raise RuntimeError("DatabaseModule has not been registered yet.")
        return self._query

    @property
    def migrations(self) -> MigrationManager:
        """The migration manager."""
        if self._migrations is None:
            raise RuntimeError("DatabaseModule has not been registered yet.")
        return self._migrations

    @property
    def seeders(self) -> SeederManager:
        """The seeder manager."""
        if self._seeders is None:
            raise RuntimeError("DatabaseModule has not been registered yet.")
        return self._seeders

    @property
    def metadata(self):
        """The SQLAlchemy metadata containing all registered models."""
        return Base.metadata

    # ------------------------------------------------------------------
    # Convenience pass-through methods
    # ------------------------------------------------------------------

    # These delegate to the QueryBuilder for quick access:
    # db.find(), db.create(), db.update(), db.delete(), etc.

    async def find(self, model: Any, id: Any):
        """Delegate to :meth:`QueryBuilder.find`."""
        return await self.query.find(model, id)

    async def create(self, model: Any, data: Dict[str, Any]):
        """Delegate to :meth:`QueryBuilder.create`."""
        return await self.query.create(model, data)

    async def update(self, model: Any, id: Any, data: Dict[str, Any]):
        """Delegate to :meth:`QueryBuilder.update`."""
        return await self.query.update(model, id, data)

    async def delete(self, model: Any, id: Any):
        """Delegate to :meth:`QueryBuilder.delete`."""
        return await self.query.delete(model, id)

    async def find_all(self, model: Any):
        """Delegate to :meth:`QueryBuilder.find_all`."""
        return await self.query.find_all(model)

    def query_model(self, model: Any):
        """Delegate to :meth:`QueryBuilder.query`."""
        return self.query.query(model)

    async def count(self, model: Any) -> int:
        """Delegate to :meth:`QueryBuilder.count`."""
        return await self.query.count(model)

    async def exists(self, model: Any, **filters: Any) -> bool:
        """Delegate to :meth:`QueryBuilder.exists`."""
        return await self.query.exists(model, **filters)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _create_tables(self) -> None:
        """
        Emit CREATE TABLE statements for all registered models
        that do not yet exist in the database.

        Uses ``create_all`` with ``checkfirst=True`` to be idempotent.
        """
        if self._connection is None:
            return

        async with self._connection.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, checkfirst=True)
