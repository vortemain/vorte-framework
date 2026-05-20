"""
Vorte N+1 Query Planner
========================
Look-ahead query optimization: detects and prevents the N+1 query problem by
tracking query counts per request and grouping nested relation loads into
atomic batch executions.

Blueprint reference: §5.3 Look-Ahead Query Optimization
  "VORTE scans route response payloads to predict model dependency requirements,
   grouping nested database execution blocks into an atomic query execution to
   eradicate the N+1 problem."
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import warnings
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Set, Tuple, Type, TypeVar

logger = logging.getLogger("vorte.query_planner")

F = TypeVar("F", bound=Callable)

active_relations: contextvars.ContextVar[Tuple[str, ...]] = contextvars.ContextVar(
    "active_relations", default=()
)

active_detector: contextvars.ContextVar[Optional[N1Detector]] = contextvars.ContextVar(
    "active_detector", default=None
)

try:
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        detector = active_detector.get()
        if detector is not None:
            detector.record(statement)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# N+1 Detector
# ---------------------------------------------------------------------------

class N1Detector:
    """
    Async context manager that counts SQLAlchemy-style query emissions per
    request and warns when the count exceeds a configurable threshold.

    Usage::

        async with N1Detector(threshold=5) as detector:
            users = await db.find_all(User)
            for user in users:
                posts = await db.find_all(Post)  # N+1!
        # Warning logged if > threshold queries detected
    """

    def __init__(self, threshold: int = 5, raise_on_exceed: bool = False) -> None:
        self.threshold = threshold
        self.raise_on_exceed = raise_on_exceed
        self._query_count: int = 0
        self._queries: List[str] = []
        self._token = None

    def record(self, sql: str = "") -> None:
        """Record one query emission. Call this from your DB layer / middleware."""
        self._query_count += 1
        if sql:
            self._queries.append(sql)

    @property
    def query_count(self) -> int:
        return self._query_count

    @property
    def queries(self) -> List[str]:
        return list(self._queries)

    async def __aenter__(self) -> "N1Detector":
        self._query_count = 0
        self._queries = []
        self._token = active_detector.set(self)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            active_detector.reset(self._token)
        if self._query_count > self.threshold:
            message = (
                f"N+1 query warning: {self._query_count} queries detected in this request "
                f"(threshold: {self.threshold}). Consider using @select_related() to batch loads."
            )
            if self.raise_on_exceed:
                raise RuntimeError(message)
            logger.warning(message)


# ---------------------------------------------------------------------------
# @select_related decorator
# ---------------------------------------------------------------------------

class _RelationGroup:
    """Holds pending relation keys to be bulk-fetched in a single query."""

    def __init__(self, relations: Tuple[str, ...]) -> None:
        self.relations = relations
        self._pending_ids: Set[Any] = set()

    def queue(self, id: Any) -> None:
        self._pending_ids.add(id)

    @property
    def pending(self) -> Set[Any]:
        return set(self._pending_ids)

    def reset(self) -> None:
        self._pending_ids.clear()


def select_related(*relations: str) -> Callable[[F], F]:
    """
    Route decorator that declares which related models should be loaded in a
    single batched query rather than one query per parent row.

    The declared *relations* are attached as metadata on the handler function.
    The ``QueryPlanner`` (or a DB middleware) reads this metadata at request
    time and groups all relation loads into a single ``IN`` query, eradicating
    N+1 patterns.

    Usage::

        @select_related("posts", "profile")
        @app.get("/users")
        async def list_users():
            users = await db.query(User).with_(User.posts, User.profile).all()
            return users

    Blueprint reference: §5.3 — Look-Ahead Query Optimization
    """
    def decorator(func: F) -> F:
        existing: Tuple[str, ...] = getattr(func, "_vorte_relations", ())
        func._vorte_relations = existing + relations  # type: ignore[attr-defined]
        func._vorte_select_related = True  # type: ignore[attr-defined]

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            return func(*args, **kwargs)

        wrapper._vorte_relations = func._vorte_relations  # type: ignore[attr-defined]
        wrapper._vorte_select_related = True  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# QueryPlanner
# ---------------------------------------------------------------------------

class QueryPlanner:
    """
    Request-scoped query planner that reads ``@select_related`` metadata from
    route handlers and pre-fetches the declared relations in a single batched
    ``IN`` query.

    Used by the DatabaseModule to intercept query execution and apply
    look-ahead optimization automatically.

    Usage::

        planner = QueryPlanner()

        # Inspect a handler for declared relations
        relations = planner.get_relations(handler)

        # Build a batched selectinload statement
        stmt = planner.apply(select(User), User, relations)
    """

    def __init__(self) -> None:
        self._stats: Dict[str, int] = {}

    def get_relations(self, handler: Callable) -> Tuple[str, ...]:
        """Return the ``@select_related`` relations declared on *handler*."""
        return getattr(handler, "_vorte_relations", ())

    def has_select_related(self, handler: Callable) -> bool:
        """Return ``True`` if *handler* has ``@select_related`` applied."""
        return bool(getattr(handler, "_vorte_select_related", False))

    def apply_active(self, stmt: Any, model: Any) -> Any:
        """Apply the currently active relations from the context var to the stmt."""
        relations = active_relations.get()
        if not relations:
            return stmt
        return self.apply(stmt, model, relations)

    def get_active_options(self, model: Any) -> List[Any]:
        """Return the active selectinload options for the current context."""
        relations = active_relations.get()
        if not relations:
            return []
        options = []
        try:
            from sqlalchemy.orm import selectinload
            from sqlalchemy.orm.relationships import RelationshipProperty
        except ImportError:
            return []

        for rel in relations:
            parts = rel.split(".")
            current_model = model
            current_option = None
            valid = True
            
            for part in parts:
                attr = getattr(current_model, part, None)
                if attr is not None and hasattr(attr, "property") and isinstance(attr.property, RelationshipProperty):
                    if current_option is None:
                        current_option = selectinload(attr)
                    else:
                        current_option = current_option.selectinload(attr)
                    current_model = attr.property.mapper.class_
                else:
                    valid = False
                    break
            
            if valid and current_option is not None:
                options.append(current_option)
        return options

    def apply(self, stmt: Any, model: Any, relations: Tuple[str, ...]) -> Any:
        """
        Apply SQLAlchemy ``selectinload`` options for *relations* to *stmt*.

        Falls back gracefully if a relation name doesn't exist on the model.
        """
        try:
            from sqlalchemy.orm import selectinload
            from sqlalchemy.orm.relationships import RelationshipProperty
        except ImportError:
            return stmt

        for rel in relations:
            parts = rel.split(".")
            current_model = model
            current_option = None
            valid = True
            
            for part in parts:
                attr = getattr(current_model, part, None)
                if attr is not None and hasattr(attr, "property") and isinstance(attr.property, RelationshipProperty):
                    if current_option is None:
                        current_option = selectinload(attr)
                    else:
                        current_option = current_option.selectinload(attr)
                    current_model = attr.property.mapper.class_
                else:
                    valid = False
                    break
            
            if valid and current_option is not None:
                stmt = stmt.options(current_option)
                self._stats[rel] = self._stats.get(rel, 0) + 1
            else:
                logger.debug(
                    "select_related: relation '%s' not found on %s, skipping.",
                    rel,
                    getattr(model, "__name__", model),
                )
        return stmt


    @property
    def stats(self) -> Dict[str, int]:
        """Count of how many times each relation was batch-loaded."""
        return dict(self._stats)

    def reset_stats(self) -> None:
        """Reset accumulated stats (useful between test runs)."""
        self._stats.clear()
