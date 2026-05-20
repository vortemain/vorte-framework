"""
Vorte Request Tracing
======================
Thread-safe trace ID propagation using contextvars.
"""

from __future__ import annotations

from contextvars import ContextVar
import uuid

# Context-local storage for request/trace ID
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def get_trace_id() -> str:
    """Get the active request trace ID for the current async/thread context."""
    return trace_id_var.get()


def set_trace_id(trace_id: str):
    """Set the active request trace ID for the current context."""
    return trace_id_var.set(trace_id)


def reset_trace_id(token) -> None:
    """Reset the active request trace ID to its previous state."""
    trace_id_var.reset(token)


def generate_trace_id() -> str:
    """Generate a unique request trace ID."""
    return f"req_{uuid.uuid4().hex[:16]}"
