import pytest
from sqlalchemy import text
from vorte import Vorte, DatabaseModule
from vorte.modules.database.model import VorteModel, StringField

class TaskItem(VorteModel):
    __tablename__ = "task_items"
    name = StringField(nullable=False)


@pytest.fixture
async def app_and_db(tmp_path):
    app = Vorte(auto_load=False)
    db_file = tmp_path / "test_active_record.db"
    db = DatabaseModule(url=f"sqlite+aiosqlite:///{db_file}", auto_create_tables=False)
    app.register(db)
    
    # Materialize DI container bindings so query resolution works
    await app.container.abuild()
    
    # Create tables
    async with db.connection.engine.begin() as conn:
        await conn.run_sync(VorteModel.metadata.create_all)
        
    yield app
    
    await db.on_shutdown()


@pytest.mark.asyncio
async def test_active_record_operations(app_and_db):
    # Test Classmethod: create
    t1 = await TaskItem.create({"name": "Buy groceries"})
    assert t1.id is not None
    assert t1.name == "Buy groceries"

    # Test Classmethod: find
    found = await TaskItem.find(t1.id)
    assert found is not None
    assert found.name == "Buy groceries"

    # Test Classmethod: find_or_fail
    found_fail = await TaskItem.find_or_fail(t1.id)
    assert found_fail.name == "Buy groceries"

    # Test Classmethod: exists
    exists = await TaskItem.exists(name="Buy groceries")
    assert exists is True
    
    not_exists = await TaskItem.exists(name="Fly to mars")
    assert not_exists is False

    # Test Classmethod: count
    cnt = await TaskItem.count()
    assert cnt == 1

    # Test Instance method: save (Insert)
    t2 = TaskItem(name="Wash dishes")
    await t2.save()
    assert await TaskItem.count() == 2

    # Test Instance method: save (Update)
    t2.name = "Wash dishes thoroughly"
    await t2.save()
    
    refreshed = await TaskItem.find(t2.id)
    assert refreshed.name == "Wash dishes thoroughly"

    # Test Classmethod: query / chainable
    records = await TaskItem.query().where(TaskItem.name.like("%Wash%")).all()
    assert len(records) == 1
    assert records[0].id == t2.id

    # Test Classmethod: find_all
    all_tasks = await TaskItem.find_all()
    assert len(all_tasks) == 2

    # Test Instance method: delete
    deleted = await t1.delete()
    assert deleted is True
    assert await TaskItem.count() == 1
