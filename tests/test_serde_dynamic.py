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


def test_enterprise_types_serde():
    import datetime
    from decimal import Decimal
    import uuid

    class CustomModel:
        def __init__(self, val):
            self.val = val
        def to_dict(self):
            return {"val": self.val}

    dt = datetime.datetime(2026, 5, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
    d = datetime.date(2026, 5, 20)
    t = datetime.time(12, 0, 0)
    dec = Decimal("123.45")
    u = uuid.UUID("12345678-1234-5678-1234-567812345678")
    custom = CustomModel("hello")

    payload = {
        "dt": dt,
        "d": d,
        "t": t,
        "dec": dec,
        "u": u,
        "custom": custom
    }

    # Serialize with our NativeSerde via FastSerializer using full mime-type negotiation
    encoded_json = FastSerializer.dumps(payload, format="application/json")
    decoded_json = FastSerializer.loads(encoded_json, format="application/json")

    assert decoded_json["dt"] == dt.isoformat()
    assert decoded_json["d"] == d.isoformat()
    assert decoded_json["t"] == t.isoformat()
    assert decoded_json["dec"] == "123.45"
    assert decoded_json["u"] == str(u)
    assert decoded_json["custom"] == {"val": "hello"}

    # Test MessagePack via content negotiation headers
    encoded_msgpack = FastSerializer.dumps(payload, format="application/x-msgpack")
    decoded_msgpack = FastSerializer.loads(encoded_msgpack, format="application/msgpack")
    assert decoded_msgpack["custom"] == {"val": "hello"}
    assert decoded_msgpack["dec"] == "123.45"

    # Test CBOR via content negotiation headers
    encoded_cbor = FastSerializer.dumps(payload, format="application/cbor")
    decoded_cbor = FastSerializer.loads(encoded_cbor, format="application/cbor")
    assert decoded_cbor["custom"] == {"val": "hello"}
    assert decoded_cbor["dec"] == "123.45"

