"""Tests for vorte.core.serializer — FastSerializer and @lazy_schema."""
import pytest
from pydantic import BaseModel
from vorte.core.serializer import FastSerializer, lazy_schema, _LazyPayload


def test_fast_serializer_dumps_returns_bytes():
    data = {"key": "value", "num": 42}
    result = FastSerializer.dumps(data)
    assert isinstance(result, bytes)
    assert b"key" in result


def test_fast_serializer_loads_roundtrip():
    data = {"hello": "world", "n": 7}
    encoded = FastSerializer.dumps(data)
    decoded = FastSerializer.loads(encoded)
    assert decoded == data


def test_fast_serializer_dumps_str_returns_string():
    result = FastSerializer.dumps_str({"a": 1})
    assert isinstance(result, str)
    assert "a" in result


def test_fast_serializer_handles_nested():
    nested = {"outer": {"inner": [1, 2, 3]}, "flag": True}
    enc = FastSerializer.dumps(nested)
    dec = FastSerializer.loads(enc)
    assert dec == nested


def test_fast_serializer_backend_is_set():
    # Backend should be either native, orjson or stdlib
    assert FastSerializer.backend in ("native", "orjson", "stdlib")


def test_fast_serializer_is_native():
    # is_native is True when native or orjson is used
    if FastSerializer.backend == "native":
        assert FastSerializer.is_native() is True
    else:
        try:
            import orjson
            assert FastSerializer.is_native() is True
        except ImportError:
            assert FastSerializer.is_native() is False


def test_lazy_schema_decorator_sets_metadata():
    class UserCreate(BaseModel):
        name: str
        age: int

    @lazy_schema(UserCreate)
    async def handler():
        pass

    assert hasattr(handler, "_vorte_lazy_schema")
    assert handler._vorte_lazy_schema is UserCreate


def test_lazy_payload_deferred_validation():
    class Item(BaseModel):
        name: str
        price: float

    raw = b'{"name": "Widget", "price": 9.99}'
    payload = _LazyPayload(raw, Item)

    assert payload.raw == raw
    # Not validated yet
    item = payload.validate()
    assert item.name == "Widget"
    assert item.price == 9.99
    # Second call returns same cached instance
    assert payload.validate() is item


def test_lazy_payload_invalid_json_raises():
    class Item(BaseModel):
        name: str

    raw = b"not-json"
    payload = _LazyPayload(raw, Item)
    with pytest.raises(Exception):
        payload.validate()
