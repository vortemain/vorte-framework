"""
Vorte Migrations
=================
Alembic-based migration system with auto-generation, migration running,
rollback, and status tracking.

Usage::

    mgr = MigrationManager(connection, Base.metadata)
    await mgr.initialize()

    # Auto-generate a migration
    await mgr.generate_migration("add_posts_table")

    # Run pending migrations
    await mgr.upgrade()

    # Rollback one step
    await mgr.downgrade()

    # Show current status
    status = await mgr.status()
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from vorte.modules.database.connection import ConnectionManager


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_MIGRATIONS_DIR = "migrations"
REVISION_ID_RE = re.compile(r"^revision\s*(?::\s*[\w]+)?\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
DOWN_REVISION_RE = re.compile(r"^down_revision\s*(?::\s*[\w\s,\[\]|]+)?\s*=\s*['\"]?([^'\"]+)['\"]?", re.MULTILINE)


class MigrationManager:
    """
    Manages Alembic-based database migrations.

    Provides methods for generating, applying, reverting, and inspecting
    migrations, as well as a programmatic fallback for environments where
    Alembic CLI is unavailable.

    Args:
        connection: The database connection manager.
        metadata: SQLAlchemy ``MetaData`` containing all model definitions.
        migrations_dir: Path to the migrations directory (relative or absolute).
    """

    def __init__(
        self,
        connection: ConnectionManager,
        metadata: Any,
        *,
        migrations_dir: str = DEFAULT_MIGRATIONS_DIR,
    ):
        self._connection = connection
        self._metadata = metadata
        self._migrations_dir = Path(migrations_dir)

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create the migrations directory and Alembic scaffolding if needed."""
        self._migrations_dir.mkdir(parents=True, exist_ok=True)
        versions_dir = self._migrations_dir / "versions"
        versions_dir.mkdir(parents=True, exist_ok=True)

        # Write alembic.ini if it doesn't exist
        alembic_ini = self._migrations_dir / "alembic.ini"
        if not alembic_ini.exists():
            self._write_alembic_ini()

        # Write env.py if it doesn't exist
        env_py = self._migrations_dir / "env.py"
        if not env_py.exists():
            self._write_env_py()

    # ------------------------------------------------------------------
    # Generate migration
    # ------------------------------------------------------------------

    async def generate_migration(
        self,
        message: str,
        *,
        autogenerate: bool = True,
        revision_id: Optional[str] = None,
    ) -> str:
        """
        Auto-generate a new migration file.

        Detects schema changes by comparing the current ``metadata`` against
        the actual database, then writes a migration script.

        Args:
            message: Human-readable description of the migration.
            autogenerate: If *True*, auto-detect schema differences.
            revision_id: Optional explicit revision ID.

        Returns:
            The file path of the generated migration.
        """
        rev_id = revision_id or self._make_revision_id()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_message = re.sub(r"[^a-zA-Z0-9_]", "_", message).lower()
        filename = f"{timestamp}_{safe_message}_{rev_id}.py"
        filepath = self._migrations_dir / "versions" / filename

        # Get current head
        head = await self._get_head()

        if autogenerate:
            # Use Alembic's autogenerate if available
            try:
                return await self._alembic_generate(message, rev_id, autogenerate=True)
            except (ImportError, Exception):
                pass

        # Fallback: generate a template migration
        upgrade_ops = await self._diff_metadata_vs_db()
        downgrade_ops = await self._reverse_ops(upgrade_ops)

        content = self._render_migration_template(
            revision_id=rev_id,
            down_revision=head,
            message=message,
            upgrade_ops=upgrade_ops,
            downgrade_ops=downgrade_ops,
        )

        filepath.write_text(content, encoding="utf-8")
        return str(filepath)

    # ------------------------------------------------------------------
    # Upgrade / downgrade
    # ------------------------------------------------------------------

    async def upgrade(
        self,
        target: str = "head",
        step: Optional[int] = None,
    ) -> List[str]:
        """
        Run pending migrations up to *target*.

        Args:
            target: Revision ID, ``"head"``, or ``"+N"``.
            step: Number of steps to apply (overrides *target*).

        Returns:
            List of applied revision IDs.
        """
        # Ensure tracking table exists
        await self._ensure_tracking_table()

        async with self._connection.session(begin=False) as session:
            applied = await self._get_applied_revisions(session)

            all_revisions = await self._discover_revisions()
            ordered = self._topological_sort(all_revisions)

            pending = [r for r in ordered if r["id"] not in applied]

            if target != "head":
                target_idx = -1
                for idx, r in enumerate(pending):
                    if r["id"] == target:
                        target_idx = idx
                        break
                if target_idx != -1:
                    pending = pending[:target_idx + 1]
                elif target in applied:
                    pending = []

            if step is not None:
                pending = pending[:step]

            applied_ids: List[str] = []
            for rev in pending:
                module_path = rev["filepath"]
                mod = self._load_migration_module(module_path)

                if hasattr(mod, "upgrade"):
                    # Inject connection/session
                    await self._run_migration(session, mod.upgrade, rev["id"], is_upgrade=True)
                    applied_ids.append(rev["id"])

            await session.commit()

        return applied_ids

    async def downgrade(
        self,
        target: Optional[str] = None,
        step: int = 1,
    ) -> List[str]:
        """
        Rollback migrations.

        Args:
            target: Revision ID to roll back to.
            step: Number of steps to roll back (default 1).

        Returns:
            List of reverted revision IDs.
        """
        await self._ensure_tracking_table()

        async with self._connection.session(begin=False) as session:
            applied = await self._get_applied_revisions(session)
            all_revisions = await self._discover_revisions()

            # Build reverse order of applied revisions
            ordered = self._topological_sort(all_revisions)
            applied_ordered = [r for r in ordered if r["id"] in applied]

            if target is not None:
                target_idx = -1
                for idx, r in enumerate(applied_ordered):
                    if r["id"] == target:
                        target_idx = idx
                        break
                if target_idx != -1:
                    to_revert = applied_ordered[target_idx + 1:]
                elif target == "base":
                    to_revert = applied_ordered
                else:
                    to_revert = []
            else:
                to_revert = applied_ordered[-step:] if step > 0 else []

            reverted_ids: List[str] = []
            for rev in reversed(to_revert):
                module_path = rev["filepath"]
                mod = self._load_migration_module(module_path)

                if hasattr(mod, "downgrade"):
                    await self._run_migration(session, mod.downgrade, rev["id"], is_upgrade=False)
                    reverted_ids.append(rev["id"])

            await session.commit()

        return reverted_ids

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def status(self) -> Dict[str, Any]:
        """
        Return the current migration status.

        Returns a dict with ``current``, ``head``, ``applied``, and ``pending``.
        """
        await self._ensure_tracking_table()

        async with self._connection.session(read_only=True) as session:
            applied = await self._get_applied_revisions(session)

        all_revisions = await self._discover_revisions()
        ordered = self._topological_sort(all_revisions)

        pending = [r for r in ordered if r["id"] not in applied]
        current = applied[-1] if applied else None
        head = ordered[-1]["id"] if ordered else None

        return {
            "current": current,
            "head": head,
            "applied_count": len(applied),
            "pending_count": len(pending),
            "applied": applied,
            "pending": [r["id"] for r in pending],
            "is_up_to_date": current == head,
        }

    # ------------------------------------------------------------------
    # Internal: revision helpers
    # ------------------------------------------------------------------

    async def _get_head(self) -> Optional[str]:
        """Get the current head revision ID."""
        await self._ensure_tracking_table()

        async with self._connection.session(read_only=True) as session:
            applied = await self._get_applied_revisions(session)

        all_revisions = await self._discover_revisions()
        ordered = self._topological_sort(all_revisions)

        # Head is the latest revision overall (not just applied)
        if ordered:
            return ordered[-1]["id"]

        # If no revisions exist yet, check what's been applied
        return applied[-1] if applied else None

    async def _ensure_tracking_table(self) -> None:
        """Create the migration tracking table if it doesn't exist."""
        # Use a more cross-compatible DDL (SQLite doesn't like NOW() or TIMESTAMP WITH TIME ZONE)
        is_sqlite = "sqlite" in str(self._connection.url)
        
        timestamp_type = "TIMESTAMP" if not is_sqlite else "DATETIME"
        default_now = "CURRENT_TIMESTAMP"
        
        ddl = f"""
        CREATE TABLE IF NOT EXISTS vorte_migrations (
            revision_id VARCHAR(64) NOT NULL PRIMARY KEY,
            applied_at {timestamp_type} NOT NULL DEFAULT {default_now},
            message TEXT
        );
        """
        await self._connection.execute_raw(ddl)

    async def _get_applied_revisions(self, session: Any) -> List[str]:
        """Return ordered list of applied revision IDs."""
        try:
            result = await session.execute(
                text(
                    "SELECT revision_id FROM vorte_migrations ORDER BY applied_at ASC"
                )
            )
            return [row[0] for row in result.fetchall()]
        except Exception:
            return []

    async def _track_revision(self, session: Any, rev_id: str, message: str = "") -> None:
        """Record a revision as applied."""
        await session.execute(
            text(
                "INSERT INTO vorte_migrations (revision_id, message) "
                "VALUES (:rev_id, :message) ON CONFLICT DO NOTHING"
            ),
            {"rev_id": rev_id, "message": message},
        )

    async def _untrack_revision(self, session: Any, rev_id: str) -> None:
        """Remove a revision from tracking."""
        await session.execute(
            text("DELETE FROM vorte_migrations WHERE revision_id = :rev_id"),
            {"rev_id": rev_id},
        )

    async def _run_migration(self, session: Any, func: Any, rev_id: str, is_upgrade: bool = True) -> None:
        """Run a migration function and track or untrack it."""
        # Check signature: accept connection/conn or use session directly
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())

        if asyncio.iscoroutinefunction(func):
            # For async functions, run them directly on the engine or session connection.
            # (Note: Alembic's 'op' is sync and will not be bound)
            if params and params[0] in ("connection", "conn", "op"):
                await func(self._connection.engine)
            else:
                await func()
        else:
            # For standard sync migrations, run inside run_sync with Operations context bound
            conn = await session.connection()

            def run_sync_migration(sync_conn):
                from alembic.migration import MigrationContext
                from alembic.operations import Operations

                ctx = MigrationContext.configure(sync_conn)
                with Operations.context(ctx):
                    if params and params[0] in ("connection", "conn", "op"):
                        func(sync_conn)
                    else:
                        func()

            await conn.run_sync(run_sync_migration)

        # Track or untrack
        if is_upgrade:
            await self._track_revision(session, rev_id)
        else:
            await self._untrack_revision(session, rev_id)

    # ------------------------------------------------------------------
    # Internal: revision discovery
    # ------------------------------------------------------------------

    async def _discover_revisions(self) -> List[Dict[str, Any]]:
        """Scan the versions directory for migration files."""
        versions_dir = self._migrations_dir / "versions"
        if not versions_dir.exists():
            return []

        revisions: List[Dict[str, Any]] = []
        for py_file in sorted(versions_dir.glob("*.py")):
            content = py_file.read_text(encoding="utf-8")
            rev_match = REVISION_ID_RE.search(content)
            down_match = DOWN_REVISION_RE.search(content)
            msg_match = re.search(r'message\s*=\s*["\'](.+?)["\']', content)

            if rev_match:
                revisions.append(
                    {
                        "id": rev_match.group(1),
                        "down_revision": down_match.group(1).strip() if down_match else None,
                        "message": msg_match.group(1) if msg_match else "",
                        "filepath": str(py_file),
                        "filename": py_file.name,
                    }
                )

        return revisions

    @staticmethod
    def _topological_sort(revisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sort revisions in dependency order (roots first)."""
        by_id = {r["id"]: r for r in revisions}
        visited: set = set()
        result: List[Dict[str, Any]] = []

        def visit(rev_id: str) -> None:
            if rev_id in visited:
                return
            visited.add(rev_id)
            rev = by_id.get(rev_id)
            if rev and rev["down_revision"]:
                visit(rev["down_revision"])
            if rev:
                result.append(rev)

        for r in revisions:
            visit(r["id"])

        return result

    # ------------------------------------------------------------------
    # Internal: schema diff (simplified)
    # ------------------------------------------------------------------

    async def _diff_metadata_vs_db(self) -> List[Dict[str, Any]]:
        """
        Detect schema differences between metadata and database.

        Returns a list of operation dicts describing changes.
        This is a simplified implementation; for production use,
        Alembic's autogenerate is recommended.
        """
        ops: List[Dict[str, Any]] = []

        async with self._connection.session(read_only=True) as session:
            # Get existing tables
            try:
                result = await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
                existing_tables = {row[0] for row in result.fetchall()}
            except Exception:
                existing_tables = set()

        for table_name, table in self._metadata.tables.items():
            bare_name = table_name.split(".")[-1] if "." in table_name else table_name
            if bare_name not in existing_tables:
                ops.append(
                    {
                        "op": "create_table",
                        "table": bare_name,
                        "columns": [
                            {
                                "name": col.name,
                                "type": str(col.type),
                                "primary_key": col.primary_key,
                                "nullable": col.nullable,
                                "unique": col.unique,
                            }
                            for col in table.columns
                        ],
                    }
                )

        return ops

    @staticmethod
    async def _reverse_ops(ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generate reverse (downgrade) operations."""
        reversed_ops: List[Dict[str, Any]] = []
        for op in reversed(ops):
            if op["op"] == "create_table":
                reversed_ops.append(
                    {"op": "drop_table", "table": op["table"]}
                )
        return reversed_ops

    # ------------------------------------------------------------------
    # Internal: file generation
    # ------------------------------------------------------------------

    @staticmethod
    def _make_revision_id(length: int = 12) -> str:
        """Generate a random revision identifier."""
        import secrets
        return secrets.token_hex(length // 2)

    @staticmethod
    def _render_migration_template(
        revision_id: str,
        down_revision: Optional[str],
        message: str,
        upgrade_ops: Optional[List[Dict[str, Any]]] = None,
        downgrade_ops: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Render a migration file template."""
        down_rev = f'"{down_revision}"' if down_revision else "None"

        upgrade_body = "    pass"
        if upgrade_ops:
            lines: List[str] = []
            for op in upgrade_ops:
                if op["op"] == "create_table":
                    lines.append(f'    # Create table: {op["table"]}')
                    lines.append(f'    op.create_table("{op["table"]}"')
                    for col in op["columns"]:
                        nullable = "" if col["nullable"] else ", nullable=False"
                        pk = ", primary_key=True" if col["primary_key"] else ""
                        unique = ", unique=True" if col["unique"] else ""
                        lines.append(
                            f'        sa.Column("{col["name"]}", sa.{_type_to_sa(col["type"])}{nullable}{pk}{unique}),'
                        )
                    lines.append("    )")
            upgrade_body = "\n".join(lines)

        downgrade_body = "    pass"
        if downgrade_ops:
            dlines: List[str] = []
            for op in downgrade_ops:
                if op["op"] == "drop_table":
                    dlines.append(f'    op.drop_table("{op["table"]}")')
            downgrade_body = "\n".join(dlines)

        return f'''"""{message}

Revision ID: {revision_id}
Revises: {down_revision}
Create Date: {datetime.now(timezone.utc).isoformat()}
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = "{revision_id}"
down_revision: Union[str, None] = {down_rev}
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
{upgrade_body}


def downgrade() -> None:
{downgrade_body}
'''

    # ------------------------------------------------------------------
    # Internal: Alembic CLI integration
    # ------------------------------------------------------------------

    async def _alembic_generate(
        self,
        message: str,
        rev_id: str,
        autogenerate: bool = True,
    ) -> str:
        """Try to use Alembic CLI for migration generation."""
        import subprocess

        cmd = [
            "alembic",
            "-c",
            str(self._migrations_dir / "alembic.ini"),
            "revision",
            "--autogenerate" if autogenerate else "",
            "-m",
            message,
        ]
        cmd = [c for c in cmd if c]  # remove empty strings

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(
                f"Alembic failed: {stderr.decode()}"
            )

        # Find the generated file
        versions_dir = self._migrations_dir / "versions"
        latest = max(versions_dir.glob("*.py"), key=lambda p: p.stat().st_mtime)
        return str(latest)

    def _load_migration_module(self, filepath: str) -> Any:
        """Dynamically load a migration module from its file path."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("migration", filepath)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load migration from {filepath}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    # ------------------------------------------------------------------
    # Internal: Alembic scaffolding
    # ------------------------------------------------------------------

    def _write_alembic_ini(self) -> None:
        """Write a minimal alembic.ini."""
        url = self._connection.url
        content = f"""[alembic]
script_location = .
prepend_sys_path = .
sqlalchemy.url = {url}

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""
        (self._migrations_dir / "alembic.ini").write_text(
            content, encoding="utf-8"
        )

    def _write_env_py(self) -> None:
        """Write a minimal env.py for async SQLAlchemy."""
        content = '''"""Alembic environment configuration for async SQLAlchemy."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import your metadata here
# from your_app.models import Base
# target_metadata = Base.metadata
target_metadata = None

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'''
        (self._migrations_dir / "env.py").write_text(
            content, encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _type_to_sa(type_str: str) -> str:
    """Convert a type string back to a SA type reference."""
    mapping = {
        "UUID": "GUID",
        "VARCHAR": "String",
        "INTEGER": "Integer",
        "BIGINT": "BigInteger",
        "FLOAT": "Float",
        "BOOLEAN": "Boolean",
        "TEXT": "Text",
        "DATETIME": "DateTime",
        "TIMESTAMP": "DateTime",
        "JSON": "JSON",
        "JSONB": "JSON",
    }
    for key, val in mapping.items():
        if key.upper() in type_str.upper():
            return val
    return "String"
