"""
Vorte Queue - Base Job Class
=============================
Abstract base class for all background jobs in the Vorte queue system.

Jobs define the unit of work that can be dispatched to a queue, processed
by a worker, and optionally scheduled on a cron expression.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any, ClassVar, Dict, List, Optional, Type


class JobPriority(IntEnum):
    """Job priority levels (higher = processed sooner)."""
    LOW = 0
    DEFAULT = 5
    HIGH = 10
    CRITICAL = 20


@dataclass
class JobPayload:
    """
    Serializable payload representing a queued job.

    This is what gets stored in the backing store (Redis, database, etc.)
    and transferred to worker processes.
    """
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    class_name: str = ""
    queue: str = "default"
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: int = JobPriority.DEFAULT
    retries: int = 3
    retry_delay: int = 5
    attempts: int = 0
    max_attempts: int = 3
    scheduled_at: Optional[float] = None  # Unix timestamp
    run_at: Optional[float] = None  # Unix timestamp (for scheduled jobs)
    timeout: int = 300  # Job timeout in seconds
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    failed_at: Optional[float] = None
    error: Optional[str] = None
    status: str = "pending"  # pending, running, completed, failed, cancelled
    trace_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the payload to a dictionary."""
        return {
            "id": self.id,
            "class_name": self.class_name,
            "queue": self.queue,
            "payload": self.payload,
            "priority": self.priority,
            "retries": self.retries,
            "retry_delay": self.retry_delay,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "scheduled_at": self.scheduled_at,
            "run_at": self.run_at,
            "timeout": self.timeout,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "failed_at": self.failed_at,
            "error": self.error,
            "status": self.status,
            "trace_id": self.trace_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> JobPayload:
        """Deserialize a payload from a dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class Job(ABC):
    """
    Abstract base class for background jobs.

    Subclass this to create a job that can be dispatched to the queue.

    Class Attributes:
        queue: Name of the queue this job belongs to (default ``"default"``).
        retries: Maximum number of retry attempts on failure.
        retry_delay: Seconds to wait between retries (exponential backoff applied).
        priority: Job priority level.
        timeout: Maximum execution time in seconds.
        schedule: Optional cron expression for scheduled execution.

    Usage:
        class SendWelcomeEmail(Job):
            queue = "emails"
            retries = 3
            timeout = 30

            async def handle(self, user_id: int, email: str):
                await mailer.send_welcome(email)
                ...

        # Dispatch the job
        await SendWelcomeEmail.dispatch(user_id=42, email="alice@example.com")
    """

    # ---- Class-level defaults (override in subclasses) ----
    queue: str = "default"
    retries: int = 3
    retry_delay: int = 5
    priority: int = JobPriority.DEFAULT
    timeout: int = 300
    schedule: Optional[str] = None  # Cron expression

    def __init__(self):
        self._id: Optional[str] = None

    @abstractmethod
    async def handle(self, **kwargs: Any) -> Any:
        """
        Execute the job's logic.

        This method is called by the worker when the job is dequeued.
        All job-specific arguments are passed as keyword arguments.

        Args:
            **kwargs: Job-specific arguments passed during dispatch.

        Returns:
            Optional result value.

        Raises:
            Exception: If the job fails, it will be retried up to ``retries`` times.
        """
        ...

    @classmethod
    def build_payload(cls, **kwargs: Any) -> JobPayload:
        """
        Build a JobPayload for this job class.

        Args:
            **kwargs: Arguments to pass to ``handle()`` when the job runs.

        Returns:
            A JobPayload ready to be enqueued.
        """
        return JobPayload(
            class_name=f"{cls.__module__}.{cls.__qualname__}",
            queue=cls.queue,
            payload=kwargs,
            priority=cls.priority,
            retries=cls.retries,
            retry_delay=cls.retry_delay,
            max_attempts=cls.retries + 1,
            timeout=cls.timeout,
        )

    @classmethod
    async def dispatch(cls, **kwargs: Any) -> str:
        """
        Dispatch this job to the queue for async processing.

        Uses the global QueueManager from the DI container to enqueue the job.

        Args:
            **kwargs: Arguments to pass to ``handle()`` when the job runs.

        Returns:
            The job ID.

        Raises:
            RuntimeError: If no QueueManager is registered in the DI container.
        """
        try:
            from vorte.core.di import _global_container
            from .queue import QueueManager
            manager = _global_container.resolve(QueueManager)
        except (KeyError, AttributeError):
            raise RuntimeError(
                "No QueueManager registered. Ensure QueueModule is registered "
                "with the Vorte app before dispatching jobs."
            )

        return await manager.enqueue(cls, **kwargs)

    @classmethod
    async def dispatch_later(cls, delay: int, **kwargs: Any) -> str:
        """
        Dispatch this job to run after a delay.

        Args:
            delay: Delay in seconds before the job should run.
            **kwargs: Arguments to pass to ``handle()``.

        Returns:
            The job ID.
        """
        try:
            from vorte.core.di import _global_container
            from .queue import QueueManager
            manager = _global_container.resolve(QueueManager)
        except (KeyError, AttributeError):
            raise RuntimeError(
                "No QueueManager registered. Ensure QueueModule is registered "
                "with the Vorte app before dispatching jobs."
            )

        import time
        payload = cls.build_payload(**kwargs)
        payload.run_at = time.time() + delay
        return await manager.enqueue_raw(payload)

    @classmethod
    def get_class_name(cls) -> str:
        """Return the fully-qualified class name for serialization."""
        return f"{cls.__module__}.{cls.__qualname__}"


# ---- Registry of all known job classes ----
_job_registry: Dict[str, Type[Job]] = {}


def register_job(job_class: Type[Job]) -> Type[Job]:
    """
    Decorator to register a job class in the global job registry.

    This allows the worker to resolve class names back to actual classes
    when deserializing payloads.

    Usage:
        @register_job
        class SendWelcomeEmail(Job):
            ...
    """
    _job_registry[job_class.get_class_name()] = job_class
    return job_class


def resolve_job_class(class_name: str) -> Optional[Type[Job]]:
    """
    Resolve a fully-qualified class name to a Job subclass.

    Args:
        class_name: Fully-qualified class name (e.g., ``"app.jobs.SendEmail"``).

    Returns:
        The Job subclass, or None if not found.
    """
    if class_name in _job_registry:
        return _job_registry[class_name]

    # Try dynamic import
    try:
        parts = class_name.rsplit(".", 1)
        if len(parts) == 2:
            module_path, class_attr = parts
            import importlib
            module = importlib.import_module(module_path)
            job_cls = getattr(module, class_attr)
            _job_registry[class_name] = job_cls
            return job_cls
    except (ImportError, AttributeError):
        pass

    return None
