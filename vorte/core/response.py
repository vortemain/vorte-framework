"""
Vorte Standard Response System
================================
Every Vorte API response follows a consistent envelope structure.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Generic, List, Optional, TypeVar

from fastapi import status
from fastapi.responses import JSONResponse, StreamingResponse, Response
from collections.abc import AsyncIterable

from vorte.core.serializer import FastSerializer

T = TypeVar("T")


def _generate_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


@dataclass
class PaginationMeta:
    """Pagination metadata."""
    page: int = 1
    per_page: int = 20
    total: int = 0
    total_pages: int = 0
    next_cursor: Optional[str] = None
    prev_cursor: Optional[str] = None
    
    @classmethod
    def from_offset(cls, page: int, per_page: int, total: int) -> "PaginationMeta":
        total_pages = (total + per_page - 1) // per_page if per_page > 0 else 0
        return cls(
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
        )
    
    @classmethod
    def from_cursor(cls, cursor: Optional[str], limit: int, total: int) -> "PaginationMeta":
        return cls(
            per_page=limit,
            total=total,
            next_cursor=cursor,
        )


@dataclass
class AIMeta:
    """AI-related metadata included in responses."""
    model: Optional[str] = None
    provider: Optional[str] = None
    tokens: Optional[int] = None
    cost: Optional[str] = None
    cached: bool = False
    response_time_ms: Optional[int] = None


@dataclass
class ResponseMeta:
    """Response metadata."""
    request_id: str = field(default_factory=_generate_request_id)
    version: str = "v1"
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    latency_ms: Optional[int] = None
    rate_limit: Optional[Dict[str, Any]] = None


@dataclass
class ErrorDetail:
    """Detailed error information."""
    code: str
    message: str
    details: Optional[Any] = None
    field: Optional[str] = None


@dataclass
class VorteResponse(Generic[T]):
    """
    Standard Vorte API response envelope.
    
    Every response from a Vorte API follows this structure:
    {
        "success": true,
        "data": { ... },
        "meta": { ... },
        "ai": { ... },
        "error": null,
        "pagination": { ... }
    }
    """
    success: bool = True
    data: Optional[Any] = None
    meta: ResponseMeta = field(default_factory=ResponseMeta)
    ai: Optional[AIMeta] = None
    error: Optional[ErrorDetail] = None
    pagination: Optional[PaginationMeta] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary."""
        result: Dict[str, Any] = {
            "success": self.success,
            "data": self.data,
            "meta": {
                "request_id": self.meta.request_id,
                "version": self.meta.version,
                "timestamp": self.meta.timestamp,
            },
        }
        
        if self.meta.latency_ms is not None:
            result["meta"]["latency_ms"] = self.meta.latency_ms
        
        if self.ai:
            result["ai"] = {
                k: v for k, v in {
                    "model": self.ai.model,
                    "provider": self.ai.provider,
                    "tokens": self.ai.tokens,
                    "cost": self.ai.cost,
                    "cached": self.ai.cached,
                    "response_time_ms": self.ai.response_time_ms,
                }.items() if v is not None
            }
        
        if self.error:
            result["error"] = {
                "code": self.error.code,
                "message": self.error.message,
            }
            if self.error.details:
                result["error"]["details"] = self.error.details
            if self.error.field:
                result["error"]["field"] = self.error.field
        
        if self.pagination:
            result["pagination"] = {
                k: v for k, v in {
                    "page": self.pagination.page,
                    "per_page": self.pagination.per_page,
                    "total": self.pagination.total,
                    "total_pages": self.pagination.total_pages,
                    "next_cursor": self.pagination.next_cursor,
                    "prev_cursor": self.pagination.prev_cursor,
                }.items() if v is not None
            }
        
        return result


class VorteJSONResponse(JSONResponse):
    """
    FastAPI JSON response that wraps data in the standard Vorte envelope.
    """
    
    def __init__(
        self,
        data: Any = None,
        success: bool = True,
        status_code: int = status.HTTP_200_OK,
        error: Optional[ErrorDetail] = None,
        pagination: Optional[PaginationMeta] = None,
        ai: Optional[AIMeta] = None,
        latency_ms: Optional[int] = None,
        version: str = "v1",
        **kwargs,
    ):
        response = VorteResponse(
            success=success,
            data=data,
            error=error,
            pagination=pagination,
            ai=ai,
        )
        response.meta.latency_ms = latency_ms
        response.meta.version = version
        
        super().__init__(
            content=response.to_dict(),
            status_code=status_code,
            **kwargs,
        )

    def render(self, content: Any) -> bytes:
        """Use FastSerializer (orjson when available) for zero-overhead encoding."""
        return FastSerializer.dumps(content)


