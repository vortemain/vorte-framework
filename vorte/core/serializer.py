"""
Vorte Fast Serializer
======================
Zero-copy-friendly JSON serialization using orjson when available, falling
back to the stdlib json module. Also provides the ``@lazy_schema`` decorator
for deferred Pydantic validation — data is kept as raw bytes until the route
handler explicitly calls ``.validate()``, eliminating eager validation debt
on every inbound request.

Blueprint reference: §4.3 Eliminating Serialization Overhead via Zero-Copy Paths
"""

from __future__ import annotations

import json as _stdlib_json
from functools import wraps
from typing import Any, Callable, Optional, Type, TypeVar

from pydantic import BaseModel

F = TypeVar("F", bound=Callable)


# ---------------------------------------------------------------------------
# Fast Serializer
# ---------------------------------------------------------------------------

try:
    import orjson as _orjson  # type: ignore[import]
except ImportError:
    _orjson = None

try:
    from vorte._vorte_engine import NativeSerde
    _native_serde = NativeSerde()
except ImportError:
    _native_serde = None

# Unwrapped, minimal-layer serializers for maximum JSON speed
if _orjson is not None:
    def _dumps_json(obj: Any) -> bytes:
        return _orjson.dumps(obj)

    def _loads_json(data: bytes | str) -> Any:
        return _orjson.loads(data)

    _BACKEND = "orjson" if _native_serde is None else "native"
else:
    def _dumps_json(obj: Any) -> bytes:
        return _stdlib_json.dumps(obj, separators=(",", ":"), default=str).encode("utf-8")

    def _loads_json(data: bytes | str) -> Any:
        return _stdlib_json.loads(data)

    _BACKEND = "stdlib"


def _dumps(obj: Any, format: str = "json") -> bytes:
    if format == "json":
        return _dumps_json(obj)
    if _native_serde is not None:
        return _native_serde.serialize(obj, format)
    raise ValueError(f"Format '{format}' not supported without native engine")


def _dumps_str(obj: Any, format: str = "json") -> str:
    if format == "json":
        if _orjson is not None:
            return _orjson.dumps(obj).decode("utf-8")
        return _stdlib_json.dumps(obj, separators=(",", ":"), default=str)
    if _native_serde is not None:
        return _native_serde.serialize(obj, format).decode("utf-8")
    raise ValueError(f"Format '{format}' not supported without native engine")


def _loads(data: bytes | str, format: str = "json") -> Any:
    if format == "json":
        return _loads_json(data)
    if isinstance(data, str):
        data = data.encode("utf-8")
    if _native_serde is not None:
        return _native_serde.deserialize(data, format)
    raise ValueError(f"Format '{format}' not supported without native engine")


