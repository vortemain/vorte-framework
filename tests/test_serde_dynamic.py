"""Tests for Phase 3: Dynamic serialization and deserialization (JSON, Msgpack, CBOR, Protobuf)."""
import pytest
from vorte.core.serializer import FastSerializer

def test_json_serde():
    payload = {
        "string": "hello world",
        "int": 42,
        "float": 3.14,
        "bool": True,
        "null": None,
        "list": [1, 2, "three"],
        "dict": {"nested": "value"}
    }
    encoded = FastSerializer.dumps(payload, format="json")
    assert isinstance(encoded, bytes)
    
    decoded = FastSerializer.loads(encoded, format="json")
    assert decoded == payload

def test_msgpack_serde():
    payload = {
        "string": "msgpack works",
        "int": -99,
        "float": 0.0001,
        "bool": False,
        "null": None,
        "list": ["a", "b", "c"],
        "dict": {"x": 10, "y": 20}
    }
    encoded = FastSerializer.dumps(payload, format="msgpack")
    assert isinstance(encoded, bytes)
    
    decoded = FastSerializer.loads(encoded, format="msgpack")
    assert decoded == payload

def test_cbor_serde():
    payload = {
        "string": "cbor works",
        "int": 123456,
        "float": 1.2345,
        "bool": True,
        "null": None,
        "list": [True, False, None],
        "dict": {"nested_list": [10, 20]}
    }
    encoded = FastSerializer.dumps(payload, format="cbor")
    assert isinstance(encoded, bytes)
    
    decoded = FastSerializer.loads(encoded, format="cbor")
    assert decoded == payload

def test_protobuf_serde():
    payload = {
        "string": "protobuf works",
        "int": 777,
        "float": 99.9,
        "bool": True,
        "null": None,
        "list": [1, 2, 3],
        "dict": {"a": "b"}
    }
    encoded = FastSerializer.dumps(payload, format="protobuf")
    assert isinstance(encoded, bytes)
    
    decoded = FastSerializer.loads(encoded, format="protobuf")
    assert decoded == payload

def test_invalid_format_raises_or_defaults():
    payload = {"a": 1}
    # If the format is unknown, it should fallback to json or handle gracefully
    encoded = FastSerializer.dumps(payload, format="unknown_format")
    decoded = FastSerializer.loads(encoded, format="unknown_format")
    assert decoded == payload
