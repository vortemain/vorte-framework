# Dashboard Module

The Dashboard Module is a built-in, token-secured, zero-dependency admin panel served directly by Vorte — no separate build step required. It gives real-time insight into every aspect of your running application.

## Setup

```python
from vorte import Vorte, DashboardModule

app = Vorte()
app.register(DashboardModule())
```

`DashboardModule` is auto-registered when using `Vorte(auto_load=True)`.

## Accessing the Dashboard

When the server starts, a security token is printed to your console:

```
======================================================================
[Vorte DX] Security Warning: Dashboard is secured. Access it using token:
  Token: <your-token>
  URL: /vorte/dashboard?token=<your-token>
======================================================================
```

Open `http://localhost:8000/vorte/dashboard?token=<your-token>` in a browser.

The token is stored in `sessionStorage` after the first visit — you don't need to pass it on every page load within the same session.

> **Note**: In development, the token is printed to stdout. In production, record it from the startup log and keep it secure.

## Dashboard Panels

### Overview

The landing panel. Provides a live summary of:

| Metric | Description |
|--------|-------------|
| **Modules** | `healthy / total` count |
| **Routes** | Total registered routes |
| **Requests** | Lifetime request count |
| **Error Rate** | `errors / total` as a percentage |
| **Uptime** | Live counter (ticks every second — client-side) |
| **CPU / Memory gauges** | Visual ring gauges for process load |
| **Recent Requests** | Last 8 HTTP requests with method, path, status, latency |
| **App Info** | Name, env, API prefix, Python version, platform, PID, debug mode |

### Modules

Cards for every registered module showing name, version, state (`Active` / `Unregistered`), description, and initialization priority.

Supports live text search to filter modules by name or description.

### Routes

Full route table with method badge, path, owning module, and handler name.

Supports:
- **Text search** — filter by path or handler name
- **Method filter buttons** — `ALL | GET | POST | PUT | PATCH | DELETE`

### Health

Runs health checks across all registered modules and displays:
- Overall status badge (`HEALTHY` / `DEGRADED`)
- Per-module health card with icon, description, and status badge
- Manual **Refresh** button

### Logs

Live-streaming log terminal. All Python log records — from uvicorn, FastAPI, and your application code — are captured into an in-memory ring buffer (last 1000 entries) and streamed to the panel every 3 seconds.

Supports:
- **Text search** — filter log messages
- **Level filter buttons** — `ALL | DEBUG | INFO | WARNING | ERROR | CRITICAL`
- **Clear terminal** button

> **How logs work**: A `RingBufferHandler` is attached to the root Python logger and directly to `uvicorn`, `uvicorn.access`, and `uvicorn.error` loggers (which set `propagate=False`). This ensures 100% capture even when `LoggingModule` is not explicitly registered.
> 
> Deduplication is built-in — the same `LogRecord` object can only appear once in the buffer, even if it propagates through multiple logger levels.

### Config

Displays a formatted JSON dump of non-sensitive configuration grouped by module. Includes a **Copy JSON** button.

## Dashboard API

All endpoints require the `X-Dashboard-Token` header or `Authorization: Bearer <token>`:

| Endpoint | Description |
|----------|-------------|
| `GET /_vorte/dashboard/overview` | Live stats: modules, routes, metrics, uptime, system info |
| `GET /_vorte/dashboard/modules` | All registered modules with state and metadata |
| `GET /_vorte/dashboard/routes` | All registered routes with method, path, module, handler |
| `GET /_vorte/dashboard/health` | Health check results for all modules |
| `GET /_vorte/dashboard/logs` | Last 1000 log entries from the ring buffer |
| `GET /_vorte/dashboard/config` | Non-sensitive configuration dump |

Static assets are served at `/vorte/dashboard/*` (CSS, JS, logo).

## Security

- Every API request requires a `X-Dashboard-Token` header matching the server token
- The token is generated fresh on each server startup
- The SPA strips the token from the URL after first load (stored in `sessionStorage`)
- Dashboard routes are excluded from public API schemas

## Technology Stack

The dashboard is a self-contained Vanilla JS SPA bundled directly into the module's `static/` directory:

| File | Role |
|------|------|
| `index.html` | Single-page app shell |
| `dashboard.css` | Dark-mode design system with CSS variables |
| `dashboard.js` | All fetch logic, polling, rendering, and state management |

No build tool, no bundler, no external UI framework required. The only external resources loaded at runtime are:
- [Google Fonts (Inter)](https://fonts.google.com)
- [Font Awesome 6 (icons)](https://fontawesome.com) via CDN
