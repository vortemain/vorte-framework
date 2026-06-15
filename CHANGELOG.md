# Changelog

All notable changes to the Vorte Framework are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.4] - 2026-06-15

### Fixed

- **Version Synchronization** — synchronized hardcoded framework version numbers across CLI, application core, and documentation to align with PyPI release.

## [1.2.3] - 2026-06-15

### Fixed

- **Auth Route Fix** — resolved `AttributeError` in `POST /auth/register` caused by schema mismatch on user creation.

## [1.2.2] - 2026-06-15

### Fixed

- **Developer Log Terminal Flooding** — added `VorteConsoleFilter` to filter out internal framework/dashboard log records (such as uvicorn logs for `/_vorte/dashboard/logs` and health probes) from stdout and the in-memory ring buffer, resolving the issue where developer route logs (like register/login) were pushed out.

## [1.2.1] - 2026-06-15

### Fixed

- **Dashboard Redirection** — refined base `/vorte/dashboard` redirection to clean trailing-slash `/vorte/dashboard/` route without exposing `/index.html` in browser address bar; also intercepted and redirected direct `/vorte/dashboard/index.html` requests
- **Console Log Refinement** — hidden start-up registration logs for standard modules (Cache, Storage, AI, etc.) in development mode by setting them to `DEBUG` level
- **Development Console Formatter** — replaced raw JSON lines in development mode with a human-readable, colorized `VorteConsoleFormatter` text output
- **Startup URL Logger** — printed absolute Admin Dashboard URL (including security tokens) inside lifespan startup hooks for direct clicking

### Added

- Documentation for the new `Dashboard` and `Logging` modules on the `vorte-website` docs page

## [1.2.0] - 2026-06-15

### Added

- **Admin Dashboard** — complete Vanilla JS SPA dashboard with 6 panels (Overview, Modules, Routes, Health, Logs, Config)
  - Token-secured access: token generated per server start, printed to console, stored in `sessionStorage`
  - **Overview panel**: live module/route/request/error stats, CPU & memory ring gauges, recent request table, app info card
  - **Modules panel**: searchable cards with name, version, state indicator, description, and priority
  - **Routes panel**: full route table with method filter buttons (ALL/GET/POST/PUT/PATCH/DELETE) and text search
  - **Health panel**: per-module health cards with manual refresh button
  - **Logs panel**: live-streaming terminal that captures all Python log output (uvicorn, FastAPI, application code) via `RingBufferHandler`; supports level filter + text search + clear
  - **Config panel**: formatted JSON config viewer with Copy JSON button
  - Live uptime counter — ticks every second client-side (no polling required for accuracy)
  - Dark-mode design with glassmorphism cards, ring gauge animations, and micro-interactions
  - Serves `logo-dark.png` from `/_vorte/assets/logos/`
- **`RingBufferHandler`** in `vorte.modules.logging` — captures all Python log records into an in-memory `deque(maxlen=1000)` for dashboard streaming
  - Attaches to root logger and directly to `uvicorn`, `uvicorn.access`, `uvicorn.error` (which set `propagate=False`)
  - Built-in deduplication via `LogRecord` object identity — zero duplicate entries even across overlapping logger hierarchies
  - Lazy re-attachment on first dashboard `/logs` request — works even without `LoggingModule` registered

### Fixed

- **ReDoc blank page** — replaced FastAPI's `get_redoc_html()` (uses dead `redoc@next` jsDelivr tag → 404) with a hand-rolled `HTMLResponse` pinned to `redoc@2.1.5`
- **Routes API metadata** — `get_routes()` now always returns valid `module` and `handler` strings (previously returned `undefined`)
- **`LoggingModule.register()`** — no longer replaces the module-level `logger` instance (which would orphan the ring buffer and lose all log history captured before registration)
- **`logger` export** — `vorte.modules.logging` now correctly exports `logger` in `__all__`

### Improved

- Dashboard uptime counter now ticks every second on the client (seeded from server value + `Date.now()` anchor) — no more 3.5-second jumps
- `formatUptime()` now shows full precision: `"1h 2m 15s"` instead of truncating seconds for multi-hour uptimes
- Topbar layout stabilized (`flex-shrink: 0`) — no longer stretches when switching tabs
- CPU/memory gauge cards use `min-height` — text labels no longer clip

## [1.0.8] - 2026-05-20

### Added

- Vorte runtime kernel evolution with bucketed memory pools
- Zero-copy buffer protocol for serialization
- Structured concurrency with `VorteTaskGroup` and `PyCancellationToken`
- Native Prometheus metrics endpoint at `/_vorte/metrics`
- Compiled DAG execution graph engine (`PyExecutionGraph`)
- Multi-format serialization: JSON, MessagePack, CBOR, Protobuf
- Buffer pooling with RAII buffer return (4-bucket: 4KB, 16KB, 64KB, 256KB)

## [1.0.7] - 2026-05-18

### Added

