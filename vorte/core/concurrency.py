"""
Vorte Structured Concurrency
=============================
Lifecycle-aware async task group that coordinates asyncio tasks with Rust/Tokio workers.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional, Type, TypeVar
from types import TracebackType

from vorte._vorte_engine import PyCancellationToken

T = TypeVar("T")

class VorteTaskGroup:
    """
    A context manager that provides structured concurrency, linking Python asyncio tasks
    and Rust/Tokio background workers using a shared PyCancellationToken.
    """
    def __init__(self, token: Optional[PyCancellationToken] = None):
        self._tg = asyncio.TaskGroup()
        self._token = token or PyCancellationToken()
        self._tasks: list[asyncio.Task[Any]] = []

    async def __aenter__(self) -> VorteTaskGroup:
        await self._tg.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> bool | None:
        if exc_type is not None:
            # Propagate cancellation to Rust/Tokio instantly
            self._token.cancel()
        
        try:
            res = await self._tg.__aexit__(exc_type, exc_val, exc_tb)
            return res
        except Exception:
            self._token.cancel()
            raise

    def create_task(self, coro) -> asyncio.Task[Any]:
        """Create a managed Python task under this group."""
        task = self._tg.create_task(coro)
        self._tasks.append(task)
        return task

    @property
    def cancel_token(self) -> PyCancellationToken:
        """Get the PyCancellationToken shared with Rust/Tokio workers."""
        return self._token
