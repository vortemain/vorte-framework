"""
Vorte Queue Module - Main Module
==================================
Background job processing with Rust-native crossbeam channels, priority queues,
backpressure watermarks, dead-letter retention, and optional Redis STREAM backend.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from vorte.core.module import Module, ModuleMeta, ModulePriority
from vorte.core.response import success_response, error_response
from vorte.modules.queue.queue import QueueManager, Priority
from vorte.modules.queue.worker import Worker
from vorte.modules.queue.scheduler import Scheduler


class QueueModule(Module):
    """
    Background job processing module (Rust-native queue engine).

    The underlying engine is always the Rust crossbeam-channel priority queue.
    Set ``driver='redis'`` to indicate that Redis STREAM consumption is also
    desired (reserved for future external-consumer support; the in-process
    engine still handles local workers).

    Args:
        driver:          ``"rust"`` (default) or ``"redis"``.
        capacity:        Hard channel capacity per queue     (default 65 536).
        hwm_ratio:       High-watermark fraction 0–1        (default 0.80).
        lwm_ratio:       Low-watermark fraction  0–1        (default 0.20).
        dlq_retention:   Max entries per dead-letter queue  (default 5 000).
        dlq_ttl_secs:    DLQ TTL in seconds; 0 = no TTL    (default 0).
        default_retries: Default max job retries            (default 3).
        default_retry_delay: Default retry delay seconds    (default 5).
        batch_size:      Jobs dequeued per worker poll      (default 1).
        concurrency:     Worker concurrency slots           (default 10).
        queues:          Queue names the default worker listens to.

    Usage::

        app.register(QueueModule())
        app.register(QueueModule(driver='redis', capacity=10_000, batch_size=5))
    """

    meta = ModuleMeta(
        name        = "queue",
        version     = "2.0.0",
        description = (
            "Rust-native background job processing with crossbeam channels, "
            "priority queues, backpressure watermarks, and dead-letter support"
        ),
        priority    = ModulePriority.QUEUE,
    )

    def __init__(
        self,
        *,
        driver:              str   = "rust",
        capacity:            int   = 65_536,
        hwm_ratio:           float = 0.80,
        lwm_ratio:           float = 0.20,
        dlq_retention:       int   = 5_000,
        dlq_ttl_secs:        int   = 0,
        default_retries:     int   = 3,
        default_retry_delay: int   = 5,
        batch_size:          int   = 1,
        concurrency:         int   = 10,
        queues:              Optional[List[str]] = None,
    ) -> None:
        super().__init__(
            driver              = driver,
            default_retries     = default_retries,
            default_retry_delay = default_retry_delay,
        )
        self._driver          = driver
        self._capacity        = capacity
        self._hwm_ratio       = hwm_ratio
        self._lwm_ratio       = lwm_ratio
        self._dlq_retention   = dlq_retention
        self._dlq_ttl_secs    = dlq_ttl_secs
        self._batch_size      = batch_size
        self._concurrency     = concurrency
        self._queues          = queues or ["default"]

        self._manager:        Optional[QueueManager] = None
        self._worker:         Optional[Worker]       = None
        self._scheduler:      Optional[Scheduler]    = None
        self._router          = APIRouter(prefix="/queue", tags=["Queue"])
        self._worker_task:    Optional[asyncio.Task] = None
        self._scheduler_task: Optional[asyncio.Task] = None

    # ─────────────────────── registration ────────────────────────────────────

    def register(self, app) -> None:
        self._manager = QueueManager(
            driver        = self._driver,
            capacity      = self._capacity,
            hwm_ratio     = self._hwm_ratio,
            lwm_ratio     = self._lwm_ratio,
            dlq_retention = self._dlq_retention,
            dlq_ttl_secs  = self._dlq_ttl_secs,
        )
        self._worker = Worker(
            queues      = self._queues,
            concurrency = self._concurrency,
            batch_size  = self._batch_size,
        )
        self._scheduler = Scheduler()

        if hasattr(app, "container"):
            app.container.register_instance(QueueManager, self._manager)
            app.container.register_instance(Worker,       self._worker)
            app.container.register_instance(Scheduler,    self._scheduler)

        self._setup_routes()
        app.include_router(self._router)

    def _setup_routes(self) -> None:

        @self._router.get("/stats", include_in_schema=False)
        async def queue_stats():
            stats = await self._manager.stats()
            return success_response(stats)

        @self._router.get("/watermark", include_in_schema=False)
        async def queue_watermark():
            """Return backpressure watermark state per queue."""
            wm = await self._manager.watermark_state()
            return success_response(wm)

        @self._router.get("/failed", include_in_schema=False)
        async def failed_jobs(queue: str = "default"):
            jobs = await self._manager.get_failed_jobs(queue_name=queue)
            return success_response(jobs)

        @self._router.post("/retry/{job_id}", include_in_schema=False)
        async def retry_job(job_id: str):
            ok = await self._manager.retry_failed(job_id)
            if not ok:
                return error_response(
                    "JOB_NOT_FOUND",
                    f"Failed job '{job_id}' not found in dead-letter queue",
                    status_code=404,
                )
            return success_response({"message": f"Job '{job_id}' queued for retry"})

        @self._router.post("/dlq/purge", include_in_schema=False)
        async def purge_dlq():
            """Prune TTL-expired entries from all dead-letter queues."""
            removed = await self._manager.purge_expired_dlq()
            return success_response({"removed": removed})

    # ─────────────────────── properties ──────────────────────────────────────

    @property
    def manager(self) -> QueueManager:
        return self._manager

    # ─────────────────────── lifecycle ───────────────────────────────────────

    async def on_startup(self) -> None:
        if self._worker:
            self._worker_task = asyncio.create_task(
                self._worker.start(self._manager)
            )
        if self._scheduler:
            self._scheduler_task = asyncio.create_task(
                self._scheduler.start(self._manager)
            )

    async def on_shutdown(self) -> None:
        if self._worker:
            await self._worker.shutdown()
        if self._scheduler:
            await self._scheduler.stop()
