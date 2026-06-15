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


class VorteConsoleFormatter(logging.Formatter):
    """Formats log records in a clean, human-readable console format for development."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created))
        level = record.levelname
        logger_name = record.name
        message = record.getMessage()

        # Simple ANSI color coding for terminal
        colors = {
            "DEBUG": "\033[36m",    # Cyan
            "INFO": "\033[32m",     # Green
            "WARNING": "\033[33m",  # Yellow
            "ERROR": "\033[31m",    # Red
            "CRITICAL": "\033[41;37m" # Red background, white text
        }
        reset = "\033[0m"
        color = colors.get(level, "")

        # Format level to be fixed width
        level_str = f"{color}{level:<8}{reset}" if color else f"{level:<8}"

        formatted = f"[{timestamp}] {level_str} [{logger_name}] {message}"

        # Check if record has HTTP attributes like method, path, status_code, latency_ms
        if hasattr(record, "method") and hasattr(record, "path"):
            status_code = getattr(record, "status_code", "")
            latency = getattr(record, "latency_ms", "")
            
            # Simple color for status code
            status_color = ""
            if isinstance(status_code, int):
                if status_code < 300:
                    status_color = "\033[32m" # Green
                elif status_code < 400:
                    status_color = "\033[34m" # Blue
                elif status_code < 500:
                    status_color = "\033[33m" # Yellow
                else:
                    status_color = "\033[31m" # Red

            status_str = f"{status_color}{status_code}{reset}" if status_color else str(status_code)
            latency_str = f"{latency}ms" if latency else ""
            
            parts = []
            if status_str:
                parts.append(f"Status: {status_str}")
            if latency_str:
                parts.append(f"Latency: {latency_str}")
                
            if parts:
                formatted += f" | {' | '.join(parts)}"

        # Handle exception if present
        if record.exc_info and record.exc_info[1]:
            exc_text = self.formatException(record.exc_info)
            formatted += f"\n{exc_text}"
            
        return formatted


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
        
        from vorte.core.config import settings
        handler = logging.StreamHandler(sys.stdout)
        if settings.is_production():
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(VorteConsoleFormatter())
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

        # Update log formatters to VorteConsoleFormatter in development for all handlers
        from vorte.core.config import settings
        if not settings.is_production():
            console_formatter = VorteConsoleFormatter()
            for name in ("", "vorte", "uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
                lg = logging.getLogger(name)
                for h in lg.handlers:
                    if isinstance(h, logging.StreamHandler):
                        h.setFormatter(console_formatter)

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

    async def on_startup(self) -> None:
        # Re-apply formatter during startup to catch any handlers uvicorn added
        from vorte.core.config import settings
        if not settings.is_production():
            console_formatter = VorteConsoleFormatter()
            for name in ("", "vorte", "uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
                lg = logging.getLogger(name)
                for h in lg.handlers:
                    if isinstance(h, logging.StreamHandler):
                        h.setFormatter(console_formatter)

    @property
    def log(self) -> Logger:
        return logger