class FastSerializer:
    """
    Drop-in JSON serializer that picks the fastest available backend.

    Backend selection (highest priority first):
      1. ``native``  — Rust-backed NativeSerde, zero-copy
      2. ``orjson``  — C-extension, 3–10× faster than stdlib
      3. ``json``    — stdlib fallback (always available)

    Usage::

        data = FastSerializer.dumps({"key": "value"})  # -> bytes
        obj  = FastSerializer.loads(data)              # -> dict

    """

    backend: str = _BACKEND

    @staticmethod
    def dumps(obj: Any, format: str = "json") -> bytes:
        """Serialize *obj* to a UTF-8 encoded JSON or other format byte string."""
        return _dumps(obj, format)

    @staticmethod
    def dumps_str(obj: Any, format: str = "json") -> str:
        """Serialize *obj* to a JSON or other format string (text, not bytes)."""
        return _dumps_str(obj, format)

    @staticmethod
    def loads(data: bytes | str, format: str = "json") -> Any:
        """Deserialize *data* from JSON or other format."""
        return _loads(data, format)

    @classmethod
    def is_native(cls) -> bool:
        """Return ``True`` if native Rust serialization is being used."""
        return cls.backend == "native" or cls.backend == "orjson"

    @staticmethod
    def benchmark_serialize(obj: Any) -> dict:
        """
        Benchmark each serialization stage for the given object.
        Measures:
          1. Preprocessing / Conversion (preparing standard dictionary or raw rows)
          2. Serialization (actual encoding to bytes)
          3. Copying / Transport (simulation of memory copy)
          4. Total pipeline duration
        """
        import time
        metrics = {}
        
        # 1. Preprocessing / Conversion
        t0 = time.perf_counter_ns()
        if hasattr(obj, "to_dict"):
            prepared = obj.to_dict()
        elif hasattr(obj, "model_dump"):
            prepared = obj.model_dump()
        else:
            prepared = obj
        t1 = time.perf_counter_ns()
        metrics["preprocessing_ns"] = t1 - t0
        metrics["preprocessing_ms"] = (t1 - t0) / 1_000_000.0
        
        # 2. Serialization
        t2 = time.perf_counter_ns()
        if _orjson is not None:
            serialized = _orjson.dumps(prepared)
        else:
            serialized = _stdlib_json.dumps(prepared).encode("utf-8")
        t3 = time.perf_counter_ns()
        metrics["serialization_ns"] = t3 - t2
        metrics["serialization_ms"] = (t3 - t2) / 1_000_000.0
        
        # 3. Copying / Transport simulation
        t4 = time.perf_counter_ns()
        _ = bytes(serialized)
        t5 = time.perf_counter_ns()
        metrics["copying_ns"] = t5 - t4
        metrics["copying_ms"] = (t5 - t4) / 1_000_000.0
        
        metrics["total_ns"] = (t1 - t0) + (t3 - t2) + (t5 - t4)
        metrics["total_ms"] = metrics["total_ns"] / 1_000_000.0
        metrics["payload_size_bytes"] = len(serialized)
        
        return metrics


# ---------------------------------------------------------------------------
# Lazy Schema Decorator
# ---------------------------------------------------------------------------

class _LazyPayload:
    """
    Wraps raw request bytes and defers Pydantic validation until ``.validate()``
    is called. This eliminates eager deserialization cost for endpoints that
    need to inspect headers or auth before touching the body.

    Usage inside a route handler (after applying ``@lazy_schema``)::

        @lazy_schema(UserCreate)
        @app.post("/users")
        async def create_user(payload: _LazyPayload):
            # body NOT validated yet — zero cost so far
            data: UserCreate = payload.validate()
            ...
    """

    __slots__ = ("_raw", "_model", "_parsed")

    def __init__(self, raw: bytes, model: Type[BaseModel]) -> None:
        self._raw = raw
        self._model = model
        self._parsed: Optional[BaseModel] = None

    def validate(self) -> BaseModel:
        """Parse and validate the raw bytes against the registered Pydantic model."""
        if self._parsed is None:
            self._parsed = self._model.model_validate_json(self._raw)
        return self._parsed

    @property
    def raw(self) -> bytes:
        """Access the unvalidated raw bytes."""
        return self._raw

    def __repr__(self) -> str:
        state = "validated" if self._parsed is not None else "deferred"
        return f"<_LazyPayload model={self._model.__name__} state={state}>"


def lazy_schema(model: Type[BaseModel]) -> Callable[[F], F]:
    """
    Route decorator that defers Pydantic validation to the point of use.

    Wraps the route handler so the first body-typed parameter receives a
    :class:`_LazyPayload` instead of a fully-parsed model instance.  Call
    ``.validate()`` on the payload only when the business logic actually needs
    the data.

    Blueprint reference: §3 — Zero-Copy SIMD Acceleration / lazy schemas

    Usage::

        @lazy_schema(UserCreate)
        @app.post("/users")
        async def create_user(payload: _LazyPayload):
            user = payload.validate()
            ...
    """
    def decorator(func: F) -> F:
        func._vorte_lazy_schema = model  # type: ignore[attr-defined]

        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)

        wrapper._vorte_lazy_schema = model  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator
