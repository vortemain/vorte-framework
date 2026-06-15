# Logging Module

Structured JSON logging with automatic request capture, an in-memory ring buffer for the admin dashboard, and zero-config setup.

## Setup

```python
from vorte import Vorte, LoggingModule

app = Vorte()
app.register(LoggingModule(level="INFO"))
```

The module is auto-registered when using `Vorte(auto_load=True)`.

## Usage

```python
from vorte.modules.logging import logger

# Simple message
logger.info("Order created")

# Structured fields (all kwargs become JSON fields)
logger.info("Order created", order_id=123, amount=99.99, user_id="usr_abc")

# Error with context
logger.error("Payment failed", order_id=123, provider="stripe")

# Debug (only shown when level="DEBUG")
logger.debug("Cache miss", key="user:42")
```

## Log Levels

| Method | Level | Use |
|--------|-------|-----|
| `logger.debug()` | `DEBUG` | Diagnostic details |
| `logger.info()` | `INFO` | Informational messages |
| `logger.warning()` | `WARNING` | Unexpected but recoverable |
| `logger.error()` | `ERROR` | Errors that affect a request |
| `logger.critical()` | `CRITICAL` | Fatal system errors |

## Output Format

Every log line is emitted as a single-line JSON object:

```json
{
  "level": "INFO",
  "timestamp": "2026-06-15T07:24:21Z",
  "message": "Order created",
  "logger": "vorte",
  "order_id": 123,
  "amount": 99.99,
  "user_id": "usr_abc"
}
```

HTTP request logs (when `LoggingModule` is registered) include:

```json
{
  "level": "INFO",
  "timestamp": "2026-06-15T07:24:22Z",
  "message": "POST /api/orders",
  "logger": "vorte",
  "method": "POST",
  "path": "/api/orders",
  "status_code": 201,
  "latency_ms": 14,
  "request_id": "req_a1b2c3"
}
```

## Configuration

```python
app.register(LoggingModule(level="INFO"))
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `level` | `str` | `"INFO"` | Minimum log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |

## Request Logging Middleware

When `LoggingModule` is registered, a middleware is automatically added that logs every HTTP request with method, path, status code, and latency.

## Dashboard Log Streaming (Ring Buffer)

All log output — from your code, uvicorn, FastAPI, and any other Python library — is captured in an in-memory ring buffer (last 1000 entries) that powers the **Logs** panel in the Vorte admin dashboard.

### How it works

A `RingBufferHandler` is installed on:
- The **root Python logger** — catches everything that propagates normally
- **`uvicorn`**, **`uvicorn.access`**, **`uvicorn.error`** — added directly because these loggers set `propagate=False`

This means logs appear in the dashboard regardless of which library emits them and regardless of whether `LoggingModule` is explicitly registered.

**Deduplication** is built-in: each `LogRecord` object is tracked by its Python object `id`. If the same record reaches the handler twice (due to overlapping logger hierarchies), the second arrival is silently dropped.

### Accessing logs from the ring buffer

```python
from vorte.modules.logging import logger

# Returns list of log dicts from the ring buffer
recent_logs = logger.get_logs()
```

Each entry is a plain `dict`:

```python
{
    "level": "INFO",
    "timestamp": "2026-06-15T07:24:21Z",
    "message": "CacheModule registered",
    "logger": "vorte.modules.cache",
    # ... optional extra fields
}
```

## Using Without `LoggingModule`

Even if you do not register `LoggingModule`, the module-level `logger` instance is always available:

```python
from vorte.modules.logging import logger

logger.info("This still works")
```

The ring buffer and `RingBufferHandler` are attached at import time — the dashboard Logs panel will capture output as soon as the first `/_vorte/dashboard/logs` request arrives (the handler is lazily re-attached at that point to ensure uvicorn loggers are initialized).

## Log Capture Scope

| Source | Captured |
|--------|----------|
| `logger.info(...)` via Vorte logger | ✅ Always |
| `logging.getLogger("my_lib").info(...)` | ✅ Via root logger |
| uvicorn access log (`GET /api/...`) | ✅ Via direct attachment |
| uvicorn startup/error log | ✅ Via direct attachment |
| FastAPI internal logs | ✅ Via root logger propagation |
| Third-party library logs | ✅ Via root logger propagation |
