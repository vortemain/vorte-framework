"""
Vorte Database Performance Mode
================================
Eradicates ORM object instantiation and Pydantic serialization on read-heavy,
deeply relational hot paths.

Provides:
- `@performance_mode` route decorator.
- `PreparedSQLManager` compiling SQLAlchemy to PostgreSQL-native JSON aggregations.
- Hybrid zero-copy stream responses.
"""

from __future__ import annotations

import functools
import inspect
import time
from typing import Any, Dict, List, Optional, Type, TypeVar, Callable

try:
    from vorte._vorte_engine import MetricsCollector
    _metrics = MetricsCollector()
except ImportError:
    _metrics = None

from fastapi import Request, Response
from sqlalchemy import text
from sqlalchemy.orm import class_mapper, Session
from sqlalchemy.orm.relationships import RelationshipProperty

from vorte.core.serializer import FastSerializer
from vorte.core.response import VorteStreamResponse


class PreparedSQLManager:
    """
    Compiles and caches raw SQL statements (including postgres json_agg constructs)
    to enable zero-overhead database executions.
    """
    _cache: Dict[tuple, str] = {}

    @classmethod
    def get_compiled_query(cls, db_url: str, model: Any, relations: List[str]) -> str:
        """Get or compile a native SQL query for the given model and relations."""
        key = (db_url, model.__name__, tuple(sorted(relations or ())))
        if key not in cls._cache:
            cls._cache[key] = compile_postgres_query(model, relations)
        return cls._cache[key]


def build_postgres_json_sql(model: Any, relation_tree: dict, table_alias: str = "t") -> str:
    """Recursively builds a PostgreSQL json_build_object string for model columns and relations."""
    mapper = class_mapper(model)
    fields = []
    
    # 1. Build column lists
    for col in mapper.columns:
        fields.append(f"'{col.key}', {table_alias}.{col.name}")
        
    # 2. Add nested relations
    for rel_name, sub_tree in relation_tree.items():
        if rel_name not in mapper.relationships:
            continue
        rel_prop = mapper.relationships[rel_name]
        target_model = rel_prop.mapper.class_
        target_table = target_model.__tablename__
        sub_alias = f"sub_{rel_name}"
        
        # Determine join condition columns
        local_col, remote_col = rel_prop.local_remote_pairs[0]
        join_cond = f"{sub_alias}.{remote_col.name} = {table_alias}.{local_col.name}"
        
        # Recurse
        sub_sql = build_postgres_json_sql(target_model, sub_tree, sub_alias)
        
        if rel_prop.uselist:
            subquery = f"coalesce((SELECT json_agg({sub_sql}) FROM {target_table} {sub_alias} WHERE {join_cond}), '[]'::json)"
        else:
            subquery = f"(SELECT {sub_sql} FROM {target_table} {sub_alias} WHERE {join_cond} LIMIT 1)"
            
        fields.append(f"'{rel_name}', {subquery}")
        
    return f"json_build_object({', '.join(fields)})"


def compile_postgres_query(model: Any, relations: List[str]) -> str:
    """Compiles a complete SELECT statement with json_agg and nested structures."""
    relation_tree: dict = {}
    for rel in relations or []:
        parts = rel.split(".")
        curr = relation_tree
        for part in parts:
            curr = curr.setdefault(part, {})
            
    json_obj_sql = build_postgres_json_sql(model, relation_tree, "t")
    table_name = model.__tablename__
    return f"SELECT coalesce(json_agg({json_obj_sql}), '[]'::json) FROM {table_name} t;"


