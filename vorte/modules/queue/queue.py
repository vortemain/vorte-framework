"""
Vorte Queue Module — Queue Manager
====================================
Rust-native priority queue with crossbeam channels, backpressure, dead-letter
retention, and optional Redis STREAM backend.

``InMemoryQueueBackend`` has been fully replaced by ``RustQueueBackend``
which delegates to the ``NativeQueue`` PyO3 extension.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type

if TYPE_CHECKING:
    from vorte.modules.queue.job import Job

from vorte.modules.queue.job import JobPayload, JobPriority

logger = logging.getLogger("vorte.queue")

# ── Priority name mapping (Python IntEnum → Rust str) ───────────────────────

_PRIORITY_NAMES: Dict[int, str] = {
    JobPriority.LOW:      "low",
    JobPriority.DEFAULT:  "normal",
    JobPriority.HIGH:     "high",
    JobPriority.CRITICAL: "critical",
}

_RUST_TO_PYTHON_PRIORITY: Dict[int, int] = {
    0: JobPriority.LOW,      # 0
    1: JobPriority.DEFAULT,  # 5
    2: JobPriority.HIGH,     # 10
    3: JobPriority.CRITICAL, # 20
}


def _priority_name(value: int) -> str:
    return _PRIORITY_NAMES.get(value, "normal")


# ── Serialization helpers ────────────────────────────────────────────────────

def _payload_to_bytes(payload: Dict[str, Any]) -> bytes:
    """Serialize the job's kwargs dict to JSON bytes."""
    return json.dumps(payload).encode("utf-8")


def _bytes_to_payload(data: bytes) -> Dict[str, Any]:
    """Deserialize JSON bytes back to the job's kwargs dict."""
    if not data:
        return {}
    return json.loads(data.decode("utf-8"))


def _raw_to_job_payload(raw: Dict[str, Any]) -> JobPayload:
    """Convert a NativeQueue dict (from Rust) back to a Python JobPayload."""
    rust_priority = raw.get("priority", 1)
    py_priority = _RUST_TO_PYTHON_PRIORITY.get(rust_priority, JobPriority.DEFAULT)

    return JobPayload(
        id           = raw["id"],
        class_name   = raw["job_class"],
        queue        = raw["queue"],
        payload      = _bytes_to_payload(bytes(raw.get("payload", b""))),
        priority     = py_priority,
        max_attempts = raw.get("max_attempts", 3),
        retry_delay  = int(raw.get("retry_delay", 30)),
        attempts     = raw.get("attempts", 0),
        status       = raw.get("status", "pending"),
        scheduled_at = raw.get("scheduled_at"),
        run_at       = raw.get("run_at"),
        started_at   = raw.get("started_at"),
        completed_at = raw.get("completed_at"),
        failed_at    = raw.get("failed_at"),
        error        = raw.get("error"),
        trace_id     = raw.get("trace_id", ""),
    )


# ── Exceptions ───────────────────────────────────────────────────────────────

class QueueFullError(RuntimeError):
    """Raised when a queue channel has hit its hard capacity limit."""


# ── RustQueueBackend ─────────────────────────────────────────────────────────

class RustQueueBackend:
    """
    Rust-native queue backend via NativeQueue (PyO3 extension).

    Replaces InMemoryQueueBackend entirely.  All queue state lives
    in the Rust-side crossbeam-channel engine — no Python locks,
    no asyncio.Lock, no in-process deque.

    Args:
        capacity:      Hard channel capacity per queue.
        hwm_ratio:     High-watermark fraction (0.0–1.0).
        lwm_ratio:     Low-watermark fraction  (0.0–1.0).
        dlq_retention: Max entries per dead-letter queue.
        dlq_ttl_secs:  Seconds before DLQ entries expire (0 = no TTL).
    """

    def __init__(
        self,
        capacity:      int   = 65_536,
        hwm_ratio:     float = 0.80,
        lwm_ratio:     float = 0.20,
        dlq_retention: int   = 5_000,
        dlq_ttl_secs:  int   = 0,
    ) -> None:
        from vorte._vorte_engine import NativeQueue  # PyO3 extension
        self._q = NativeQueue(
            capacity      = capacity,
            hwm_ratio     = hwm_ratio,
            lwm_ratio     = lwm_ratio,
            dlq_retention = dlq_retention,
            dlq_ttl_secs  = dlq_ttl_secs,
        )

    # ── enqueue ──────────────────────────────────────────────────────────────

    async def enqueue(self, payload: JobPayload) -> str:
        data = _payload_to_bytes(payload.payload)
        result = self._q.enqueue(
            id           = payload.id,
            queue        = payload.queue,
            job_class    = payload.class_name,
            payload      = data,
            priority     = _priority_name(payload.priority),
            max_attempts = payload.max_attempts,
            retry_delay  = float(payload.retry_delay),
            run_at       = payload.run_at,
            trace_id     = payload.trace_id or None,
            attempts     = payload.attempts,
        )
        status = result["status"]
        if status == "full":
            raise QueueFullError(
                f"Queue '{payload.queue}' is at capacity — job {payload.id} rejected"
            )
        if status == "backpressure":
            logger.warning(
                "Queue '%s' above high-watermark (job %s accepted but apply backpressure)",
                payload.queue, payload.id,
            )
        return result["id"]

    # ── dequeue (batch) ───────────────────────────────────────────────────────

    async def dequeue(
        self,
        queues: List[str],
        count:  int = 1,
    ) -> List[JobPayload]:
        """Return up to *count* highest-priority jobs from the given queues."""
        raw_jobs = self._q.dequeue(queues, count)
        return [_raw_to_job_payload(r) for r in raw_jobs]

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def complete(self, job_id: str, result: Any = None) -> None:
        self._q.complete(job_id)

    async def fail(self, job_id: str, error: str) -> None:
        self._q.fail(job_id, error)

    # ── dead-letter ───────────────────────────────────────────────────────────

    async def get_failed(
        self,
        queue_name: str = "default",
        limit:      int = 50,
    ) -> List[JobPayload]:
        raw = self._q.get_dead_letter(queue_name, limit)
        return [_raw_to_job_payload(r) for r in raw]

    async def retry_failed(self, job_id: str) -> bool:
        return self._q.retry_dead_letter(job_id)

    async def purge_expired_dlq(self) -> int:
        """Remove TTL-expired DLQ entries.  Returns count removed."""
        return self._q.purge_expired_dlq()

    # ── stats / watermark ─────────────────────────────────────────────────────

    async def stats(self) -> Dict[str, Any]:
        return self._q.stats()

    async def size(self, queue_name: str = "default") -> int:
        return self._q.size(queue_name)

    async def watermark_state(self) -> Dict[str, str]:
        """Return {'queue_name': 'normal'|'high'|'full'} per queue."""
        return self._q.watermark_state()

    async def queue_backpressure(self, queue_name: str = "default") -> str:
        """Return the backpressure state for a single queue."""
        return self._q.queue_backpressure(queue_name)

    # ── per-queue config override ─────────────────────────────────────────────

    def configure_queue(
        self,
        queue_name:    str,
        capacity:      int,
        hwm_ratio:     float = 0.80,
        lwm_ratio:     float = 0.20,
        dlq_retention: int   = 5_000,
        dlq_ttl_secs:  int   = 0,
    ) -> None:
        """Override capacity/watermark/DLQ config for a specific queue.

        Must be called *before* the first enqueue to that queue.
        """
        self._q.configure_queue(
            queue_name    = queue_name,
            capacity      = capacity,
            hwm_ratio     = hwm_ratio,
            lwm_ratio     = lwm_ratio,
            dlq_retention = dlq_retention,
            dlq_ttl_secs  = dlq_ttl_secs,
        )

    def promote_scheduled(self) -> int:
        """Manually promote scheduled jobs whose run_at has arrived."""
        return self._q.promote_scheduled()


# ── QueueManager ─────────────────────────────────────────────────────────────

