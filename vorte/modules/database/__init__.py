"""
Vorte Database Module
======================
Async-native database layer built on SQLAlchemy 2.0 with auto-migrations,
chainable query builder, cursor/offset pagination, ORM base model, and
database seeders.

Quick start::

    from vorte.modules.database import DatabaseModule, VorteModel

    app = Vorte()
    app.register(DatabaseModule(url="postgresql+asyncpg://localhost/mydb"))

    # Define models
    class User(VorteModel):
        __tablename__ = "users"
        email = StringField(unique=True, index=True)
        name = StringField(max_length=255)

    # Query
    user = await db_module.find(User, user_id)
    users = await db_module.query_model(User).where(User.name == "Alice").all()
    page = await db_module.query.paginate(User, cursor="...", limit=20)
"""

from vorte.modules.database.connection import ConnectionManager
from vorte.modules.database.migrations import MigrationManager
from vorte.modules.database.model import (
    Base,
    BooleanField,
    DateTimeField,
    Field,
    ForeignKeyField,
    FloatField,
    GUID,
    IntegerField,
    JSONField,
    StringField,
    TimestampMixin,
    UUIDField,
    VorteModel,
)
from vorte.modules.database.module import DatabaseModule
from vorte.modules.database.pagination import (
    CursorPage,
    CursorPaginator,
    OffsetPage,
    OffsetPaginator,
)
from vorte.modules.database.query import (
    BulkOperationError,
    ChainableQuery,
    QueryBuilder,
    RecordNotFoundError,
)
from vorte.modules.database.seeders import BaseSeeder, SeederManager
from vorte.modules.database.performance import performance_mode, PreparedSQLManager

__all__ = [
    # Module
    "DatabaseModule",
    # Connection
    "ConnectionManager",
    # ORM model
    "Base",
    "VorteModel",
    "TimestampMixin",
    "GUID",
    # Field helpers
    "Field",
    "UUIDField",
    "StringField",
    "IntegerField",
    "FloatField",
    "BooleanField",
    "DateTimeField",
    "JSONField",
    "ForeignKeyField",
    # Query builder
    "QueryBuilder",
    "ChainableQuery",
    "RecordNotFoundError",
    "BulkOperationError",
    # Pagination
    "OffsetPage",
    "OffsetPaginator",
    "CursorPage",
    "CursorPaginator",
    # Migrations
    "MigrationManager",
    # Seeders
    "BaseSeeder",
    "SeederManager",
    # Performance Mode
    "performance_mode",
    "PreparedSQLManager",
]