def success_response(
    data: Any = None,
    status_code: int = 200,
    pagination: Optional[PaginationMeta] = None,
    ai: Optional[AIMeta] = None,
    latency_ms: Optional[int] = None,
) -> VorteJSONResponse:
    """Create a success response."""
    return VorteJSONResponse(
        data=data,
        success=True,
        status_code=status_code,
        pagination=pagination,
        ai=ai,
        latency_ms=latency_ms,
    )


def error_response(
    code: str,
    message: str,
    status_code: int = 400,
    details: Optional[Any] = None,
    field_name: Optional[str] = None,
) -> VorteJSONResponse:
    """Create an error response."""
    error = ErrorDetail(
        code=code,
        message=message,
        details=details,
        field=field_name,
    )
    return VorteJSONResponse(
        success=False,
        error=error,
        status_code=status_code,
    )


def paginated_response(
    data: List[Any],
    page: int,
    per_page: int,
    total: int,
    status_code: int = 200,
) -> VorteJSONResponse:
    """Create a paginated response."""
    pagination = PaginationMeta.from_offset(page, per_page, total)
    return success_response(
        data=data,
        pagination=pagination,
        status_code=status_code,
    )


def ai_response(
    data: Any,
    model: str,
    provider: str = None,
    tokens: int = None,
    cost: str = None,
    cached: bool = False,
    response_time_ms: int = None,
) -> VorteJSONResponse:
    """Create an AI response with AI metadata."""
    ai_meta = AIMeta(
        model=model,
        provider=provider,
        tokens=tokens,
        cost=cost,
        cached=cached,
        response_time_ms=response_time_ms,
    )
    return success_response(data=data, ai=ai_meta)


class VorteSSEResponse(StreamingResponse):
    """
    Server-Sent Events (SSE) streaming response.

    Accepts an async generator/iterable yielding either strings, dictionaries/lists
    (which are serialized using FastSerializer), or SSE event payloads, and yields
    properly structured `data: <content>\n\n` event-stream blocks.
    """

    def __init__(
        self,
        content: AsyncIterable[Any],
        status_code: int = 200,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ):
        headers = headers or {}
        headers.setdefault("Cache-Control", "no-cache")
        headers.setdefault("Connection", "keep-alive")
        headers.setdefault("X-Accel-Buffering", "no")

        async def sse_generator() -> AsyncIterable[bytes]:
            async for item in content:
                if isinstance(item, bytes):
                    yield item
                elif isinstance(item, str):
                    if item.startswith("data:") or item.startswith("event:") or item.startswith(":"):
                        yield item.encode("utf-8")
                    else:
                        yield b"data: " + item.encode("utf-8") + b"\n\n"
                else:
                    serialized_bytes = FastSerializer.dumps(item)
                    yield b"data: " + serialized_bytes + b"\n\n"

        super().__init__(
            content=sse_generator(),
            status_code=status_code,
            media_type="text/event-stream",
            headers=headers,
            **kwargs,
        )


class VorteStreamResponse(Response):
    """
    Zero-copy streaming response that bypasses Starlette/FastAPI overhead on the hot path.
    """
    def __init__(
        self,
        content: Any,
        status_code: int = 200,
        headers: Optional[Dict[str, str]] = None,
        media_type: str = "application/json",
    ):
        super().__init__(content=None, status_code=status_code, headers=headers, media_type=media_type)
        self.content = content

    async def __call__(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        # Build raw low-level headers for ASGI start
        headers_list = [
            (b"content-type", self.media_type.encode("utf-8")),
        ]
        for k, v in self.headers.items():
            headers_list.append((k.lower().encode("utf-8"), v.encode("utf-8")))

        await send({
            "type": "http.response.start",
            "status": self.status_code,
            "headers": headers_list,
        })

        if isinstance(self.content, (bytes, bytearray)):
            # Direct binary write: one-shot fast stream
            await send({
                "type": "http.response.body",
                "body": self.content,
                "more_body": False,
            })
        elif isinstance(self.content, str):
            await send({
                "type": "http.response.body",
                "body": self.content.encode("utf-8"),
                "more_body": False,
            })
        else:
            # We assume it is an async generator or iterable
            async for chunk in self.content:
                if isinstance(chunk, str):
                    chunk_bytes = chunk.encode("utf-8")
                elif isinstance(chunk, (bytes, bytearray)):
                    chunk_bytes = chunk
                else:
                    chunk_bytes = FastSerializer.dumps(chunk)

                await send({
                    "type": "http.response.body",
                    "body": chunk_bytes,
                    "more_body": True,
                })

            await send({
                "type": "http.response.body",
                "body": b"",
                "more_body": False,
            })

