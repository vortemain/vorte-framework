# Built-in API Endpoints

Vorte automatically registers several built-in endpoints on every application.

## Documentation UI

When `debug=True` (the default in development), Vorte serves both Swagger UI and ReDoc:

| URL | UI |
|-----|-----|
| `/docs` | Swagger UI — interactive API explorer |
| `/redoc` | ReDoc — clean reference documentation |
| `/openapi.json` | Raw OpenAPI 3.x specification |

> **ReDoc note**: Vorte pins the ReDoc bundle to `redoc@2.1.5` instead of using FastAPI's default `redoc@next` tag (which is no longer available on jsDelivr and causes a blank page).

---

## Health & Probes

### `GET /health`

Full module health check. Used as a Kubernetes startup probe.

```json
{
  "success": true,
  "data": {
    "status": "healthy",
    "modules": {
      "database": {"status": "healthy"},
      "cache": {"status": "healthy", "layers": {...}}
    }
  }
}
```

Returns `200` when all modules are healthy, `503` when any module is degraded.

### `GET /ready`

Kubernetes readiness probe. Returns `200` when the application is ready to accept traffic.

### `GET /live`

Kubernetes liveness probe. Returns `200` when the application process is alive.

---

## Framework Info

### `GET /_vorte/info`

Runtime and framework information.

```json
{
  "success": true,
  "data": {
    "framework": "Vorte",
    "version": "1.2.4",
    "python_version": "3.13.6",
    "platform": "win32",
    "module_count": 5,
    "route_count": 48
  }
}
```

---

## Prometheus Metrics

### `GET /_vorte/metrics`

Prometheus-formatted metrics (populated by the native Rust engine when available):

```
vorte_serialization_time_ns 5100
vorte_database_wait_time_ns 2300
vorte_scheduling_latency_ns 1800
vorte_event_loop_lag_ns 450
vorte_buffered_spans_total 42
vorte_metrics_buffer_capacity_total 10000
```

---

## Dashboard API

All dashboard endpoints require authentication via `X-Dashboard-Token: <token>` header (or `Authorization: Bearer <token>`).

The token is printed to stdout on each server startup.

### `GET /_vorte/dashboard/overview`

Complete live overview including:
- Framework name, version, uptime seconds, env, debug mode, API prefix, PID
- Module count (healthy / total)
- Route count
- Request metrics (total, errors, last 8 requests with method/path/status/latency)
- System info (Python version, platform, PID)

### `GET /_vorte/dashboard/modules`

All registered modules with:
- Name, version, description
- Current state (`ready`, `failed`, etc.)
- Priority level

### `GET /_vorte/dashboard/routes`

All registered routes with:
- HTTP method (GET, POST, PUT, PATCH, DELETE, etc.)
- Path
- Owning module name
- Handler function name

### `GET /_vorte/dashboard/health`

Per-module health check results:
- Overall status (`healthy` / `degraded`)
- Per-module status with description

### `GET /_vorte/dashboard/logs`

Last 1000 log entries from the in-memory ring buffer. Entries include:
- `level` — log level string (`INFO`, `WARNING`, `ERROR`, etc.)
- `timestamp` — ISO 8601 UTC
- `message` — log message
- `logger` — originating logger name
- Optional HTTP fields: `method`, `path`, `status_code`, `latency_ms`, `request_id`

### `GET /_vorte/dashboard/config`

Non-sensitive configuration dump grouped by module. Sensitive fields (keys, secrets, passwords, tokens) are masked as `"***"`.