async def fetch_and_stitch(db_module: Any, model: Any, relations_list: List[str]) -> List[dict]:
    """
    Performs single-query-per-table fetching and recursive in-memory stitching.
    Used as an ultra-fast fallback on SQLite or other non-PostgreSQL databases.
    """
    relation_tree: dict = {}
    for rel in relations_list or []:
        parts = rel.split(".")
        curr = relation_tree
        for part in parts:
            curr = curr.setdefault(part, {})

    def get_cols(m):
        return [col.name for col in class_mapper(m).columns]

    async def fetch_node(m, tree, session):
        table_name = m.__tablename__
        cols = get_cols(m)
        query_str = f"SELECT {', '.join(cols)} FROM {table_name}"
        result = await session.execute(text(query_str))
        
        rows = [dict(zip(cols, row)) for row in result.fetchall()]
        
        for rel_name, sub_tree in tree.items():
            mapper = class_mapper(m)
            if rel_name not in mapper.relationships:
                continue
            rel_prop = mapper.relationships[rel_name]
            target_model = rel_prop.mapper.class_
            local_col, remote_col = rel_prop.local_remote_pairs[0]
            
            target_rows = await fetch_node(target_model, sub_tree, session)
            
            grouped: dict = {}
            for tr in target_rows:
                key = tr[remote_col.name]
                grouped.setdefault(key, []).append(tr)
                
            for r in rows:
                p_key = r[local_col.name]
                matched_rows = grouped.get(p_key, [])
                if rel_prop.uselist:
                    r[rel_name] = matched_rows
                else:
                    r[rel_name] = matched_rows[0] if matched_rows else None
                    
        return rows

    async with db_module.connection.session() as session:
        return await fetch_node(model, relation_tree, session)


def performance_mode(func: Callable = None, *, relations: List[str] = None):
    """
    Route decorator that triggers high-performance execution.
    Bypasses SQLAlchemy ORM object instantiation and Pydantic serialization.
    """
    if func is None:
        return lambda f: performance_mode(f, relations=relations)

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # 1. Resolve db module
        from vorte.modules.database.module import DatabaseModule
        from fastapi import Request
        
        db = None
        req = None
        for k, v in kwargs.items():
            if hasattr(v, "app") and hasattr(v.app, "modules"):
                req = v
                if not db:
                    db = v.app.modules.get("database")
            elif hasattr(v, "connection") and hasattr(v.connection, "session"):
                db = v
                
        if not db:
            for arg in args:
                if hasattr(arg, "app") and hasattr(arg, "modules"):
                    req = arg
                    db = arg.app.modules.get("database")
                    break
                elif hasattr(arg, "connection") and hasattr(arg, "connection", "session"):
                    db = arg
                    break
                    
        if req and not db:
            if hasattr(req, "app") and hasattr(req.app, "modules"):
                db = req.app.modules.get("database")
                
        if not db:
            raise RuntimeError(
                "Performance Mode: could not locate DatabaseModule. "
                "Ensure DatabaseModule or Request is passed as an argument to the route handler."
            )

        # 2. Invoke original handler
        result = await func(*args, **kwargs)

        # 3. Extract model and relations
        model = None
        if isinstance(result, type) and hasattr(result, "__mapper__"):
            model = result
        elif hasattr(result, "column_descriptions"):
            model = result.column_descriptions[0]["entity"]
        elif hasattr(result, "_entity") and hasattr(result._entity, "class_"):
            model = result._entity.class_
        else:
            # Fallback: if result is already a dictionary/list/response, return it
            return result

        from vorte.modules.database.planner import active_relations
        relations_list = relations or list(active_relations.get()) or getattr(func, "_vorte_relations", [])

        # 4. Compile and stream
        db_url = db.connection.url
        is_postgres = db_url.startswith("postgresql")

        if is_postgres:
            sql_str = PreparedSQLManager.get_compiled_query(db_url, model, relations_list)
            async with db.connection.session() as session:
                start_db = time.perf_counter_ns()
                res = await session.execute(text(sql_str))
                row = res.fetchone()
                db_elapsed = time.perf_counter_ns() - start_db
                if _metrics is not None:
                    _metrics.increment_database_wait_time(db_elapsed)
                raw_data = row[0] if row else "[]"
                # If result is already a string (Postgres json_agg output), we can stream it directly!
                if isinstance(raw_data, str):
                    raw_bytes = raw_data.encode("utf-8")
                elif isinstance(raw_data, bytes):
                    raw_bytes = raw_data
                else:
                    raw_bytes = FastSerializer.dumps(raw_data)
        else:
            # Fallback SQLite stitching
            start_db = time.perf_counter_ns()
            data = await fetch_and_stitch(db, model, relations_list)
            db_elapsed = time.perf_counter_ns() - start_db
            if _metrics is not None:
                _metrics.increment_database_wait_time(db_elapsed)
            raw_bytes = FastSerializer.dumps(data)

        return VorteStreamResponse(raw_bytes, media_type="application/json")

    return wrapper