- `FastSerializer` with automatic backend selection (native > orjson > stdlib)
- `@lazy_schema` decorator for deferred Pydantic validation
- `_LazyPayload` wrapper with zero-copy raw access and cached validation
- Database performance mode (`@performance_mode` decorator)
- `PreparedSQLManager` for prepared SQL statement management
- Benchmark utility for serialization performance testing

## [1.0.6] - 2026-05-15

### Fixed

- Router duplicate `kwargs` parameter in route registration
- Planner list type annotations for proper type checking

## [1.0.5] - 2026-05-13

### Added

- Stable engine compilation with maturin build system
- Look-ahead query planner for automatic N+1 detection
- `@select_related` decorator for manual eager loading
- `N1Detector` with configurable threshold
- `QueryPlanner` with SQLAlchemy `selectinload` integration
- `VorteAPIRoute` with automatic relationship inference from Pydantic models

## [1.0.0] - 2026-05-10

### Added

- Initial release of Vorte Framework
- Core application class (`Vorte`) with ASGI 3.0 support
- Module system with priority-based initialization and dependency validation
- 21 built-in modules:
  - AI (OpenAI, Anthropic, Gemini, Mistral) with cost tracking and routing
  - Agents (tools, memory, RAG, pipelines, guardrails, prompts)
  - Auth (JWT, OAuth, API keys, RBAC, MFA, sessions)
  - Cache (4-layer: L1 memory, L2 Redis, L3 CDN, L4 database)
  - Database (SQLAlchemy async, N+1 detection, query planning)
  - Queue (priority, backpressure, dead letter queue, retry)
  - Storage (local filesystem, AWS S3)
  - Search (MeiliSearch, pgvector)
  - Mailer (SMTP)
  - M-Pesa (STK Push, C2B, B2C, B2B)
  - Payments (Stripe, Paystack)
  - Notifications (in-app, email, push, SMS)
  - Security (Helmet, CSRF, XSS, rate limiting, bot detection)
  - Webhooks (HMAC signing, retry, delivery logs)
  - Sockets (WebSocket with rooms and broadcasting)
  - GraphQL (auto-schema, playground, subscriptions)
  - Multi-Tenancy (subdomain, header, path, JWT resolution)
  - Feature Flags (boolean, percentage rollout, targeting, A/B testing)
  - i18n (translation files, interpolation, locale detection)
  - Logging (structured JSON logging)
  - Dashboard (Next.js admin panel)
- Dependency injection container with singleton, request, and transient scopes
- `@wire` decorator for compile-time graph wiring
- API versioning with URL and header strategies
- Route deprecation with Sunset and Link headers
- Standard response envelope with success/data/meta/ai/error/pagination
- `VorteSSEResponse` for Server-Sent Events
- `VorteStreamResponse` for zero-copy raw streaming
- `VorteExecutor` work-stealing thread pool with `@safe_route` decorator
- `WasmSandbox` for isolated WebAssembly execution
- `TypeMirror` for automatic TypeScript interface generation
- CLI with 30+ commands (new, serve, routes, make:module, migrate, etc.)
- Testing framework (`VorteTestClient`, `AIMocker`, `MpesaMocker`, `VorteTestCase`)
- Rust native engine with 8 crates:
  - `vorte-http` -- Zero-copy HTTP types
  - `vorte-router` -- Radix tree router
  - `vorte-core` -- Hyper/Tokio server
  - `vorte-py` -- PyO3 ASGI bridge
  - `vorte-scheduler` -- Priority task scheduler
  - `vorte-queue` -- Async queue engine
  - `vorte-serde` -- Multi-format serialization
  - `vorte-graph` -- DAG execution graph
- Kubernetes health probes (`/health`, `/ready`, `/live`)
- Built-in admin dashboard with real-time monitoring
- Docker and Kubernetes manifest generation
- CI/CD with GitHub Actions (3-platform wheel build + PyPI publish)
- MIT License

[1.2.4]: https://github.com/vortemain/vorte-framework/releases/tag/v1.2.4
[1.2.3]: https://github.com/vortemain/vorte-framework/releases/tag/v1.2.3
[1.2.2]: https://github.com/vortemain/vorte-framework/releases/tag/v1.2.2
[1.2.1]: https://github.com/vortemain/vorte-framework/releases/tag/v1.2.1
[1.2.0]: https://github.com/vortemain/vorte-framework/releases/tag/v1.2.0
[1.0.8]: https://github.com/Lijohtech-Developers/vorte-framework/releases/tag/v1.0.8
[1.0.7]: https://github.com/Lijohtech-Developers/vorte-framework/releases/tag/v1.0.7
[1.0.6]: https://github.com/Lijohtech-Developers/vorte-framework/releases/tag/v1.0.6
[1.0.5]: https://github.com/Lijohtech-Developers/vorte-framework/releases/tag/v1.0.5
[1.0.0]: https://github.com/Lijohtech-Developers/vorte-framework/releases/tag/v1.0.0
