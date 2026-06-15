"""
Vorte ORM Model Base
=====================
Declarative base model with UUID primary keys, automatic timestamps,
and a :func:`Field` helper for column definitions with constraints.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple, Type, TypeVar, Union

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    declarative_mixin,
    mapped_column,
    relationship,
)
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

# Re-export common SA types for user convenience
from sqlalchemy import JSON as JSONType  # noqa: F401

T = TypeVar("T", bound="VorteModel")

# ---------------------------------------------------------------------------
# UUID storage adapter (works across PostgreSQL, SQLite, MySQL)
# ---------------------------------------------------------------------------

class GUID(TypeDecorator):
    """Platform-independent UUID type.

    Uses PostgreSQL's native UUID on Postgres, and CHAR(32) elsewhere.
    """

    impl = String(32)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID())
        return dialect.type_descriptor(String(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value).replace("-", "") if isinstance(value, uuid.UUID) else str(value).replace("-", "")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


# ---------------------------------------------------------------------------
# Timestamp mixin
# ---------------------------------------------------------------------------

@declarative_mixin
class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# Field helper
# ---------------------------------------------------------------------------

class Field:
    """
    Declarative field descriptor for Vorte models.

    Produces a :class:`mapped_column` with the appropriate SQLAlchemy type,
    constraints, and defaults.

    Usage::

        class User(VorteModel):
            email = Field(String, unique=True, index=True)
            name = Field(String, max_length=255, nullable=False)
            age = Field(Integer, default=0)
            is_active = Field(Boolean, default=True)
            metadata_ = Field(JSON, default=dict)
    """

    def __init__(
        self,
        field_type: Type = String,
        *,
        primary_key: bool = False,
        nullable: Optional[bool] = None,
        default: Any = None,
        default_factory: Optional[Callable[[], Any]] = None,
        unique: bool = False,
        index: bool = False,
        max_length: Optional[int] = None,
        foreign_key: Optional[str] = None,
        ondelete: Optional[str] = None,
        onupdate: Optional[str] = None,
        server_default: Optional[Any] = None,
        comment: Optional[str] = None,
        doc: Optional[str] = None,
        **kwargs: Any,
    ):
        self.field_type = field_type
        self.primary_key = primary_key
        self.nullable = nullable
        self.default = default
        self.default_factory = default_factory
        self.unique = unique
        self.index = index
        self.max_length = max_length
        self.foreign_key = foreign_key
        self.ondelete = ondelete
        self.onupdate = onupdate
        self.server_default = server_default
        self.comment = comment
        self.doc = doc
        self.extra_kwargs = kwargs

    def to_column(self) -> "Column[Any]":
        """Convert this Field descriptor into a SQLAlchemy Column."""
        col_type = self._resolve_type()

        # Resolve default
        col_default = self.default
        if self.default_factory is not None:
            col_default = self.default_factory

        kwargs: Dict[str, Any] = {
            "type_": col_type,
            "primary_key": self.primary_key,
            "unique": self.unique,
            "index": self.index,
            "default": col_default,
            "server_default": self.server_default,
            "comment": self.doc or self.comment,
            **self.extra_kwargs,
        }

        # Nullable: default False for primary_key, None otherwise lets SA decide
        if self.nullable is not None:
            kwargs["nullable"] = self.nullable
        elif not self.primary_key and col_default is None and self.server_default is None:
            kwargs["nullable"] = True

        # Foreign key
        if self.foreign_key:
            fk_kwargs: Dict[str, str] = {}
            if self.ondelete:
                fk_kwargs["ondelete"] = self.ondelete
            if self.onupdate:
                fk_kwargs["onupdate"] = self.onupdate
            kwargs["foreign_key"] = ForeignKey(self.foreign_key, **fk_kwargs)

        return Column(**kwargs)

    def _resolve_type(self) -> Any:
        """Resolve the Python type hint into a SQLAlchemy column type."""
        if self.field_type is str:
            length = self.max_length or 255
            return String(length)
        if self.field_type is int:
            return Integer
        if self.field_type is float:
            return Float
        if self.field_type is bool:
            return Boolean
        if self.field_type is dict or self.field_type is JSON:
            return JSON
        if self.field_type is list:
            return JSON
        if self.field_type is datetime:
            return DateTime(timezone=True)
        if self.field_type is uuid.UUID:
            return GUID
        # Allow passing a SA type directly (e.g., String(500))
        if isinstance(self.field_type, type) and issubclass(
            self.field_type, (TypeDecorator, Column)
        ):
            return self.field_type
        # If user passed a SA type instance like String(500)
        if hasattr(self.field_type, "__class__") and getattr(
            self.field_type.__class__, "__name__", ""
        ) in (
            "String",
            "Integer",
            "Float",
            "Boolean",
            "DateTime",
            "JSON",
            "Text",
            "UUID",
            "JSONB",
        ):
            return self.field_type
        return self.field_type

    def _python_type(self) -> type:
        """Return the Python type for use in ``Mapped[...]`` annotations."""
        _map = {
            str: str,
            int: int,
            float: float,
            bool: bool,
            datetime: datetime,
            uuid.UUID: uuid.UUID,
            dict: dict,
            list: list,
        }
        result = _map.get(self.field_type)
        if result is not None:
            return result
        # For JSON/JSONB/Text etc. fall back to Any
        return Any  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Convenience shorthands
# ---------------------------------------------------------------------------

def UUIDField(
    *,
    primary_key: bool = False,
    nullable: bool = False,
    default: Any = None,
    **kwargs: Any,
) -> Field:
    """Create a UUID-typed field."""
    if default is None and primary_key:
        default = uuid.uuid4
    return Field(
        uuid.UUID,
        primary_key=primary_key,
        nullable=nullable,
        default=default,
        **kwargs,
    )


def StringField(
    *,
    max_length: int = 255,
    nullable: Optional[bool] = None,
    default: Any = None,
    unique: bool = False,
    index: bool = False,
    **kwargs: Any,
) -> Field:
    """Create a string-typed field."""
    return Field(
        str,
        max_length=max_length,
        nullable=nullable,
        default=default,
        unique=unique,
        index=index,
        **kwargs,
    )


def IntegerField(
    *,
    nullable: Optional[bool] = None,
    default: Any = None,
    **kwargs: Any,
) -> Field:
    """Create an integer-typed field."""
    return Field(int, nullable=nullable, default=default, **kwargs)


def FloatField(
    *,
    nullable: Optional[bool] = None,
    default: Any = None,
    **kwargs: Any,
) -> Field:
    """Create a float-typed field."""
    return Field(float, nullable=nullable, default=default, **kwargs)


def BooleanField(
    *,
    default: bool = False,
    nullable: Optional[bool] = None,
    **kwargs: Any,
) -> Field:
    """Create a boolean-typed field."""
    return Field(bool, default=default, nullable=nullable, **kwargs)


def DateTimeField(
    *,
    nullable: Optional[bool] = None,
    default: Any = None,
    server_default: Any = None,
    **kwargs: Any,
) -> Field:
    """Create a datetime-typed field."""
    return Field(
        datetime,
        nullable=nullable,
        default=default,
        server_default=server_default,
        **kwargs,
    )


def JSONField(
    *,
    nullable: Optional[bool] = None,
    default: Any = None,
    server_default: Any = None,
    **kwargs: Any,
) -> Field:
    """Create a JSON-typed field."""
    return Field(
        JSON,
        nullable=nullable,
        default=default,
        server_default=server_default,
        **kwargs,
    )


def ForeignKeyField(
    target_model: str,
    *,
    nullable: Optional[bool] = None,
    ondelete: Optional[str] = None,
    onupdate: Optional[str] = None,
    **kwargs: Any,
) -> Field:
    """Create a foreign-key field."""
    return Field(
        uuid.UUID,
        foreign_key=target_model,
        nullable=nullable,
        ondelete=ondelete,
        onupdate=onupdate,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base for all Vorte models."""

    type_annotation_map = {
        dict: JSON,
        # JSONB is Postgres-only; JSON is used as the portable fallback.
    }

    # Allow models to define a custom __tablename__ prefix
    class Config:
        arbitrary_types_allowed = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Convert ``Field`` descriptor instances into ``mapped_column`` entries.

        This hook fires *before* SQLAlchemy's own declarative processing so
        that our helper fields are already proper ``Column`` objects by the
        time SA introspects the class namespace.
        """
        # Scan cls.__dict__ (not inherited attrs) for Field instances
        field_names = [
            attr
            for attr, val in cls.__dict__.items()
            if isinstance(val, Field) and not attr.startswith("_")
        ]
        for attr in field_names:
            field_obj: Field = cls.__dict__[attr]
            col = field_obj.to_column()
            # Replace the Field descriptor with a proper mapped_column
            # by annotating and setting the column on the class.
            mc_kwargs: Dict[str, Any] = {
                k: getattr(col, k)
                for k in (
                    "primary_key", "nullable", "unique", "index",
                    "server_default", "comment",
                )
                if getattr(col, k, None) is not None
            }
            if col.default is not None:
                mc_kwargs["default"] = col.default.arg if hasattr(col.default, "arg") else col.default
            setattr(cls, attr, mapped_column(col.type, *col.foreign_keys, **mc_kwargs))
            # Inject a Mapped[...] annotation so SA 2.0 declarative form accepts it
            if "__annotations__" not in cls.__dict__:
                cls.__annotations__ = {}
            python_type = field_obj._python_type()
            # SA 2.0 requires Mapped[T] or Mapped[Optional[T]] annotations
            nullable = field_obj.nullable
            if nullable is None:
                # Default: nullable if no default and not primary_key
                nullable = (
                    field_obj.default is None
                    and field_obj.default_factory is None
                    and field_obj.server_default is None
                    and not field_obj.primary_key
                )
            if nullable:
                cls.__annotations__.setdefault(attr, Mapped[Optional[python_type]])  # type: ignore[valid-type]
            else:
                cls.__annotations__.setdefault(attr, Mapped[python_type])  # type: ignore[valid-type]
        super().__init_subclass__(**kwargs)



# ---------------------------------------------------------------------------
# VorteModel
# ---------------------------------------------------------------------------

class VorteModel(Base, TimestampMixin):
    """
    Abstract base model for all Vorte database entities.

    Every model automatically receives:
    - ``id``: UUID primary key (auto-generated)
    - ``created_at``: timezone-aware timestamp, set on insert
    - ``updated_at``: timezone-aware timestamp, set on insert and on every update

    Usage::

        class User(VorteModel):
            __tablename__ = "users"

            email = StringField(unique=True, index=True)
            name = StringField(max_length=255, nullable=False)
            is_active = BooleanField(default=True)
            settings = JSONField(default=dict)

            posts = relationship("Post", back_populates="author")

        class Post(VorteModel):
            __tablename__ = "posts"

            title = StringField(max_length=500)
            body = Field(Text)
            author_id = ForeignKeyField("users.id", ondelete="CASCADE")
            author = relationship("User", back_populates="posts")
    """

    __abstract__ = True

    id: Mapped[str] = mapped_column(
        GUID,
        primary_key=True,
        default=uuid.uuid4,
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the model instance to a plain dictionary."""
        result: Dict[str, Any] = {}
        for column in self.__table__.columns:  # type: ignore[attr-defined]
            value = getattr(self, column.name, None)
            if isinstance(value, uuid.UUID):
                value = str(value)
            elif isinstance(value, datetime):
                value = value.isoformat()
            result[column.name] = value
        return result

    @classmethod
    def from_dict(cls: Type[T], data: Dict[str, Any]) -> T:
        """Create an instance from a dictionary (no persistence)."""
        # Filter out keys that aren't actual columns
        valid_keys = {c.name for c in cls.__table__.columns}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    def __repr__(self) -> str:
        pk = getattr(self, "id", None)
        return f"<{self.__class__.__name__} id={pk!r}>"

    @classmethod
    def query(cls: Type[T]) -> Any:
        """Get a chainable QueryBuilder for this model."""
        from vorte.core.di import _global_container
        from vorte.modules.database.query import QueryBuilder
        try:
            qb = _global_container.resolve(QueryBuilder)
            return qb.query(cls)
        except Exception:
            raise RuntimeError(
                "DatabaseModule is not registered or initialized. "
                "Ensure DatabaseModule is registered on the app."
            )

    @classmethod
    async def find(cls: Type[T], id: Any) -> Optional[T]:
        """Find a model record by primary key."""
        from vorte.core.di import _global_container
        from vorte.modules.database.query import QueryBuilder
        qb = _global_container.resolve(QueryBuilder)
        return await qb.find(cls, id)

    @classmethod
    async def find_or_fail(cls: Type[T], id: Any) -> T:
        """Find a model record by primary key or raise RecordNotFoundError."""
        from vorte.core.di import _global_container
        from vorte.modules.database.query import QueryBuilder
        qb = _global_container.resolve(QueryBuilder)
        return await qb.find_or_fail(cls, id)

    @classmethod
    async def find_all(cls: Type[T]) -> List[T]:
        """Find all records for this model."""
        from vorte.core.di import _global_container
        from vorte.modules.database.query import QueryBuilder
        qb = _global_container.resolve(QueryBuilder)
        return await qb.find_all(cls)

    @classmethod
    async def create(cls: Type[T], data: Dict[str, Any]) -> T:
        """Create and persist a new model record."""
        from vorte.core.di import _global_container
        from vorte.modules.database.query import QueryBuilder
        qb = _global_container.resolve(QueryBuilder)
        return await qb.create(cls, data)

    @classmethod
    async def exists(cls: Type[T], **filters: Any) -> bool:
        """Check if a record matching the filters exists."""
        from vorte.core.di import _global_container
        from vorte.modules.database.query import QueryBuilder
        qb = _global_container.resolve(QueryBuilder)
        return await qb.exists(cls, **filters)

    @classmethod
    async def count(cls: Type[T]) -> int:
        """Count the total number of records for this model."""
        from vorte.core.di import _global_container
        from vorte.modules.database.query import QueryBuilder
        qb = _global_container.resolve(QueryBuilder)
        return await qb.count(cls)

    async def save(self) -> None:
        """Save (insert or update) the current model instance to the database.

        If ``self.id`` is ``None``, a new UUID is auto-generated so the
        instance can be located after insertion.
        """
        from vorte.core.di import _global_container
        from vorte.modules.database.query import QueryBuilder
        qb = _global_container.resolve(QueryBuilder)
        # Auto-generate ID for brand new instances
        if self.id is None:
            self.id = uuid.uuid4()  # type: ignore[assignment]
        is_existing = await qb.exists(self.__class__, id=self.id)
        if is_existing:
            data = {
                c.name: getattr(self, c.name)
                for c in self.__table__.columns  # type: ignore[attr-defined]
                if c.name != "id"
            }
            # Remove None values to avoid overwriting server-managed columns
            # (e.g. created_at/updated_at set by the DB on insert)
            data = {k: v for k, v in data.items() if v is not None}
            await qb.update(self.__class__, self.id, data)
        else:
            data = self.to_dict()
            await qb.create(self.__class__, data)

    async def delete(self) -> bool:
        """Delete the current model instance from the database."""
        from vorte.core.di import _global_container
        from vorte.modules.database.query import QueryBuilder
        qb = _global_container.resolve(QueryBuilder)
        return await qb.delete(self.__class__, self.id)

