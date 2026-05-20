import pytest
import asyncio
from vorte import Vorte
from fastapi.testclient import TestClient

def test_vorte_buffer_zero_copy():
    try:
        from vorte._vorte_engine import NativeSerde, VorteBuffer
    except ImportError:
        pytest.skip("vorte._vorte_engine not compiled")

    ns = NativeSerde()
    data = {"name": "VORTE Engine", "version": 1.0, "active": True}
    
    # 1. Serialize to VorteBuffer
    buf = ns.serialize_to_buffer(data, "json")
    assert isinstance(buf, VorteBuffer)

    # 2. Extract memoryview (zero-copy)
    mv = buf.to_memoryview()
    assert isinstance(mv, memoryview)

    # 3. Read content from memoryview and verify correct bytes
    raw_bytes = bytes(mv)
    import json
    loaded = json.loads(raw_bytes.decode("utf-8"))
    assert loaded == data

    # 4. Verify to_bytes also works
    raw_bytes_from_buf = buf.to_bytes()
    assert raw_bytes_from_buf == raw_bytes


@pytest.mark.asyncio
async def test_structured_concurrency_cancellation():
    try:
        from vorte.core.concurrency import VorteTaskGroup
        from vorte._vorte_engine import PyCancellationToken
    except ImportError:
        pytest.skip("Structured Concurrency elements not available")

    token = PyCancellationToken()
    assert not token.is_cancelled()

    async def worker_coro():
        await asyncio.sleep(0.1)

    async def failing_coro():
        await asyncio.sleep(0.01)
        raise ValueError("Simulated Exception")

    # VorteTaskGroup should automatically cancel token when a task fails
    with pytest.raises(BaseException) as exc_info:
        async with VorteTaskGroup(token) as tg:
            tg.create_task(worker_coro())
            tg.create_task(failing_coro())

    # Verify the exception is or contains ValueError("Simulated Exception")
    exc = exc_info.value
    if exc.__class__.__name__ in ("ExceptionGroup", "BaseExceptionGroup"):
        found = False
        for sub_exc in exc.exceptions:
            if isinstance(sub_exc, ValueError) and "Simulated Exception" in str(sub_exc):
                found = True
                break
        assert found, f"Expected ValueError with 'Simulated Exception' inside ExceptionGroup, got: {exc}"
    else:
        assert isinstance(exc, ValueError)
        assert "Simulated Exception" in str(exc)

    # Token must be cancelled!
    assert token.is_cancelled()


def test_prometheus_metrics_endpoint():
    from vorte import Vorte
    from fastapi.testclient import TestClient

    app = Vorte(auto_load=False)
    client = TestClient(app.fastapi)

    # Execute some serialized operations to increment counter if available
    try:
        from vorte._vorte_engine import NativeSerde
        ns = NativeSerde()
        _ = ns.serialize({"dummy": 123}, "json")
    except ImportError:
        pass

    response = client.get("/_vorte/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    
    text = response.text
    assert "vorte_serialization_time_ns" in text
    assert "vorte_database_wait_time_ns" in text
    assert "vorte_scheduling_latency_ns" in text
    assert "vorte_event_loop_lag_ns" in text
    assert "vorte_buffered_spans_total" in text
    assert "vorte_metrics_buffer_capacity_total" in text


def test_compiled_execution_graph():
    try:
        from vorte._vorte_engine import PyExecutionGraph
    except ImportError:
        pytest.skip("PyExecutionGraph not compiled")

    graph = PyExecutionGraph()
    
    # Define Nodes
    graph.add_middleware_node("m1", "AuthMiddleware", {"X-Auth": "Token"})
    graph.add_dependency_node("d1", "DBConnection", "postgres")
    graph.add_query_node("q1", "SelectUser", "SELECT * FROM users WHERE id = :id")
    graph.add_format_node("f1", "JSONFormatter", "json")
    graph.add_python_fallback_node("pf1", "SlowFallback")

    # Define edges (m1 -> d1 -> q1 -> f1)
    graph.add_edge("m1", "d1")
    graph.add_edge("d1", "q1")
    graph.add_edge("q1", "f1")
    graph.add_root("m1")

    res = graph.execute()
    assert "AuthMiddleware" in res
    assert "DBConnection" in res
    assert "SelectUser" in res
    assert "JSONFormatter" in res
    assert "SHORT-CIRCUIT" not in res

    # Test short-circuit node
    graph2 = PyExecutionGraph()
    graph2.add_middleware_node("m2", "CacheMiddleware", {"X-Short-Circuit": "True"})
    graph2.add_query_node("q2", "HeavyQuery", "SELECT * FROM huge_table")
    graph2.add_edge("m2", "q2")
    graph2.add_root("m2")

    res2 = graph2.execute()
    assert "CacheMiddleware" in res2
    assert "SHORT-CIRCUIT" in res2
    assert "HeavyQuery" not in res2  # Short-circuited and bypassed!
