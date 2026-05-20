"""
Vorte Queue - Worker
=====================
Worker that processes jobs from one or more queues.  Supports concurrent
batch processing, backpressure-aware polling, exponential backoff retries,
timeout enforcement, and graceful shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from .job import Job, JobPayload, JobPriority, resolve_job_class

logger = logging.getLogger("vorte.queue.worker")


@dataclass
class WorkerStats:
    """Runtime statistics for a worker."""
    jobs_processed: int = 0
    jobs_succeeded: int = 0
    jobs_failed:    int = 0
    jobs_retried:   int = 0
    jobs_timed_out: int = 0
    start_time:     float = field(default_factory=time.time)
    last_job_time:  Optional[float] = None


class Worker:
    """
    Processes jobs from one or more queues.

    The worker polls the QueueManager for pending jobs in configurable
    batch sizes, instantiates the appropriate Job subclass, and calls
    ``handle(**kwargs)``.  Failed jobs are retried with exponential
    backoff up to the configured max attempts.

    Features:
        - Concurrent batch processing (configurable batch_size + concurrency)
        - Backpressure-aware polling (backs off when all queues are "full")
        - Exponential backoff on retries
        - Per-job timeout enforcement
        - Graceful shutdown (finish running jobs)
        - Error reporting via callbacks
        - Queue prioritization

    Args:
        queues:        Queue names to consume from.
        concurrency:   Max concurrent job processors (semaphore slots).
        batch_size:    Jobs to dequeue per poll cycle (worker efficiency).
        poll_interval: Seconds between queue polls when empty.
        on_job_success: Callback fired after a successful job.
        on_job_failure: Callback fired after a final (permanent) failure.
        name:          Human-readable worker name.
    """

    def __init__(
        self,
        queues:         Optional[List[str]] = None,
        concurrency:    int   = 10,
        batch_size:     int   = 1,
        poll_interval:  float = 1.0,
        on_job_success: Optional[Callable] = None,
        on_job_failure: Optional[Callable] = None,
        name:           str   = "worker-1",
    ):
        self._queues        = list(queues) if queues else ["default"]
        self._concurrency   = concurrency
        self._batch_size    = max(1, batch_size)
        self._poll_interval = poll_interval
        self._on_success    = on_job_success
        self._on_failure    = on_job_failure
        self._name          = name

        self._running      = False
        self._stats        = WorkerStats()
        self._active_jobs: Set[asyncio.Task] = set()
        self._semaphore    = asyncio.Semaphore(concurrency)
        self._queue_manager: Optional[Any] = None

    # ─────────────────────── properties ──────────────────────────────────────

    @property
    def stats(self) -> WorkerStats:
        return self._stats

    @property
    def is_running(self) -> bool:
        return self._running

    # ─────────────────────── lifecycle ───────────────────────────────────────

    async def start(self, queue_manager: Any) -> None:
        """
        Start the worker and begin processing jobs.

        Blocks until the worker is stopped via ``shutdown()``.
        """
        self._queue_manager = queue_manager
        self._running       = True
        self._stats         = WorkerStats()

        logger.info(
            "Worker '%s' started (queues=%s, concurrency=%d, batch_size=%d)",
            self._name, self._queues, self._concurrency, self._batch_size,
        )

        try:
            while self._running:
                await self._poll_and_process()
        except asyncio.CancelledError:
            logger.info("Worker '%s' cancelled", self._name)
        finally:
            if self._active_jobs:
                logger.info(
                    "Worker '%s': waiting for %d active jobs to finish…",
                    self._name, len(self._active_jobs),
                )
                await asyncio.gather(*self._active_jobs, return_exceptions=True)
            self._running = False
            logger.info("Worker '%s' stopped", self._name)

    async def shutdown(self, wait: bool = True, timeout: float = 30.0) -> None:
        """Signal the worker to stop, optionally waiting for active jobs."""
        self._running = False
        logger.info("Worker '%s' shutting down…", self._name)

        if wait and self._active_jobs:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._active_jobs, return_exceptions=True),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Worker '%s' timed out waiting for active jobs", self._name
                )
                for task in self._active_jobs:
                    task.cancel()

    # ─────────────────────── polling ─────────────────────────────────────────

    async def _poll_and_process(self) -> None:
        """Poll the queue (batch) and spawn tasks for each dequeued job."""
        if self._queue_manager is None:
            return

        try:
            # Backpressure guard: if the queue is full we must not attempt to
            # enqueue retries — but we can still drain what's already there.
            # Only sleep if the queue is legitimately empty (no jobs returned).
            payloads = await self._queue_manager.dequeue_batch(
                queues=self._queues,
                count=self._batch_size,
            )

            if payloads:
                for payload in payloads:
                    await self._semaphore.acquire()
                    task = asyncio.create_task(self._process_job(payload))
                    task.add_done_callback(self._on_task_done)
                    self._active_jobs.add(task)
            else:
                # Check backpressure state: if all queues are "full" (a retry
                # storm), back off longer to avoid spin-looping.
                all_full = await self._all_queues_full()
                sleep_for = self._poll_interval * (2.0 if all_full else 1.0)
                await asyncio.sleep(sleep_for)

        except Exception as exc:
            logger.error("Worker '%s' poll error: %s", self._name, exc)
            await asyncio.sleep(self._poll_interval)

    async def _all_queues_full(self) -> bool:
        """Return True only if every managed queue is at capacity."""
        try:
            wm = await self._queue_manager.watermark_state()
            return all(wm.get(q, "normal") == "full" for q in self._queues)
        except Exception:
            return False

    # ─────────────────────── job processing ──────────────────────────────────

    async def _process_job(self, payload: JobPayload) -> None:
        """Resolve the job class, call handle(), handle errors."""
        self._stats.jobs_processed += 1

        job_class = resolve_job_class(payload.class_name)
        if job_class is None:
            logger.error("Unknown job class: %s", payload.class_name)
            payload.status    = "failed"
            payload.error     = f"Unknown job class: {payload.class_name}"
            payload.failed_at = time.time()
            self._stats.jobs_failed += 1
            if self._queue_manager:
                await self._queue_manager.fail(payload.id, payload.error)
            return

        job               = job_class()
        payload.status    = "running"
        payload.started_at = time.time()
        payload.attempts  += 1

        from vorte.core.tracing import set_trace_id, reset_trace_id
        token = set_trace_id(getattr(payload, "trace_id", ""))
        try:
            try:
                result = await asyncio.wait_for(
                    job.handle(**payload.payload),
                    timeout=payload.timeout,
                )
                payload.status       = "completed"
                payload.completed_at = time.time()
                self._stats.jobs_succeeded += 1
                self._stats.last_job_time  = time.time()

                if self._queue_manager:
                    await self._queue_manager.complete(payload.id)

                logger.debug(
                    "Job %s (%s) completed in %.2fs",
                    payload.id, payload.class_name,
                    payload.completed_at - payload.started_at,
                )

                if self._on_success:
                    try:
                        if asyncio.iscoroutinefunction(self._on_success):
                            await self._on_success(payload, result)
                        else:
                            self._on_success(payload, result)
                    except Exception:
                        pass

            except asyncio.TimeoutError:
                payload.status    = "failed"
                payload.error     = f"Job timed out after {payload.timeout}s"
                payload.failed_at = time.time()
                self._stats.jobs_timed_out += 1
                await self._handle_failure(payload)

            except Exception as exc:
                payload.error     = f"{type(exc).__name__}: {str(exc)}"
                payload.failed_at = time.time()
                self._stats.jobs_failed += 1
                logger.error(
                    "Job %s (%s) failed: %s\n%s",
                    payload.id, payload.class_name, exc,
                    traceback.format_exc(),
                )
                await self._handle_failure(payload)
        finally:
            reset_trace_id(token)

    async def _handle_failure(self, payload: JobPayload) -> None:
        """Retry or permanently fail a job.  Uses exponential back-off."""
        if payload.attempts < payload.max_attempts:
            backoff           = payload.retry_delay * (2 ** (payload.attempts - 1))
            payload.status    = "pending"
            payload.run_at    = time.time() + backoff
            self._stats.jobs_retried += 1

            logger.info(
                "Retrying job %s in %ds (attempt %d/%d)",
                payload.id, backoff, payload.attempts, payload.max_attempts,
            )

            if self._queue_manager:
                try:
                    await self._queue_manager.enqueue_raw(payload)
                except Exception as exc:
                    # Backpressure / queue full — log but don't crash worker
                    logger.warning(
                        "Could not re-enqueue job %s for retry: %s", payload.id, exc
                    )
        else:
            payload.status = "failed"
            logger.error(
                "Job %s (%s) permanently failed after %d attempts: %s",
                payload.id, payload.class_name, payload.attempts, payload.error,
            )
            if self._queue_manager:
                await self._queue_manager.fail(payload.id, payload.error or "unknown")

            if self._on_failure:
                try:
                    if asyncio.iscoroutinefunction(self._on_failure):
                        await self._on_failure(payload)
                    else:
                        self._on_failure(payload)
                except Exception:
                    pass

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._active_jobs.discard(task)
        self._semaphore.release()
        if not task.cancelled() and task.exception():
            logger.debug("Task exception: %s", task.exception())
