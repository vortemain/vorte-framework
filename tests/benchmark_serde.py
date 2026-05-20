"""Benchmark comparing Vorte NativeSerde serialization/deserialization vs orjson and stdlib json."""
import time
import json as stdlib_json
from vorte.core.serializer import FastSerializer

try:
    import orjson
except ImportError:
    orjson = None

def run_benchmarks():
    print("=" * 60)
    print("      Vorte Serialization Engine Performance Benchmark")
    print("=" * 60)
    
    # Large nested payload representing a typical complex API response
    payload = {
        "id": "req_1234567890abcdef",
        "timestamp": "2026-05-20T14:00:00Z",
        "success": True,
        "results": [
            {
                "index": i,
                "uuid": f"usr_uuid_{i}",
                "active": i % 2 == 0,
                "score": 98.6 + i * 0.1,
                "tags": ["admin", "user", "active", "api-client"],
                "profile": {
                    "first_name": f"User{i}",
                    "last_name": f"Tester{i}",
                    "address": {
                        "street": "123 Main St",
                        "city": "San Francisco",
                        "state": "CA",
                        "zip": "94105",
                        "coordinates": {"lat": 37.7749, "lng": -122.4194}
                    }
                }
            } for i in range(100)
        ],
        "meta": {
            "version": "1.0.0",
            "provider": "vorte-engine",
            "cache_hits": 42
        }
    }
    
    iterations = 2000
    print(f"Payload size: ~{len(stdlib_json.dumps(payload))} bytes")
    print(f"Iterations: {iterations}\n")
    
    # Dump benchmarks
    print("--- Serialization (dumps) ---")
    
    # Stdlib JSON
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = stdlib_json.dumps(payload, separators=(",", ":")).encode("utf-8")
    t_stdlib = time.perf_counter() - t0
    print(f"stdlib json: {t_stdlib:.4f}s ({iterations/t_stdlib:.1f} ops/sec)")
    
    # Orjson
    if orjson:
        t0 = time.perf_counter()
        for _ in range(iterations):
            _ = orjson.dumps(payload)
        t_orjson = time.perf_counter() - t0
        print(f"orjson:      {t_orjson:.4f}s ({iterations/t_orjson:.1f} ops/sec)")
    else:
        print("orjson:      Not installed")
        
    # Vorte NativeSerde JSON
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = FastSerializer.dumps(payload, format="json")
    t_native_json = time.perf_counter() - t0
    print(f"vorte json:  {t_native_json:.4f}s ({iterations/t_native_json:.1f} ops/sec)")
    
    # Vorte Msgpack
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = FastSerializer.dumps(payload, format="msgpack")
    t_native_msgpack = time.perf_counter() - t0
    print(f"vorte msgpk: {t_native_msgpack:.4f}s ({iterations/t_native_msgpack:.1f} ops/sec)")
    
    # Vorte CBOR
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = FastSerializer.dumps(payload, format="cbor")
    t_native_cbor = time.perf_counter() - t0
    print(f"vorte cbor:  {t_native_cbor:.4f}s ({iterations/t_native_cbor:.1f} ops/sec)")

    # Vorte Protobuf
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = FastSerializer.dumps(payload, format="protobuf")
    t_native_proto = time.perf_counter() - t0
    print(f"vorte proto: {t_native_proto:.4f}s ({iterations/t_native_proto:.1f} ops/sec)")

    print("\n--- Deserialization (loads) ---")
    json_bytes = FastSerializer.dumps(payload, format="json")
    msgpack_bytes = FastSerializer.dumps(payload, format="msgpack")
    cbor_bytes = FastSerializer.dumps(payload, format="cbor")
    proto_bytes = FastSerializer.dumps(payload, format="protobuf")
    
    # Stdlib JSON
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = stdlib_json.loads(json_bytes)
    t_stdlib_loads = time.perf_counter() - t0
    print(f"stdlib json: {t_stdlib_loads:.4f}s ({iterations/t_stdlib_loads:.1f} ops/sec)")
    
    # Orjson
    if orjson:
        t0 = time.perf_counter()
        for _ in range(iterations):
            _ = orjson.loads(json_bytes)
        t_orjson_loads = time.perf_counter() - t0
        print(f"orjson:      {t_orjson_loads:.4f}s ({iterations/t_orjson_loads:.1f} ops/sec)")
        
    # Vorte NativeSerde JSON
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = FastSerializer.loads(json_bytes, format="json")
    t_native_json_loads = time.perf_counter() - t0
    print(f"vorte json:  {t_native_json_loads:.4f}s ({iterations/t_native_json_loads:.1f} ops/sec)")
    
    # Vorte Msgpack
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = FastSerializer.loads(msgpack_bytes, format="msgpack")
    t_native_msgpack_loads = time.perf_counter() - t0
    print(f"vorte msgpk: {t_native_msgpack_loads:.4f}s ({iterations/t_native_msgpack_loads:.1f} ops/sec)")
    
    # Vorte CBOR
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = FastSerializer.loads(cbor_bytes, format="cbor")
    t_native_cbor_loads = time.perf_counter() - t0
    print(f"vorte cbor:  {t_native_cbor_loads:.4f}s ({iterations/t_native_cbor_loads:.1f} ops/sec)")

    # Vorte Protobuf
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = FastSerializer.loads(proto_bytes, format="protobuf")
    t_native_proto_loads = time.perf_counter() - t0
    print(f"vorte proto: {t_native_proto_loads:.4f}s ({iterations/t_native_proto_loads:.1f} ops/sec)")
    print("=" * 60)

if __name__ == "__main__":
    run_benchmarks()
