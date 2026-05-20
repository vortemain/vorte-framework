"""Tests for Phase 4: Server-Sent Events (SSE) Response streaming."""
import pytest
from vorte import Vorte, VorteSSEResponse
from vorte.testing import VorteTestClient

async def sse_event_generator():
    yield "first event"
    yield {"key": "value"}
    yield b"data: raw bytes\n\n"
    yield "data: custom event string\n\n"

@pytest.mark.asyncio
async def test_sse_streaming():
    app = Vorte(auto_load=False)

    @app.get("/sse")
    async def get_sse():
        return VorteSSEResponse(sse_event_generator())

    async with VorteTestClient(app) as client:
        resp = await client.get("/sse")
        assert resp.status_code == 200
        assert resp._response.headers["content-type"].startswith("text/event-stream")
        assert resp._response.headers["cache-control"] == "no-cache"
        assert resp._response.headers["connection"] == "keep-alive"
        
        body = resp._response.content
        lines = [line.strip() for line in body.split(b"\n") if line.strip()]
        
        # Check first event
        assert b"data: first event" in lines
        # Check dict serialization
        assert b'data: {"key":"value"}' in lines or b'data: {"key": "value"}' in lines
        # Check raw bytes yield
        assert b"data: raw bytes" in lines
        # Check custom event string
        assert b"data: custom event string" in lines
