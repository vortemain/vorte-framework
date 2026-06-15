"""
Vorte Logging Module
=====================
Structured JSON logging with request context, OpenTelemetry integration,
and zero-config setup.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from fastapi import Request

from vorte.core.module import Module, ModuleMeta, ModulePriority


class JSONFormatter(logging.Formatter):
    """Formats log records as structured JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "level": record.levelname,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
        }
        # Add extra fields
        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.request_id
        if hasattr(record, "user_id"):
            log_entry["user_id"] = record.user_id
        if hasattr(record, "tenant"):
            log_entry["tenant"] = record.tenant
        if hasattr(record, "method"):
            log_entry["method"] = record.method
        if hasattr(record, "path"):
            log_entry["path"] = record.path
        if hasattr(record, "status_code"):
            log_entry["status_code"] = record.status_code
        if hasattr(record, "latency_ms"):
            log_entry["latency_ms"] = record.latency_ms
        if hasattr(record, "extra"):
            log_entry.update(record.extra)
        if record.exc_info and record.exc_info[1]:
            log_entry["error"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }
        return json.dumps(log_entry, default=str)


class RingBufferHandler(logging.Handler):
    """Intercepts Python logging records into the Vorte ring buffer (dedup-safe)."""

    def __init__(self, ring: deque):
        super().__init__()
        self._ring = ring
        # Small LRU set to deduplicate: same LogRecord object may fire this
        # handler multiple times if it was added to both a child and parent logger.
        # We use the record's object id as a key; we keep the last 256 to bound
        # memory without needing a lock (CPython GIL protects the set).
        self._seen: deque = deque(maxlen=256)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            rid = id(record)
            if rid in self._seen:
                return
            self._seen.append(rid)

            entry = {
                "level": record.levelname,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
                "message": record.getMessage(),
                "logger": record.name,
            }
            # Attach known extra HTTP fields if present
            for f in ("method", "path", "status_code", "latency_ms", "request_id"):
                if hasattr(record, f):
                    entry[f] = getattr(record, f)
            self._ring.append(entry)
        except Exception:
            pass


class Logger:
    """Vorte structured logger."""

    def __init__(self, name: str = "vorte", level: str = "INFO"):
        self._logger = logging.getLogger(name)
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        self._logger.handlers.clear()
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        self._logger.addHandler(handler)
        # Prevent propagation to root logger
        self._logger.propagate = False
        
        # In-memory ring buffer for dashboard
        self._log_history: deque = deque(maxlen=1000)
        
        # Install a root-level handler so ALL log records flow into the ring buffer
        self._ring_handler = RingBufferHandler(self._log_history)
        self._ring_handler.setLevel(logging.DEBUG)
        self._attach_ring_handler()

    def _attach_ring_handler(self) -> None:
        """Attach the ring buffer handler to capture all log output.

        Strategy:
        - Root logger catches every record that propagates normally.
        - uvicorn.* loggers are known to set propagate=False after startup,
          so we add the handler directly to them as well.
        - We do NOT add to vorte / fastapi / vorte.core — those propagate
          to root, and adding there would cause duplicates.
        - RingBufferHandler.emit() deduplicates by record object-id anyway,
          so even if the hierarchy changes this is safe.
        """
        handler = self._ring_handler
        # Root logger — catches everything that propagates normally
        root = logging.getLogger()
        if not any(isinstance(h, RingBufferHandler) for h in root.handlers):
            root.addHandler(handler)
        # Uvicorn loggers hard-set propagate=False; add directly to them
        for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
            lg = logging.getLogger(name)
            if not any(isinstance(h, RingBufferHandler) for h in lg.handlers):
                lg.addHandler(handler)


    def get_logs(self):
        """Retrieve recent logs from the buffer."""
        return list(self._log_history)

    def _log(self, level: str, message: str, **kwargs):
        extra = {"extra": kwargs}
        # Move known extra fields to top level for the formatter
        for key in ["request_id", "user_id", "tenant", "method", "path", "status_code", "latency_ms"]:
            if key in kwargs:
                extra[key] = kwargs.pop(key)
                extra["extra"][key] = extra[key]
                
        # Format log entry manually for the ring buffer
        log_entry = {
            "level": level.upper(),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "message": message,
            "logger": self._logger.name,
            **extra.get("extra", {})
        }
        for k in ["request_id", "user_id", "tenant", "method", "path", "status_code", "latency_ms"]:
            if k in extra:
                log_entry[k] = extra[k]
                
        self._log_history.append(log_entry)
        
        getattr(self._logger, level.lower())(message, extra=extra)

    def debug(self, message: str, **kwargs):
        self._log("DEBUG", message, **kwargs)

    def info(self, message: str, **kwargs):
        self._log("INFO", message, **kwargs)

    def warning(self, message: str, **kwargs):
        self._log("WARNING", message, **kwargs)

    def error(self, message: str, **kwargs):
        self._log("ERROR", message, **kwargs)

    def critical(self, message: str, **kwargs):
        self._log("CRITICAL", message, **kwargs)


# Module-level logger instance
logger = Logger()


class LoggingModule(Module):
    """
    Structured JSON logging module.
    
    Usage:
        app.register(LoggingModule())
        
        from vorte.modules.logging import logger
        logger.info('Order created', order_id=123, amount=99.99)
    """

    meta = ModuleMeta(
        name="logging",
        version="1.0.0",
        description="Structured JSON logging with OpenTelemetry integration",
        priority=ModulePriority.CONFIG,
    )

    def __init__(self, *, level: str = "INFO", telemetry: str = ""):
        super().__init__(level=level, telemetry=telemetry)
        self._level = level
        self._telemetry = telemetry

    def register(self, app) -> None:
        global logger
        logger._logger.setLevel(getattr(logging, self._level.upper(), logging.INFO))
        # Re-attach ring buffer handler to all key loggers (uvicorn sets propagate=False late)
        logger._attach_ring_handler()


        # Request logging middleware
        @app.middleware("http")
        async def logging_middleware(request: Request, call_next):
            start = time.time()
            response = await call_next(request)
            latency_ms = int((time.time() - start) * 1000)
            logger.info(
                f"{request.method} {request.url.path}",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                latency_ms=latency_ms,
                request_id=getattr(request.state, "request_id", ""),
            )
            return response

        if hasattr(app, 'container'):
            app.container.register_instance(Logger, logger)

    @property
    def log(self) -> Logger:
        return logger
