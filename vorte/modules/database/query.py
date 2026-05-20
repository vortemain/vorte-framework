"""
Vorte Query Builder
====================
Chainable async query builder for SQLAlchemy 2.0 with helpers for
CRUD, pagination, bulk operations, streaming, and transactions.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    Generic,
    List,
    Optional,
    Sequence,
    Tuple,
    Type,
    TypeVar,
    Union,
)

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, Load, selectinload, joinedload
# SelectBase was removed in SQLAlchemy 2.0; use Select directly for type hints

from vorte.modules.database.connection import ConnectionManager
from vorte.modules.database.model import VorteModel
from vorte.modules.database.pagination import (
    CursorPage,
    CursorPaginator,
    OffsetPage,
    OffsetPaginator,
)
from vorte.modules.database.planner import QueryPlanner

T = TypeVar("T", bound=VorteModel)


class QueryBuilder:
    """
    Chainable async query builder wrapping SQLAlchemy 2.0 select statements.

    Usage::

        # Quick CRUD
        user = await db.find(User, user_id)
        users = await db.find_all(User)
        user = await db.create(User, {"email": "a@b.com", "name": "Alice"})
        user = await db.update(User, user_id, {"name": "Bob"})
        await db.delete(User, user_id)

        # Chainable queries
        users = (
            await db.query(User)
            .where(User.is_active == True)
            .order_by(User.created_at.desc())
            .limit(10)
            .all()
        )

        # Pagination
        page = await db.paginate(User, cursor="...", limit=20)

        # Bulk operations
        await db.bulk_insert([User(email="a@b.com"), User(email="c@d.com")])

        # Transactions
        async with db.transaction():
            await db.create(User, {...})
            await db.create(Post, {...})
    """

    def __init__(self, connection: ConnectionManager, planner: Optional[QueryPlanner] = None):
        self._connection = connection
        self._planner = planner or QueryPlanner()

    # ------------------------------------------------------------------
    # Session shortcut
    # ------------------------------------------------------------------

    async def _get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield a session from the connection manager."""
        async with self._connection.session() as session:
            yield session

    # ------------------------------------------------------------------
    # Quick CRUD
    # ------------------------------------------------------------------

    async def find(self, model: Type[T], id: Any) -> Optional[T]:
        """Fetch a single record by primary key.

        Args:
            model: The model class.
            id: Primary key value.

        Returns:
            The model instance or *None* if not found.
        """
        async with self._connection.session() as session:
            options = self._planner.get_active_options(model)
            result = await session.get(model, id, options=options)
            return result

    async def find_or_fail(self, model: Type[T], id: Any) -> T:
        """Fetch a single record by primary key or raise ``NotFoundError``."""
        record = await self.find(model, id)
        if record is None:
            raise RecordNotFoundError(
                f"{model.__name__} with id={id!r} not found"
            )
        return record

    async def find_all(self, model: Type[T]) -> List[T]:
        """Fetch all records of a model.

        .. warning::
            Use :meth:`stream` or :meth:`paginate` for large tables.

        Args:
            model: The model class.

        Returns:
            List of model instances.
        """
        async with self._connection.session() as session:
            stmt = select(model)
            stmt = self._planner.apply_active(stmt, model)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create(self, model: Type[T], data: Dict[str, Any]) -> T:
        """Create and persist a new record.

        Args:
            model: The model class.
            data: Column-value mapping.

        Returns:
            The newly created model instance (attached to the session).
        """
        async with self._connection.session() as session:
            instance = model(**data)
            session.add(instance)
            await session.flush()
            await session.refresh(instance)
            return instance

    async def update(self, model: Type[T], id: Any, data: Dict[str, Any]) -> Optional[T]:
        """Update a record by primary key.

        Args:
            model: The model class.
            id: Primary key value.
            data: Column-value mapping of fields to update.

        Returns:
            The updated instance or *None* if not found.
        """
        async with self._connection.session() as session:
            instance = await session.get(model, id)
            if instance is None:
                return None
            for key, value in data.items():
                if hasattr(instance, key):
                    setattr(instance, key, value)
            await session.flush()
            await session.refresh(instance)
            return instance

    async def delete(self, model: Type[T], id: Any) -> bool:
        """Delete a record by primary key.

        Args:
            model: The model class.
            id: Primary key value.

        Returns:
            *True* if a row was deleted, *False* otherwise.
        """
        async with self._connection.session() as session:
            instance = await session.get(model, id)
            if instance is None:
                return False
            await session.delete(instance)
            await session.flush()
            return True

    async def count(self, model: Type[T]) -> int:
        """Count all records of a model."""
        async with self._connection.session() as session:
            result = await session.execute(select(func.count()).select_from(model))
            return result.scalar() or 0

    async def exists(self, model: Type[T], **filters: Any) -> bool:
        """Check if any record matches the given filters.

        Usage::

            exists = await db.exists(User, email="a@b.com")
        """
        async with self._connection.session() as session:
            conditions = [getattr(model, k) == v for k, v in filters.items()]
            stmt = select(func.count()).select_from(model).where(*conditions)
            result = await session.execute(stmt)
            return (result.scalar() or 0) > 0

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    async def bulk_insert(
        self,
        records: Sequence[Union[VorteModel, Dict[str, Any]]],
        *,
        model: Optional[Type[T]] = None,
        batch_size: int = 1000,
    ) -> List[T]:
        """Insert multiple records efficiently in batches.

        Args:
            records: Model instances or dictionaries. If dicts, *model* is required.
            model: Required when *records* are dicts.
            batch_size: Number of records per INSERT batch.

        Returns:
            List of inserted instances.
        """
        if not records:
            return []

        async with self._connection.session() as session:
            inserted: List[T] = []
            batch: List[Any] = []

            for record in records:
                if isinstance(record, dict):
                    if model is None:
                        raise ValueError("model is required when records are dicts")
                    instance = model(**record)
                else:
                    instance = record
                session.add(instance)
                batch.append(instance)

                if len(batch) >= batch_size:
                    await session.flush()
                    for inst in batch:
                        await session.refresh(inst)
                    inserted.extend(batch)  # type: ignore[arg-type]
                    batch.clear()

            if batch:
                await session.flush()
                for inst in batch:
                    await session.refresh(inst)
                inserted.extend(batch)  # type: ignore[arg-type]

            return inserted

    async def bulk_update(
        self,
        model: Type[T],
        updates: List[Dict[str, Any]],
        *,
        id_field: str = "id",
        batch_size: int = 500,
    ) -> int:
        """Update multiple records by primary key in batches.

        Args:
            model: The model class.
            updates: List of dicts each containing at least *id_field*.
            id_field: Name of the primary key column (default ``"id"``).
            batch_size: Records processed per flush.

        Returns:
            Total number of updated records.
        """
        if not updates:
            return 0

        count = 0
        async with self._connection.session() as session:
            for batch_start in range(0, len(updates), batch_size):
                batch = updates[batch_start : batch_start + batch_size]
                for row in batch:
                    pk = row.get(id_field)
                    if pk is None:
                        continue
                    instance = await session.get(model, pk)
                    if instance is None:
                        continue
                    for key, value in row.items():
                        if key != id_field and hasattr(instance, key):
                            setattr(instance, key, value)
                    count += 1
                await session.flush()
        return count

    # ------------------------------------------------------------------
    # Transaction
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Context manager providing a transactional session.

        All operations inside the ``async with`` block share a single session.
        The transaction is committed on success and rolled back on exception.

        Usage::

            async with db.transaction() as session:
                user = User(email="a@b.com")
                session.add(user)
                post = Post(title="Hello", author_id=user.id)
                session.add(post)
        """
        async with self._connection.session() as session:
            async with session.begin():
                yield session

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream(
        self,
        model: Type[T],
        *,
        chunk_size: int = 1000,
        stmt: Optional[Any] = None,
        order_by: Optional[Any] = None,
    ) -> AsyncGenerator[List[T], None]:
        """Stream large result sets in fixed-size chunks.

        Each chunk is fetched in a separate session to keep memory usage low.

        Args:
            model: The model class.
            chunk_size: Records per chunk.
            stmt: Optional base select statement.
            order_by: Optional order-by clause (recommended for stable ordering).

        Yields:
            Lists of model instances.
        """
        base = stmt or select(model)
        if order_by is not None:
            base = base.order_by(order_by)

        offset = 0
        while True:
            async with self._connection.session() as session:
                page_stmt = base.offset(offset).limit(chunk_size)
                result = await session.execute(page_stmt)
                rows = list(result.scalars().all())

            if not rows:
                break

            yield rows
            offset += len(rows)

            if len(rows) < chunk_size:
                break

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    async def paginate(
        self,
        model: Type[T],
        *,
        cursor: Optional[str] = None,
        page: Optional[int] = None,
        per_page: int = 20,
        limit: Optional[int] = None,
        order_desc: bool = True,
        cursor_column: Optional[str] = None,
        stmt: Optional[Any] = None,
    ) -> Union[OffsetPage[T], CursorPage[T]]:
        """Paginate query results.

        If *cursor* is provided, uses cursor-based pagination.
        If *page* is provided, uses offset-based pagination.
        If neither is provided, defaults to offset page=1.

        Args:
            model: The model class.
            cursor: Opaque cursor token.
            page: Page number (for offset pagination).
            per_page: Items per page (for offset pagination).
            limit: Items per page (for cursor pagination).
            order_desc: Sort descending (cursor pagination).
            cursor_column: Column for cursoring.
            stmt: Optional base select.

        Returns:
            Either :class:`OffsetPage` or :class:`CursorPage`.
        """
        if cursor is not None:
            actual_limit = limit or per_page
            async with self._connection.session() as session:
                return await CursorPaginator.paginate(
                    session,
                    model,
                    cursor=cursor,
                    limit=actual_limit,
                    cursor_column=cursor_column,
                    order_desc=order_desc,
                    stmt=stmt,
                )
        else:
            actual_page = page or 1
            async with self._connection.session() as session:
                return await OffsetPaginator.paginate(
                    session,
                    model,
                    page=actual_page,
                    per_page=per_page,
                    stmt=stmt,
                )

    # ------------------------------------------------------------------
    # Chainable query
    # ------------------------------------------------------------------

    def query(self, model: Type[T]) -> "ChainableQuery[T]":
        """Start a new chainable query for *model*.

        Usage::

            users = (
                await db.query(User)
                .where(User.is_active == True)
                .order_by(User.created_at.desc())
                .limit(10)
                .all()
            )
        """
        return ChainableQuery(self._connection, model, self._planner)


class ChainableQuery(Generic[T]):
    """
    Fluent query builder for a single model.

    Supports ``where``, ``order_by``, ``limit``, ``offset``, ``join``,
    ``with_`` (eager loading), ``group_by``, ``having``, and ``distinct``.
    Terminates with ``all()``, ``first()``, ``one()``, ``count()``, or
    ``paginate()``.
    """

    def __init__(self, connection: ConnectionManager, model: Type[T], planner: QueryPlanner):
        self._connection = connection
        self._model = model
        self._planner = planner
        self._stmt: Select = select(model)
        self._stmt = self._planner.apply_active(self._stmt, model)
        self._load_options: List[Any] = []
        self._order_clauses: List[Any] = []

    # ---- Filters ----

    def where(self, *conditions: Any) -> "ChainableQuery[T]":
        """Add WHERE conditions (AND'd together).

        Usage::

            .where(User.is_active == True, User.age >= 18)
        """
        self._stmt = self._stmt.where(*conditions)
        return self

    def or_where(self, *conditions: Any) -> "ChainableQuery[T]":
        """Add WHERE conditions (OR'd together).

        Usage::

            .or_where(User.role == "admin", User.role == "super")
        """
        from sqlalchemy import or_
        self._stmt = self._stmt.where(or_(*conditions))
        return self

    # ---- Ordering ----

    def order_by(self, *clauses: Any) -> "ChainableQuery[T]":
        """Add ORDER BY clauses.

        Usage::

            .order_by(User.created_at.desc(), User.name.asc())
        """
        self._stmt = self._stmt.order_by(*clauses)
        self._order_clauses.extend(clauses)
        return self

    # ---- Limit / Offset ----

    def limit(self, limit: int) -> "ChainableQuery[T]":
        """Set the query result limit."""
        self._stmt = self._stmt.limit(limit)
        return self

    def offset(self, offset: int) -> "ChainableQuery[T]":
        """Set the query offset."""
        self._stmt = self._stmt.offset(offset)
        return self

    # ---- Joins ----

    def join(self, target: Any, *on_criteria: Any, **kwargs: Any) -> "ChainableQuery[T]":
        """Add a JOIN clause.

        Usage::

            .join(Post, User.id == Post.author_id)
        """
        self._stmt = self._stmt.join(target, *on_criteria, **kwargs)
        return self

    def outerjoin(self, target: Any, *on_criteria: Any, **kwargs: Any) -> "ChainableQuery[T]":
        """Add a LEFT OUTER JOIN clause."""
        self._stmt = self._stmt.outerjoin(target, *on_criteria, **kwargs)
        return self

    # ---- Eager loading ----

    def with_(self, *relationships: Any) -> "ChainableQuery[T]":
        """Specify relationships to eager-load.

        Uses ``selectinload`` by default for efficiency.

        Usage::

            .with_(User.posts, User.profile)
        """
        for rel in relationships:
            if isinstance(rel, Load):
                self._load_options.append(rel)
            else:
                self._load_options.append(selectinload(rel))
        return self

    def with_joined(self, *relationships: Any) -> "ChainableQuery[T]":
        """Eager-load relationships using ``joinedload`` (single JOIN)."""
        for rel in relationships:
            if isinstance(rel, Load):
                self._load_options.append(rel)
            else:
                self._load_options.append(joinedload(rel))
        return self

    # ---- Grouping ----

    def group_by(self, *columns: Any) -> "ChainableQuery[T]":
        """Add GROUP BY clauses."""
        self._stmt = self._stmt.group_by(*columns)
        return self

    def having(self, *conditions: Any) -> "ChainableQuery[T]":
        """Add HAVING conditions (for grouped queries)."""
        self._stmt = self._stmt.having(*conditions)
        return self

    # ---- Distinct ----

    def distinct(self) -> "ChainableQuery[T]":
        """Apply DISTINCT to the query."""
        self._stmt = self._stmt.distinct()
        return self

    # ---- Locking ----

    def with_for_update(self, *, skip_locked: bool = False, nowait: bool = False) -> "ChainableQuery[T]":
        """Apply ``SELECT ... FOR UPDATE`` row-level locking."""
        kwargs: Dict[str, Any] = {}
        if skip_locked:
            kwargs["skip_locked"] = True
        if nowait:
            kwargs["nowait"] = True
        self._stmt = self._stmt.with_for_update(**kwargs)
        return self

    # ---- Termination methods ----

    async def all(self) -> List[T]:
        """Execute and return all matching records."""
        if self._load_options:
            self._stmt = self._stmt.options(*self._load_options)
        async with self._connection.session() as session:
            result = await session.execute(self._stmt)
            return list(result.scalars().all())

    async def first(self) -> Optional[T]:
        """Execute and return the first matching record, or *None*."""
        self._stmt = self._stmt.limit(1)
        if self._load_options:
            self._stmt = self._stmt.options(*self._load_options)
        async with self._connection.session() as session:
            result = await session.execute(self._stmt)
            return result.scalars().first()

    async def one(self) -> T:
        """Execute and return exactly one record. Raises if 0 or >1."""
        if self._load_options:
            self._stmt = self._stmt.options(*self._load_options)
        async with self._connection.session() as session:
            result = await session.execute(self._stmt)
            return result.scalars().one()

    async def one_or_none(self) -> Optional[T]:
        """Execute and return at most one record."""
        if self._load_options:
            self._stmt = self._stmt.options(*self._load_options)
        async with self._connection.session() as session:
            result = await session.execute(self._stmt)
            return result.scalars().one_or_none()

    async def count(self) -> int:
        """Execute and return the count of matching records."""
        from sqlalchemy import func as sa_func
        count_stmt = select(sa_func.count()).select_from(self._stmt.subquery())
        async with self._connection.session() as session:
            result = await session.execute(count_stmt)
            return result.scalar() or 0

    async def paginate(
        self,
        *,
        page: Optional[int] = None,
        per_page: int = 20,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Union[OffsetPage[T], CursorPage[T]]:
        """Paginate the current query."""
        builder = QueryBuilder(self._connection, self._planner)
        return await builder.paginate(
            self._model,
            page=page,
            per_page=per_page,
            cursor=cursor,
            limit=limit,
            stmt=self._stmt,
        )

    async def exists(self) -> bool:
        """Return True if any row matches the query."""
        from sqlalchemy import exists as sa_exists
        exists_stmt = select(sa_exists(self._stmt.subquery()))
        async with self._connection.session() as session:
            result = await session.execute(exists_stmt)
            return result.scalar() or False

    # ---- Raw access ----

    @property
    def statement(self) -> Select:
        """Access the underlying SQLAlchemy Select statement."""
        return self._stmt


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RecordNotFoundError(Exception):
    """Raised when a record is not found in the database."""

    pass


class BulkOperationError(Exception):
    """Raised when a bulk operation fails."""

    pass