class QueueManager:
    """
    Manages background job queues.

    The backend is always ``RustQueueBackend`` (Rust crossbeam channels via
    PyO3).  Set ``driver="redis"`` to use the async Redis STREAM helper
    alongside the in-process engine (future: full Redis-only mode).

    Args:
        driver:        ``"rust"`` (default) or ``"redis"``.
        capacity:      Hard channel capacity per queue.
        hwm_ratio:     High-watermark fraction (0.0–1.0).
        lwm_ratio:     Low-watermark fraction  (0.0–1.0).
        dlq_retention: Max entries per dead-letter queue.
        dlq_ttl_secs:  DLQ TTL in seconds (0 = keep forever up to retention).
    """

    def __init__(
        self,
        driver:        str   = "rust",
        capacity:      int   = 65_536,
        hwm_ratio:     float = 0.80,
        lwm_ratio:     float = 0.20,
        dlq_retention: int   = 5_000,
        dlq_ttl_secs:  int   = 0,
    ) -> None:
        self._driver = driver
        # RustQueueBackend is always the in-process engine.
        # "redis" driver is noted for future external consumer support.
        self._backend = RustQueueBackend(
            capacity      = capacity,
            hwm_ratio     = hwm_ratio,
            lwm_ratio     = lwm_ratio,
            dlq_retention = dlq_retention,
            dlq_ttl_secs  = dlq_ttl_secs,
        )
        self._running = False

    # ── enqueue ──────────────────────────────────────────────────────────────

    async def enqueue(self, job_class: Type[Job], **kwargs: Any) -> str:
        """Enqueue a job class with keyword parameters."""
        from vorte.core.tracing import get_trace_id
        payload = job_class.build_payload(**kwargs)
        payload.trace_id = get_trace_id()
        return await self._backend.enqueue(payload)

    async def enqueue_raw(self, payload: JobPayload) -> str:
        """Enqueue a pre-built JobPayload (e.g. from scheduler or retry)."""
        from vorte.core.tracing import get_trace_id
        if not getattr(payload, "trace_id", ""):
            payload.trace_id = get_trace_id()
        return await self._backend.enqueue(payload)

    # ── dequeue (single, for backwards compatibility) ─────────────────────────

    async def dequeue(self, queues: List[str]) -> Optional[JobPayload]:
        """Dequeue the single highest-priority job from the given queues."""
        results = await self._backend.dequeue(queues, count=1)
        return results[0] if results else None

    async def dequeue_batch(
        self,
        queues: List[str],
        count:  int = 10,
    ) -> List[JobPayload]:
        """Batch-dequeue up to *count* jobs from the given queues."""
        return await self._backend.dequeue(queues, count=count)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def complete(self, job_id: str, result: Any = None) -> None:
        await self._backend.complete(job_id, result)

    async def fail(self, job_id: str, error: str) -> None:
        await self._backend.fail(job_id, error)

    # ── dead-letter ───────────────────────────────────────────────────────────

    async def stats(self) -> Dict[str, Any]:
        return await self._backend.stats()

    async def get_failed_jobs(
        self,
        queue_name: str = "default",
    ) -> List[Dict]:
        jobs = await self._backend.get_failed(queue_name)
        return [j.to_dict() for j in jobs]

    async def retry_failed(self, job_id: str) -> bool:
        return await self._backend.retry_failed(job_id)

    async def purge_expired_dlq(self) -> int:
        return await self._backend.purge_expired_dlq()

    # ── backpressure ──────────────────────────────────────────────────────────

    async def watermark_state(self) -> Dict[str, str]:
        """Return {'queue_name': 'normal'|'high'|'full'} for all queues."""
        return await self._backend.watermark_state()

    async def queue_backpressure(self, queue_name: str = "default") -> str:
        return await self._backend.queue_backpressure(queue_name)

    # ── config ────────────────────────────────────────────────────────────────

    def configure_queue(self, queue_name: str, **kwargs: Any) -> None:
        """Override per-queue config (must be called before first enqueue)."""
        self._backend.configure_queue(queue_name, **kwargs)

    def get_backend(self) -> RustQueueBackend:
        return self._backend


# Re-export Priority alias for module.py import compatibility
Priority = JobPriority
