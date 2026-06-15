import pytest
import os
import shutil
import asyncio
from pathlib import Path
from sqlalchemy import text
from vorte.modules.database.connection import ConnectionManager
from vorte.modules.database.migrations import MigrationManager
from vorte.modules.database.model import Base

@pytest.fixture
def temp_migrations_dir(tmp_path):
    """Fixture that returns a temporary directory for migrations scaffolding."""
    return tmp_path / "migrations"

@pytest.fixture
async def conn_manager(tmp_path):
    """Fixture that initializes a connection manager to a temporary SQLite file."""
    db_file = tmp_path / "test_vorte_migrations.db"
    url = f"sqlite+aiosqlite:///{db_file}"
    conn = ConnectionManager(url=url)
    conn.initialize()
    yield conn
    await conn.close()

@pytest.mark.asyncio
async def test_migration_scaffolding_initialization(conn_manager, temp_migrations_dir):
    """Test that initialize() sets up the directories and config files."""
    mgr = MigrationManager(conn_manager, Base.metadata, migrations_dir=str(temp_migrations_dir))
    mgr.initialize()

    assert temp_migrations_dir.exists()
    assert (temp_migrations_dir / "versions").exists()
    assert (temp_migrations_dir / "alembic.ini").exists()
    assert (temp_migrations_dir / "env.py").exists()

@pytest.mark.asyncio
async def test_migration_generation_and_upgrade_downgrade(conn_manager, temp_migrations_dir):
    """Test generating a template migration, running it, tracking, and rolling it back."""
    mgr = MigrationManager(conn_manager, Base.metadata, migrations_dir=str(temp_migrations_dir))
    mgr.initialize()

    # Generate a migration template
    filepath = await mgr.generate_migration("add_dummy_table", autogenerate=False, revision_id="rev1")
    assert Path(filepath).exists()

    # Verify that the generated file contains revision metadata
    content = Path(filepath).read_text(encoding="utf-8")
    assert 'revision: str = "rev1"' in content
    assert 'down_revision: Union[str, None] = None' in content

    # Let's write custom table creation inside the migration file's upgrade and downgrade
    # We will modify the generated migration template file to execute some operations
    custom_content = """
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "rev1"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Use raw SQL connection check/migration run
    op.create_table(
        "dummy_table",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("name", sa.String(), nullable=True),
    )

def downgrade() -> None:
    op.drop_table("dummy_table")
"""
    Path(filepath).write_text(custom_content, encoding="utf-8")

    # Run status check prior to upgrade
    status_before = await mgr.status()
    assert status_before["current"] is None
    assert status_before["head"] == "rev1"
    assert status_before["applied_count"] == 0
    assert status_before["pending_count"] == 1
    assert status_before["is_up_to_date"] is False

    # Upgrade!
    applied = await mgr.upgrade()
    assert applied == ["rev1"]

    # Verify the table was created and tracking is correct
    async with conn_manager.session() as session:
        # Check tracking
        res_tracking = await session.execute(text("SELECT revision_id FROM vorte_migrations"))
        tracked = [row[0] for row in res_tracking.fetchall()]
        assert tracked == ["rev1"]

        # Check dummy_table exists
        res_dummy = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='dummy_table'"))
        assert res_dummy.scalar() == "dummy_table"

    # Status after upgrade
    status_after = await mgr.status()
    assert status_after["current"] == "rev1"
    assert status_after["is_up_to_date"] is True
    assert status_after["applied"] == ["rev1"]
    assert status_after["pending"] == []

    # Downgrade rollback!
    reverted = await mgr.downgrade(step=1)
    assert reverted == ["rev1"]

    # Verify tracking and table are gone
    async with conn_manager.session() as session:
        # Check tracking
        res_tracking = await session.execute(text("SELECT revision_id FROM vorte_migrations"))
        tracked = [row[0] for row in res_tracking.fetchall()]
        assert tracked == []

        # Check dummy_table is gone
        res_dummy = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='dummy_table'"))
        assert res_dummy.scalar() is None

    # Status after rollback
    status_final = await mgr.status()
    assert status_final["current"] is None
    assert status_final["is_up_to_date"] is False
    assert status_final["applied"] == []
    assert status_final["pending"] == ["rev1"]

@pytest.mark.asyncio
async def test_migration_target_upgrade_and_downgrade(conn_manager, temp_migrations_dir):
    """Test upgrading up to a target revision, and downgrading back to a target revision."""
    mgr = MigrationManager(conn_manager, Base.metadata, migrations_dir=str(temp_migrations_dir))
    mgr.initialize()

    # Generate 3 migrations: rev1 -> rev2 -> rev3
    p1 = await mgr.generate_migration("m1", autogenerate=False, revision_id="rev1")
    p2 = await mgr.generate_migration("m2", autogenerate=False, revision_id="rev2")
    p3 = await mgr.generate_migration("m3", autogenerate=False, revision_id="rev3")

    # Override files with dummy upgrade/downgrade implementations
    Path(p1).write_text("revision = 'rev1'\ndown_revision = None\ndef upgrade(): pass\ndef downgrade(): pass", "utf-8")
    Path(p2).write_text("revision = 'rev2'\ndown_revision = 'rev1'\ndef upgrade(): pass\ndef downgrade(): pass", "utf-8")
    Path(p3).write_text("revision = 'rev3'\ndown_revision = 'rev2'\ndef upgrade(): pass\ndef downgrade(): pass", "utf-8")

    # Upgrade to target = rev2 (only rev1 and rev2 should be run)
    applied = await mgr.upgrade(target="rev2")
    assert applied == ["rev1", "rev2"]

    status = await mgr.status()
    assert status["current"] == "rev2"
    assert status["applied"] == ["rev1", "rev2"]
    assert status["pending"] == ["rev3"]

    # Upgrade the rest to head
    applied_rem = await mgr.upgrade()
    assert applied_rem == ["rev3"]

    # Downgrade back to target = rev1 (rev3 and rev2 should be reverted)
    reverted = await mgr.downgrade(target="rev1")
    assert reverted == ["rev3", "rev2"]

    status_down = await mgr.status()
    assert status_down["current"] == "rev1"
    assert status_down["applied"] == ["rev1"]
    assert status_down["pending"] == ["rev2", "rev3"]

    # Downgrade to base
    reverted_base = await mgr.downgrade(target="base")
    assert reverted_base == ["rev1"]

    status_base = await mgr.status()
    assert status_base["current"] is None
    assert status_base["applied"] == []
