"""
Tests for Vorte Queue Module (Rust-native channel-driven queue engine).
"""

import asyncio
import time
import pytest
from pydantic import BaseModel

from vorte.modules.queue.job import Job, JobPriority, JobPayload, register_job
from vorte.modules.queue.queue import QueueManager, QueueFullError
from vorte.modules.queue.worker import Worker


# ── Test Jobs ─────────────────────────────────────────────────────────────────

@register_job
class SimpleTestJob(Job):
    queue = "test-default"
    priority = JobPriority.DEFAULT
    retries = 3

    async def handle(self, x: int, y: int) -> int:
        return x + y


@register_job
class HighPriorityJob(Job):
    queue = "test-default"
    priority = JobPriority.HIGH

    async def handle(self, msg: str) -> str:
        return msg


@register_job
class FailJob(Job):
    queue = "test-fail"
    retries = 1
    retry_delay = 1

    async def handle(self, fail_count: int = 1) -> None:
        raise ValueError(f"Job failed intentionally: {fail_count}")


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_queue_basic_operations():
    """Verify simple enqueue, priority dequeue, and completion."""
    # Create an in-process Rust Queue
    qm = QueueManager(capacity=100)
    
    # Enqueue a default priority job
    id1 = await qm.enqueue(SimpleTestJob, x=10, y=20)
    assert id1 is not None

    # Enqueue a high priority job
    id2 = await qm.enqueue(HighPriorityJob, msg="important")
    assert id2 is not None

    # Dequeue single job — High priority should come first
    p1 = await qm.dequeue(queues=["test-default"])
    assert p1 is not None
    assert p1.id == id2
    assert p1.class_name.endswith("HighPriorityJob")
    assert p1.payload == {"msg": "important"}
    assert p1.priority == JobPriority.HIGH

    # Complete it
    await qm.complete(p1.id)

    # Dequeue the second job
    p2 = await qm.dequeue(queues=["test-default"])
    assert p2 is not None
    assert p2.id == id1
    assert p2.payload == {"x": 10, "y": 20}
    assert p2.priority == JobPriority.DEFAULT

    # Complete it
    await qm.complete(p2.id)

    # Dequeue again — should be empty
    p3 = await qm.dequeue(queues=["test-default"])
    assert p3 is None


@pytest.mark.asyncio
async def test_queue_batch_dequeue():
    """Verify batch dequeue gathers multiple jobs efficiently in priority order."""
    qm = QueueManager(capacity=100)

    # Enqueue 5 jobs
    ids = []
    for i in range(5):
        job_id = await qm.enqueue(SimpleTestJob, x=i, y=i)
        ids.append(job_id)

    # Batch dequeue 3 jobs
    batch = await qm.dequeue_batch(queues=["test-default"], count=3)
    assert len(batch) == 3
    assert [b.id for b in batch] == ids[:3]

    # Dequeue remaining 2
    batch2 = await qm.dequeue_batch(queues=["test-default"], count=10)
    assert len(batch2) == 2
    assert [b.id for b in batch2] == ids[3:]


@pytest.mark.asyncio
async def test_queue_full_backpressure():
    """Verify that when a queue hits capacity, QueueFullError is raised."""
    # Capacity = 2
    qm = QueueManager(capacity=2)

    # Enqueue 2 jobs successfully
    await qm.enqueue(SimpleTestJob, x=1, y=1)
    await qm.enqueue(SimpleTestJob, x=2, y=2)

    # The 3rd enqueue should fail due to capacity
    with pytest.raises(QueueFullError) as exc_info:
        await qm.enqueue(SimpleTestJob, x=3, y=3)
    assert "at capacity" in str(exc_info.value)


@pytest.mark.asyncio
async def test_watermark_state():
    """Verify watermark states transition correctly under different load levels."""
    # Capacity = 10, HWM = 80%, LWM = 20%
    qm = QueueManager(capacity=10, hwm_ratio=0.8, lwm_ratio=0.2)
    qm.configure_queue("test-default", capacity=10, hwm_ratio=0.8, lwm_ratio=0.2)

    # Initially normal
    wm = await qm.watermark_state()
    assert wm.get("test-default", "normal") == "normal"

    # Enqueue 7 jobs -> still normal (below 8)
    for i in range(7):
        await qm.enqueue(SimpleTestJob, x=i, y=i)
    wm = await qm.watermark_state()
    assert wm.get("test-default", "normal") == "normal"

    # Enqueue 1 more (total 8) -> becomes high (above/equal 8)
    await qm.enqueue(SimpleTestJob, x=7, y=7)
    wm = await qm.watermark_state()
    assert wm.get("test-default", "normal") == "high"

    # Enqueue 2 more (total 10) -> becomes full
    await qm.enqueue(SimpleTestJob, x=8, y=8)
    await qm.enqueue(SimpleTestJob, x=9, y=9)
    wm = await qm.watermark_state()
    assert wm.get("test-default", "normal") == "full"

    # Try to enqueue 11th -> QueueFullError
    with pytest.raises(QueueFullError):
        await qm.enqueue(SimpleTestJob, x=10, y=10)

    # Dequeue batch of 5 -> len drops to 5, which is above LWM (2) but below HWM (8).
    # Note: watermark state is checked based on capacity limits
    await qm.dequeue_batch(queues=["test-default"], count=5)
    wm = await qm.watermark_state()
    # It drops back to normal since length (5) < HWM (8)
    assert wm.get("test-default", "normal") == "normal"


@pytest.mark.asyncio
async def test_dead_letter_queue_and_retry():
    """Verify that jobs failing permanently go to DLQ and can be retried."""
    qm = QueueManager(dlq_retention=10)

    # Enqueue a failing job
    job_id = await qm.enqueue(FailJob, fail_count=1)

    # 1st attempt
    p = await qm.dequeue(queues=["test-fail"])
    assert p is not None
    assert p.id == job_id
    assert p.attempts == 0
    # Simulating worker failing it:
    # Worker increases attempts locally and calls manager.fail()
    p.attempts += 1
    await qm.fail(p.id, "ValueError: first failure")

    # The job is scheduled for retry. Let's manually promote scheduled jobs.
    backend = qm.get_backend()
    # We force promotion by waiting or calling promote_scheduled
    # Let's mock/advance the system time or manually call retry
    backend.promote_scheduled()

    # 2nd attempt
    # Since retry delay is 1, let's sleep a moment and promote
    await asyncio.sleep(1.1)
    backend.promote_scheduled()

    p = await qm.dequeue(queues=["test-fail"])
    assert p is not None
    p.attempts += 1
    # 2nd failure — goes to DLQ since max_attempts = 2 (retries = 1)
    await qm.fail(p.id, "ValueError: second failure")

    # Dequeue again — should be empty (job is failed)
    p_none = await qm.dequeue(queues=["test-fail"])
    assert p_none is None

    # Retrieve failed jobs from DLQ
    failed_jobs = await qm.get_failed_jobs("test-fail")
    assert len(failed_jobs) == 1
    assert failed_jobs[0]["id"] == job_id
    assert failed_jobs[0]["status"] == "failed"
    assert "second failure" in failed_jobs[0]["error"]

    # Retry the failed job
    ok = await qm.retry_failed(job_id)
    assert ok is True

    # DLQ should be empty now
    failed_jobs_after = await qm.get_failed_jobs("test-fail")
    assert len(failed_jobs_after) == 0

    # The job is back in the active queue!
    p_retry = await qm.dequeue(queues=["test-fail"])
    assert p_retry is not None
    assert p_retry.id == job_id
    assert p_retry.attempts == 0  # Resets attempts on retry


@pytest.mark.asyncio
async def test_worker_processing():
    """Verify that Worker successfully runs jobs concurrently and handles outcomes."""
    qm = QueueManager()
    
    # We will spawn a worker
    worker = Worker(
        queues=["test-worker"],
        concurrency=2,
        batch_size=2,
        poll_interval=0.1,
    )

    # Register success/fail tracking
    succeeded = []
    failed = []

    def on_success(payload, result):
        succeeded.append((payload.id, result))

    def on_fail(payload):
        failed.append(payload.id)

    worker._on_success = on_success
    worker._on_failure = on_fail

    # Enqueue a successful job and a failing job
    id_ok = await qm.enqueue(SimpleTestJob, x=5, y=5)
    id_fail = await qm.enqueue(FailJob, fail_count=1)

    # We need to configure FailJob with no retries to fail immediately
    # or just let it fail. We can manually edit max_attempts to 1.
    # Let's change the queue config to only listen to "test-default" and "test-fail"
    worker._queues = ["test-default", "test-fail"]

    # Start the worker task
    worker_task = asyncio.create_task(worker.start(qm))

    # Give it some time to process
    await asyncio.sleep(0.5)

    # Shut down worker
    await worker.shutdown()
    await worker_task

    # Check stats
    stats = worker.stats
    assert stats.jobs_processed >= 2
    assert len(succeeded) >= 1
    assert any(x[0] == id_ok for x in succeeded)
    assert succeeded[0][1] == 10  # 5 + 5
