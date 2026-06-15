<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/vortemain/vorte-framework/main/assets/logos/logo-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/vortemain/vorte-framework/main/assets/logos/logo-light.png">
    <img alt="Vorte Framework Logo" src="https://raw.githubusercontent.com/vortemain/vorte-framework/main/assets/logos/logo-light.png" width="320">
  </picture>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Rust-Engine-000000?style=for-the-badge&logo=rust&logoColor=white" alt="Rust Engine">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="MIT License">
  <img src="https://img.shields.io/badge/Version-1.2.0-blue?style=for-the-badge" alt="Version 1.2.0">
</p>

# Vorte Framework

**The AI-First, Battery-Included Python API Framework.**

Vorte is a high-performance Python framework built on top of FastAPI, designed for modern web development with first-class AI integration, a real-time admin dashboard, and 21 production-ready modules. It ships with an optional Rust-native engine for zero-copy routing, multi-format serialization, and work-stealing task scheduling.

---

## Table of Contents

- [Key Features](#key-features)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Architecture Overview](#architecture-overview)
- [Core Concepts](#core-concepts)
  - [Application](#application)
  - [Module System](#module-system)
  - [Configuration](#configuration)
  - [Routing & Versioning](#routing--versioning)
  - [Response Envelope](#response-envelope)
  - [Dependency Injection](#dependency-injection)
  - [Serialization](#serialization)
  - [Executor & Concurrency](#executor--concurrency)
- [Built-in Modules (21)](#built-in-modules-21)
  - [AI Module](#ai-module)
  - [Agents Module (Beta)](#agents-module-beta)
  - [Auth Module](#auth-module)
  - [Database Module](#database-module)
  - [Cache Module](#cache-module)
  - [Queue Module](#queue-module)
  - [Storage Module](#storage-module)
  - [Search Module (Beta)](#search-module-beta)
  - [Mailer Module (Beta)](#mailer-module-beta)
  - [M-Pesa Module (Beta)](#mpesa-module-beta)
  - [Payments Module (Beta)](#payments-module-beta)
  - [Notifications Module (Beta)](#notifications-module-beta)
  - [Security Module](#security-module)
  - [Webhooks Module (Beta)](#webhooks-module-beta)
  - [Sockets Module (Beta)](#sockets-module-beta)
  - [GraphQL Module (Beta)](#graphql-module-beta)
  - [Multi-Tenancy Module (Beta)](#multi-tenancy-module-beta)
  - [Feature Flags Module (Beta)](#feature-flags-module-beta)
  - [Internationalization Module](#i18n-module)
  - [Logging Module](#logging-module)
  - [Dashboard Module](#dashboard-module)
- [CLI Reference](#cli-reference)
- [Testing](#testing)
- [Rust Native Engine](#rust-native-engine)
- [Deployment](#deployment)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [Changelog](#changelog)
- [License](#license)

---

## Key Features

- **AI-First Architecture** -- Native multi-provider AI integration (OpenAI, Anthropic, Gemini, Mistral) with cost tracking, streaming, embeddings, and intelligent routing.
- **21 Built-in Modules** -- 10 Core Stable Modules (Auth, Database, Cache, Queue, AI, Storage, i18n, Security, Logging, Dashboard) and 11 Additional Modules in Beta (Agents, Search, Mailer, M-Pesa, Payments, Notifications, Webhooks, Sockets, GraphQL, Multi-Tenancy, Feature Flags).
- **Rust Native Engine** -- Optional zero-copy radix tree router, work-stealing executor, priority task scheduler, multi-format serialization (JSON, MsgPack, CBOR, Protobuf), and DAG execution graphs.
- **Built-in Admin Dashboard** -- Real-time Next.js dashboard served at `/_vorte/dashboard` with metrics, module health, route inspection, and system monitoring.
- **Standard Response Envelope** -- Every response wrapped in a consistent `{success, data, meta, ai, error, pagination}` structure with request tracing.
- **CLI Scaffolding** -- 30+ CLI commands for project creation, module/job/agent/pipeline generation, migrations, Docker/K8s manifests, benchmarking, and more.
- **Production Ready** -- Kubernetes health probes (`/health`, `/ready`, `/live`), Prometheus metrics (`/_vorte/metrics`), API versioning with deprecation headers, and graceful shutdown.
- **WASM Sandbox** -- Execute untrusted code in isolated WebAssembly sandboxes with fuel limits and optional WASI.
- **TypeScript Generation** -- Auto-generate TypeScript interfaces from Pydantic models via TypeMirror.

---

## Quick Start

### 1. Install Vorte

```bash
pip install vorte
```

For AI features:
```bash
pip install vorte[ai]
```

For everything:
```bash
pip install vorte[full]
```

### 2. Create a New Project

```bash
vorte new my-awesome-app
cd my-awesome-app
```

### 3. Start Development Server

```bash
vorte serve --watch
```

Visit `http://localhost:8000/_vorte/dashboard` for the admin panel.

### 4. Minimal Application

```python
from vorte import Vorte

app = Vorte(auto_load=True)

@app.get("/api/v1/hello")
async def hello():
    return {"message": "Welcome to Vorte!"}
```

---

## Installation

### From PyPI

```bash
pip install vorte
```

### Optional Dependency Groups

| Group | Install Command | Includes |
|-------|----------------|----------|
| AI | `pip install vorte[ai]` | openai, anthropic, google-generativeai |
| Payments | `pip install vorte[payments]` | stripe |
| Search | `pip install vorte[search]` | meilisearch |
| Storage | `pip install vorte[storage]` | boto3 |
| Sandbox | `pip install vorte[sandbox]` | wasmtime |
| Full | `pip install vorte[full]` | All of the above |
| Dev | `pip install vorte[dev]` | pytest, ruff, mypy |

### Requirements

- **Python** >= 3.11
- **Rust** >= 1.75 (only for building the native engine from source)

---

## Architecture Overview

Vorte follows a modular **Core + Modules** architecture:

```
┌─────────────────────────────────────────────────┐
│                  Vorte Application               │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │  FastAPI  │  │  Module  │  │  DI Container │  │
│  │  (ASGI)   │  │ Registry │  │  (Singleton,  │  │
│  │           │  │ (21 mods)│  │  Request,     │  │
│  └──────────┘  └──────────┘  │  Transient)    │  │
│  ┌──────────┐  ┌──────────┐  └───────────────┘  │
│  │ Executor │  │  Router  │  ┌───────────────┐  │
│  │(Work-    │  │(Versioned│  │  TypeMirror   │  │
│  │ Stealing)│  │ + Deprec)│  │ (TypeScript)  │  │
│  └──────────┘  └──────────┘  └───────────────┘  │
└──────────────────────┬──────────────────────────┘
                       │ PyO3 ASGI Bridge
┌──────────────────────┴──────────────────────────┐
│              Vorte Engine (Rust)                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │  Radix   │  │  Serde   │  │  Scheduler   │   │
│  │  Router  │  │  Engine  │  │  (Priority)  │   │
│  └──────────┘  └──────────┘  └──────────────┘   │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │  HTTP    │  │  Queue   │  │  DAG Graph   │   │
│  │  Server  │  │  Engine  │  │  Executor    │   │
│  └──────────┘  └──────────┘  └──────────────┘   │
└──────────────────────────────────────────────────┘
```

### Initialization Flow

```
Vorte.__init__()
  -> Settings.from_env()          # Load .env configuration
  -> ModuleRegistry()             # Create module registry
  -> Container (DI)               # Initialize dependency injection
  -> VorteExecutor                # Work-stealing thread pool
  -> FastAPI(lifespan=...)        # ASGI app with lifecycle
  -> CORS + Tracing Middleware    # Request tracking
  -> /health, /ready, /live       # Kubernetes probes
  -> /_vorte/metrics              # Prometheus endpoint
  -> /_vorte/dashboard/*          # Admin API
  -> register_all()               # Priority-sorted module init
  -> startup_all()                # Module startup hooks
```

### Request Lifecycle

```
HTTP Request
  -> CORSMiddleware
  -> Trace ID + Request Timing middleware
  -> VersioningMiddleware (deprecation headers)
  -> VorteAPIRoute (N+1 query look-ahead)
  -> Route Handler (@safe_route for sync dispatch)
  -> VorteJSONResponse (envelope + FastSerializer)
  -> X-Request-ID, X-Response-Time, X-Powered-By headers
```

---

## Core Concepts

### Application

The `Vorte` class is the main entry point. It wraps a FastAPI instance and orchestrates all subsystems.

```python
from vorte import Vorte

# Auto-load all 21 modules
app = Vorte(auto_load=True)

# Cherry-pick specific modules
app = Vorte()
app.register(AuthModule(), AIModule(), DatabaseModule())

# Exclude specific modules
app = Vorte(auto_load=True, exclude_modules=["graphql", "sockets"])

# Custom configuration
app = Vorte(title="My API", version="2.0.0", dashboard=True)

# Lifecycle hooks
@app.on_startup
async def setup():
    print("Starting up...")

@app.on_shutdown
async def cleanup():
    print("Shutting down...")
```

#### Built-in Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Full module health check (200 healthy, 503 degraded) |
| `/ready` | GET | Kubernetes readiness probe |
| `/live` | GET | Kubernetes liveness probe |
| `/_vorte/info` | GET | Framework version, Python version, module count, route count |
| `/_vorte/metrics` | GET | Prometheus-formatted metrics |
| `/_vorte/dashboard/overview` | GET | Dashboard overview data |
| `/_vorte/dashboard/modules` | GET | All registered modules |
| `/_vorte/dashboard/routes` | GET | All registered routes |
| `/_vorte/dashboard/health` | GET | Health check details |
| `/_vorte/dashboard/config` | GET | Non-sensitive configuration |

### Module System

All features are implemented as `Module` subclasses with priority-based initialization and dependency validation.

```python
from vorte import Module, ModuleMeta, ModulePriority, ModuleState

class CustomModule(Module):
    meta = ModuleMeta(
        name="custom",
        version="1.0.0",
        description="My custom module",
        priority=ModulePriority.DEFAULT,
        dependencies=["database"],
        auto_discover=True,
        lazy_load=False,
    )

    def register(self, app):
        @app.get("/custom")
        async def custom_route():
            return {"hello": "world"}

    async def on_startup(self):
        print("Custom module starting...")

    async def on_shutdown(self):
        print("Custom module shutting down...")

    async def health_check(self):
        return {"module": "custom", "status": "healthy"}
```

#### Module Priority Order

Modules are initialized in priority order (lower = earlier):

| Priority | Value | Typical Use |
|----------|-------|-------------|
| CONFIG | 0 | Configuration modules |
| DATABASE | 10 | Database connections |
| CACHE | 20 | Cache layers |
| QUEUE | 30 | Job queues |
| AUTH | 40 | Authentication |
| SEARCH | 50 | Search engines |
| MIDDLEWARE | 60 | Middleware |
| ROUTES | 70 | Route handlers |
| AI | 80 | AI providers |
| PAYMENTS | 90 | Payment providers |
| DASHBOARD | 100 | Admin dashboard |

#### Module States

- `REGISTERED` -- Module has been added to the registry
- `INITIALIZING` -- Module is running its startup routine
- `READY` -- Module is fully operational
- `FAILED` -- Module encountered an error
- `SHUTTING_DOWN` -- Module is shutting down
- `SHUTDOWN` -- Module has completed shutdown

### Configuration

Vorte uses a layered configuration system with environment variables (prefixed `VORTE_`) and `.env` file support.

```python
from vorte import Settings, settings

# Access global settings
db_url = settings.database.url
redis_url = settings.redis.url

# Check environment
if settings.is_production():
    # Production-specific logic
    pass

# Create custom settings
custom = Settings.from_env()
```

#### Configuration Sections

| Section | Key Fields | Description |
|---------|-----------|-------------|
| `settings.database` | `url`, `pool_size`, `max_overflow`, `echo`, `read_replica_urls` | PostgreSQL/asyncpg connection |
| `settings.redis` | `url`, `cache_url`, `queue_url` | Redis connections |
| `settings.auth` | `strategy`, `secret_key`, `refresh_tokens`, `mfa`, `oauth_providers` | Authentication |
| `settings.ai` | `default_model`, `providers`, `track_costs`, `max_tokens` | AI providers |
| `settings.cache` | `driver`, `default_ttl`, `l1_enabled`, `l2_enabled` | 4-layer cache |
| `settings.queue` | `driver`, `default_retries`, `concurrency` | Background jobs |
| `settings.storage` | `driver`, `bucket`, `region` | File storage |
| `settings.mailer` | `driver`, `host`, `port`, `from_address` | Email sending |
| `settings.mpesa` | `environment`, `consumer_key`, `shortcode` | M-Pesa Daraja |
| `settings.payments` | `provider`, `currency`, `api_key` | Stripe/Paystack |
| `settings.security` | `helmet`, `csrf`, `xss`, `rate_limit` | Security middleware |
| `settings.search` | `engine`, `meilisearch_url` | Full-text search |
| `settings.tenancy` | `strategy`, `isolation` | Multi-tenancy |
| `settings.i18n` | `default_locale`, `fallback_locale` | Internationalization |
| `settings.performance` | `http2`, `brotli`, `protobuf` | Performance tuning |

#### Environment Variable Helpers

```python
from vorte.core.config import env, env_bool, env_int, env_list

database_url = env("DATABASE_URL")              # VORTE_DATABASE_URL
debug = env_bool("DEBUG", default=False)         # "true"/"1"/"yes"/"on"
pool_size = env_int("POOL_SIZE", default=10)     # Integer
origins = env_list("CORS_ORIGINS")               # Comma-separated
```

### Routing & Versioning

Vorte extends FastAPI routing with API versioning, deprecation headers, and N+1 query optimization.

```python
from vorte import router
from vorte.core.router import VorteAPIRouter, VersioningMiddleware

# Use the default router
@router.get("/users")
async def list_users():
    return {"users": []}

# Create a versioned router
api = VorteAPIRouter()

@api.get("/users", version="v1")
async def list_users_v1():
    return {"users": []}

@api.get(
    "/users",
    version="v2",
    deprecated_in="v2",
    sunset_date="2025-12-31",
)
async def list_users_v2():
    return {"users": [], "metadata": {}}

# Register on the app
app.include_router(api)
```

#### Versioning Strategies

- **URL-based** (default): `/v1/users`, `/v2/users`
- **Header-based**: `API-Version: v1` header

Deprecated routes automatically emit `Deprecation`, `Sunset`, and `Link` headers.

### Response Envelope

Every response is wrapped in a standard envelope:

```json
{
  "success": true,
  "data": { ... },
  "meta": {
    "request_id": "req_a1b2c3d4e5f6",
    "version": "v1",
    "timestamp": "2026-05-20T10:30:00Z",
    "latency_ms": 12.5
  },
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 100,
    "total_pages": 5
  },
  "ai": {
    "model": "gpt-4",
    "provider": "openai",
    "tokens": 150,
    "cost": 0.003,
    "cached": false,
    "response_time_ms": 850
  }
}
```

#### Response Helpers

```python
from vorte import success_response, error_response, paginated_response, ai_response

# Success
return success_response(data={"user": user}, status_code=200)

# Error
return error_response(
    code="NOT_FOUND",
    message="User not found",
    status_code=404,
    field_name="user_id"
)

# Paginated
return paginated_response(
    data=users,
    page=1,
    per_page=20,
    total=100
)

# AI-augmented
return ai_response(
    data={"answer": "..."},
    model="gpt-4",
    provider="openai",
    tokens=150,
    cost=0.003
)
```

#### Streaming Responses

```python
from vorte import VorteSSEResponse, VorteStreamResponse

# Server-Sent Events
async def event_stream():
    for i in range(10):
        yield {"count": i}

@app.get("/events")
async def sse_endpoint():
    return VorteSSEResponse(event_stream())

# Raw streaming (zero-copy)
@app.get("/download")
async def download():
    return VorteStreamResponse(large_data_stream())
```

### Dependency Injection

Vorte includes a full DI container with singleton, request, and transient scopes.

```python
from vorte import Container, Depends, inject, wire

# Define services
class UserService:
    def get_user(self, user_id: int):
        return {"id": user_id, "name": "John"}

# Register with the container
container = Container()
container.register(UserService, singleton=True)

# Or use the @wire decorator for auto-registration
@wire(UserService, singleton=True)
class UserService:
    pass

# Use in routes with Depends
@app.get("/users/{user_id}")
async def get_user(user_id: int, service: UserService = Depends(UserService)):
    return service.get_user(user_id)

# Mark function for injection
@inject
def process_data(service: UserService = Depends(UserService)):
    return service.get_user(1)
```

#### Container Scopes

- **Singleton** -- One instance for the container lifetime
- **Request** -- One instance per HTTP request
- **Transient** -- New instance every resolution

```python
container.register(CacheService, singleton=True)
container.register(RequestService, transient=True)

# Eager initialization
container.build()           # Sync
await container.abuild()    # Async

# Child containers (scoped)
child = container.create_child()
```

### Serialization

FastSerializer automatically picks the fastest available backend:

| Priority | Backend | Performance |
|----------|---------|-------------|
| 1 | Native Rust | Fastest |
| 2 | orjson | 3-10x faster than stdlib |
| 3 | stdlib json | Fallback |

```python
from vorte import FastSerializer, lazy_schema
from pydantic import BaseModel

# Serialize
data = {"name": "Vorte", "version": "1.0.8"}
encoded = FastSerializer.dumps(data)           # bytes
encoded_str = FastSerializer.dumps_str(data)    # string

# Deserialize
decoded = FastSerializer.loads(encoded)

# Multi-format
msgpack_data = FastSerializer.dumps(data, format="msgpack")
cbor_data = FastSerializer.dumps(data, format="cbor")
proto_data = FastSerializer.dumps(data, format="protobuf")

# Check backend
print(FastSerializer.backend)  # "native", "orjson", or "stdlib"
print(FastSerializer.is_native())

# Lazy schema validation (defer Pydantic validation)
class UserSchema(BaseModel):
    name: str
    email: str

@lazy_schema(UserSchema)
async def create_user(request):
    payload = request._vorte_lazy_payload  # Access raw bytes
    user = payload.validate()              # Validate on demand
    return user
```

### Executor & Concurrency

VorteExecutor provides a work-stealing thread pool with optional Rust scheduler integration.

```python
from vorte import VorteExecutor, safe_route, VorteTaskGroup

executor = VorteExecutor(max_workers=8)

# Run async functions directly
result = await executor.run(async_function, arg1, arg2)

# Run sync functions in thread pool
result = await executor.run(sync_function, arg1, arg2)

# Timeout support
result = await executor.run_with_timeout(fn, arg, timeout=30.0)

# Background jobs (fire-and-forget)
executor.submit_background(cleanup_task, priority="low")

# @safe_route decorator - transparent sync/async dispatch
@safe_route
def sync_handler():          # Auto-wrapped for async dispatch
    return compute_result()

@safe_route
async def async_handler():   # Passed through unchanged
    return await fetch_data()
```

#### Structured Concurrency

```python
from vorte import VorteTaskGroup

async def process():
    async with VorteTaskGroup() as tg:
        task1 = tg.create_task(fetch_users())
        task2 = tg.create_task(fetch_orders())
        # If any task fails, all others are cancelled via Rust cancellation token
        # tg.cancel_token propagates to Rust/Tokio workers
```

---

## Built-in Modules (21)

### AI Module

Multi-provider AI integration with cost tracking, streaming, and intelligent routing.

```python
from vorte import AIModule

# Auto-configured via settings.ai
app.register(AIModule())

# Supported providers: OpenAI, Anthropic, Gemini, Mistral
# Features: streaming, embeddings, cost tracking, model routing
```

**Configuration** (`settings.ai`):
- `default_model` -- Default model to use (e.g., "gpt-4")
- `providers` -- Dict of provider configurations
- `track_costs` -- Enable cost tracking
- `max_tokens`, `temperature` -- Default generation parameters

**Routing Strategies** (via `ProviderRegistry`):
- `STATIC` -- Always use the configured provider
- `ROUND_ROBIN` -- Distribute across providers
- `COST_OPTIMIZED` -- Choose cheapest provider
- `LEAST_LOADED` -- Route to least busy provider
- `LATENCY_OPTIMIZED` -- Route to fastest provider
- `QUALITY_FIRST` -- Route to highest-quality provider

### Agents Module (Beta)

Build AI agents with tools, memory, RAG, pipelines, and guardrails.

```python
from vorte import AgentsModule

app.register(AgentsModule())

# Features:
# - Agent orchestration with tool use
# - Conversation memory (short-term + long-term with summarization)
# - RAG (Retrieval-Augmented Generation) pipelines
# - Multi-step agent pipelines
# - Guardrails for safe AI output
# - Versioned prompt templates with {{variable}} interpolation
```

### Auth Module

Complete authentication with JWT, OAuth, API keys, RBAC, MFA, and sessions.

```python
from vorte import AuthModule

app.register(AuthModule())

# Features:
# - JWT access + refresh tokens
# - OAuth2 providers (Google, GitHub, etc.)
# - API key authentication
# - Role-Based Access Control (RBAC)
# - Multi-Factor Authentication (MFA/TOTP)
# - Session management
# - Route guards (@require_auth, @require_role)
```

**Configuration** (`settings.auth`):
- `strategy` -- "jwt" or "session"
- `secret_key` -- JWT signing key
- `token_expiry_minutes` -- Access token lifetime
- `refresh_expiry_days` -- Refresh token lifetime
- `oauth_providers` -- OAuth provider configurations
- `mfa` -- Enable MFA

### Database Module

SQLAlchemy async ORM with query planning, N+1 detection, and performance mode.

```python
from vorte import DatabaseModule, performance_mode, QueryPlanner, select_related, N1Detector

app.register(DatabaseModule())

# Features:
# - Async SQLAlchemy with asyncpg/aiosqlite
# - Automatic N+1 query detection
# - Look-ahead query planning (auto eager-loading)
# - Performance mode (raw SQL + FastSerializer)
# - Alembic migration support
# - Read replica support
```

#### N+1 Detection & Query Planning

```python
from vorte import select_related, N1Detector
from pydantic import BaseModel

class UserResponse(BaseModel):
    name: str
    profile: ProfileResponse  # Nested relationship auto-detected

# Auto-detects nested relationships from response_model
@app.get("/users", response_model=list[UserResponse])
async def list_users():
    # VorteAPIRoute auto-infers "profile" relationship
    # QueryPlanner adds selectinload(User.profile)
    return await User.all()

# Manual eager loading
@select_related("posts", "posts.comments")
@app.get("/users/{id}")
async def get_user(id: int):
    return await User.get(id)

# N+1 Detection
detector = N1Detector(threshold=5)
detector.track("SELECT * FROM users WHERE id = 1")
if detector.is_n1():
    print(f"N+1 detected! {detector.query_count} queries")
```

### Cache Module

4-layer caching with L1 (in-memory), L2 (Redis), L3 (CDN), L4 (database).

```python
from vorte import CacheModule

app.register(CacheModule())

# Features:
# - L1: In-memory LRU cache
# - L2: Redis cache
# - L3: CDN cache headers
# - L4: Database-backed cache
# - Cache decorators
# - TTL management
# - Cache invalidation
```

**Configuration** (`settings.cache`):
- `driver` -- "redis" or "memory"
- `default_ttl` -- Default time-to-live
- `l1_enabled`, `l1_max_size` -- In-memory cache
- `l2_enabled` -- Redis cache
- `l3_cdn_url` -- CDN integration
- `l4_db_cache` -- Database cache

### Queue Module

Background job processing with priorities, backpressure, dead letter queues, and retry logic.

```python
from vorte import QueueModule

app.register(QueueModule())

# Features:
# - Priority-based job queues (CRITICAL, HIGH, DEFAULT, LOW)
# - Backpressure with watermarks (NORMAL -> HIGH -> FULL)
# - Dead Letter Queue (DLQ) for permanently failed jobs
# - Automatic retry with configurable delay
# - Worker concurrency control
# - Redis or in-memory backends
```

**Configuration** (`settings.queue`):
- `driver` -- "redis" or "memory"
- `default_retries` -- Max retry attempts
- `default_retry_delay` -- Seconds between retries
- `concurrency` -- Max concurrent workers

### Storage Module

File storage with local filesystem and S3 backends.

```python
from vorte import StorageModule

app.register(StorageModule())

# Features:
# - Local filesystem storage
# - AWS S3 storage
# - CDN integration
# - File upload/download
# - Presigned URLs
```

**Configuration** (`settings.storage`):
- `driver` -- "local" or "s3"
- `bucket` -- S3 bucket name
- `region` -- AWS region
- `cdn_url` -- CDN URL

### Search Module (Beta)

Full-text search with MeiliSearch and pgvector support.

```python
from vorte import SearchModule

app.register(SearchModule())

# Features:
# - MeiliSearch integration
# - pgvector vector search
# - Index management
# - Search API
```

### Mailer Module (Beta)

Email sending with SMTP backend.

```python
from vorte import MailerModule

app.register(MailerModule())

# Features:
# - SMTP driver
# - HTML/text emails
# - Template-based emails
# - Queue-based sending
```

### M-Pesa Module (Beta)

First-class Safaricom M-Pesa (Daraja API) integration.

```python
from vorte import MpesaModule

app.register(MpesaModule())

# Features:
# - STK Push (Lipa Na M-Pesa Online)
# - C2B (Customer to Business)
# - B2C (Business to Customer)
# - B2B (Business to Business)
# - Account Balance
# - Transaction Status
# - Sandbox + Production environments
```

**Configuration** (`settings.mpesa`):
- `environment` -- "sandbox" or "production"
- `consumer_key`, `consumer_secret` -- Daraja API credentials
- `shortcode` -- Business shortcode
- `passkey` -- Lipa Na M-Pesa passkey
- `callback_url` -- Webhook URL

### Payments Module (Beta)

Multi-provider payment processing with Stripe and Paystack.

```python
from vorte import PaymentsModule

app.register(PaymentsModule())

# Features:
# - Stripe integration (charges, subscriptions, usage, entitlements)
# - Paystack integration (Africa-focused)
# - Webhook verification
# - Subscription management
```

### Notifications Module (Beta)

Multi-channel notification delivery.

```python
from vorte import NotificationsModule

app.register(NotificationsModule())

# Features:
# - In-app notifications
# - Email notifications
# - Push notifications
# - SMS notifications
# - Template-based messages
```

### Security Module

Security middleware with rate limiting, CSRF, XSS protection, and bot detection.

```python
from vorte import SecurityModule

app.register(SecurityModule())

# Features:
# - Helmet-style security headers
# - CSRF protection
# - XSS sanitization
# - Rate limiting (configurable max/window)
# - Bot detection
# - Geo-blocking
```

### Webhooks Module (Beta)

Webhook management with delivery, retry, and verification.

```python
from vorte import WebhooksModule

app.register(WebhooksModule())

# Features:
# - Webhook registration
# - Signed delivery (HMAC)
# - Retry with exponential backoff
# - Delivery logs
```

### Sockets Module (Beta)

WebSocket management with rooms, broadcasting, and authentication.

```python
from vorte import SocketModule

app.register(SocketModule())

# Features:
# - WebSocket connection manager
# - Room-based broadcasting
# - Authentication
# - Connection lifecycle hooks
```

### GraphQL Module (Beta)

GraphQL API with auto-schema generation and subscriptions.

```python
from vorte import GraphQLModule

app.register(GraphQLModule())

# Features:
# - Auto-schema from SQLAlchemy models
# - GraphQL Playground
# - Subscriptions
# - Query/Mutation resolvers
```

### Multi-Tenancy Module (Beta)

Multi-tenant application support with multiple isolation strategies.

```python
from vorte import MultiTenancyModule

app.register(MultiTenancyModule())

# Features:
# - Subdomain-based resolution (tenant.myapp.com)
# - Header-based resolution (X-Tenant-ID)
# - Path-based resolution (/t/{tenant}/...)
# - JWT claim-based resolution
# - Schema-level isolation
```

### Feature Flags Module (Beta)

Feature flags with percentage rollouts, targeting rules, and A/B testing.

```python
from vorte import FeaturesModule

app.register(FeaturesModule())

# Features:
# - Boolean feature flags
# - Percentage rollouts (MD5 hash bucketing)
# - User targeting rules
# - A/B test variants
# - Runtime flag updates
```

### Internationalization Module

Multi-language support with translation files, formatting, and locale detection.

```python
from vorte import I18nModule

app.register(I18nModule())

# Features:
# - JSON-based translation files
# - Variable interpolation
# - Currency/date/number formatting
# - Accept-Language detection
# - Swahili support included
```

### Logging Module

Structured logging with multiple outputs.

```python
from vorte import LoggingModule

app.register(LoggingModule())

# Features:
# - Structured JSON logging
# - Multiple output targets
# - Log level configuration
# - Request logging
```

### Dashboard Module

Real-time admin dashboard built with Next.js, Tailwind CSS, and Framer Motion.

```python
from vorte import DashboardModule

app.register(DashboardModule())

# Served at /_vorte/dashboard
# Shows: modules, routes, health, metrics, config, events
```

---

## CLI Reference

Vorte provides 30+ CLI commands organized by category:

### Project Commands

```bash
vorte new <name>                  # Create new project (minimal or ai-saas template)
vorte serve [--host] [--port] [--watch] [--workers]  # Start development server
vorte routes                      # List all registered routes
vorte modules                     # List all registered modules
vorte health                      # Check application health
```

### Generator Commands

```bash
vorte make:module <name>          # Generate module scaffold
vorte make:job <name>             # Generate background job
vorte make:agent <name>           # Generate AI agent
vorte make:pipeline <name>        # Generate AI pipeline
vorte make:migration <name>       # Generate Alembic migration
```

### Database Commands

```bash
vorte migrate                     # Run pending migrations
vorte migrate:rollback [--step]   # Rollback migrations
vorte migrate:fresh [--seed]      # Drop all tables and re-migrate
vorte migrate:status              # Show migration status
vorte db:seed                     # Run database seeders
```

### AI Commands

```bash
vorte ai:models                   # List AI models with pricing
vorte ai:costs [--period]         # Show AI cost report
```

### M-Pesa Commands

```bash
vorte mpesa:setup                 # Interactive M-Pesa credential setup
vorte mpesa:balance               # Check M-Pesa account balance
```

### DevOps Commands

```bash
vorte docker:init                 # Generate Dockerfile + docker-compose.yml
vorte docker:build                # Build Docker image
vorte k8s:init [--name]           # Generate Kubernetes manifests
vorte bench [--url] [--requests] [--concurrency]  # HTTP benchmark
```

### Manifest Commands

```bash
vorte manifest:export [--app] [--output]       # Export OpenAPI + route JSON
vorte manifest:validate [--app] [--manifest]   # Validate against saved manifest
vorte manifest:types [--app] [--output]        # Generate TypeScript interfaces
```

### Other Commands

```bash
vorte cache:stats                 # Show cache statistics
vorte search:index [action]       # Manage MeiliSearch indexes
vorte dashboard:build             # Build Next.js dashboard
```

---

## Testing

Vorte includes a complete testing framework with async test client, AI mocker, and M-Pesa mocker.

```python
import pytest
from vorte import Vorte
from vorte.testing import VorteTestClient, AIMocker, MpesaMocker, VorteTestCase

app = Vorte(auto_load=True)

@pytest.fixture
async def client():
    async with VorteTestClient(app) as client:
        yield client

# Basic testing
async def test_hello(client):
    response = await client.get("/api/v1/hello")
    response.assert_success()
    response.assert_status(200)
    response.assert_data({"message": "Welcome to Vorte!"})

# Schema validation
async def test_users(client):
    response = await client.get("/api/v1/users")
    response.assert_schema(UserListSchema)

# AI mocking
async def test_ai_endpoint(client):
    ai_mock = AIMocker()
    ai_mock.mock_response("Summarize this", {"summary": "A great article"})

    response = await client.post("/api/v1/summarize", json={"text": "..."})
    response.assert_success()
    response.assert_ai_usage()

# M-Pesa mocking
async def test_mpesa_stk(client):
    mpesa = MpesaMocker()
    mpesa.stk_push_success(receipt="RKT123", checkout_request_id="ws123")

# Class-based testing
class TestUsers(VorteTestCase):
    app = Vorte(auto_load=True)

    def test_list_users(self):
        response = self.client.get("/api/v1/users")
        self.assert_success(response)
```

---

## Rust Native Engine

Vorte ships with an optional Rust engine that provides significant performance improvements. The engine uses 8 crates:

| Crate | Purpose |
|-------|---------|
| `vorte-http` | Zero-copy HTTP request/response types (FFI-safe `repr(C)`) |
| `vorte-router` | Zero-allocation radix tree router with lock-free concurrent reads |
| `vorte-core` | Hyper/Tokio HTTP server engine |
| `vorte-py` | PyO3 ASGI bridge (Python bindings) |
| `vorte-scheduler` | Priority-based task scheduler (work-stealing) |
| `vorte-queue` | Async queue engine with backpressure, DLQ, and Redis backend |
| `vorte-serde` | Multi-format serialization with buffer pooling (JSON, MsgPack, CBOR, Protobuf) |
| `vorte-graph` | DAG execution graph engine with short-circuit support |

### Features

- **Zero-copy hot path** -- RawRequest/RawResponse use offset/length views into shared buffers
- **404 short-circuit** -- Unmatched routes never enter Python
- **Lock-free routing** -- After `freeze()`, radix tree is `Arc<Node>` for concurrent reads
- **GIL release** -- I/O operations release the Python GIL
- **Buffer pooling** -- 4-bucket pool (4KB, 16KB, 64KB, 256KB) with RAII buffer return
- **Multi-format serialization** -- JSON, MessagePack, CBOR, Protobuf via Serde

### Building from Source

```bash
# Prerequisites: Rust >= 1.75, Python >= 3.11
pip install maturin
maturin develop --release
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VORTE_HOST` | `0.0.0.0` | Server bind address |
| `VORTE_PORT` | `8000` | Server bind port |
| `VORTE_WORKERS` | `1` | Number of worker threads |
| `VORTE_MAX_CONNECTIONS` | `10000` | Max concurrent connections |
| `VORTE_KEEP_ALIVE` | `75` | Keep-alive timeout (seconds) |
| `VORTE_TCP_NODELAY` | `true` | Disable Nagle's algorithm |
| `VORTE_HTTP2` | `false` | Enable HTTP/2 |
| `VORTE_SHUTDOWN_TIMEOUT` | `30` | Graceful shutdown timeout |

---

## Deployment

### Docker

```bash
vorte docker:init     # Generates Dockerfile + docker-compose.yml
vorte docker:build    # Build the Docker image
```

Generated `docker-compose.yml` includes: API server, PostgreSQL, Redis, background worker, and scheduler.

### Kubernetes

```bash
vorte k8s:init --name my-app    # Generates Deployment + Service manifests
```

### Health Probes

Vorte automatically provides Kubernetes-compatible health endpoints:

```yaml
livenessProbe:
  httpGet:
    path: /live
    port: 8000
readinessProbe:
  httpGet:
    path: /ready
    port: 8000
startupProbe:
  httpGet:
    path: /health
    port: 8000
```

### Prometheus Metrics

Available at `/_vorte/metrics`:
- `vorte_serialization_time_ns`
- `vorte_database_wait_time_ns`
- `vorte_scheduling_latency_ns`
- `vorte_event_loop_lag_ns`
- `vorte_buffered_spans_total`
- `vorte_metrics_buffer_capacity_total`

---

## Project Structure

```
vorte-framework/
├── vorte/                          # Python package
│   ├── __init__.py                 # Public API exports
│   ├── engine.py                   # VorteEngine (Python/Rust bridge)
│   ├── core/                       # Core framework
│   │   ├── app.py                  # Vorte application class
│   │   ├── config.py               # Settings & configuration
│   │   ├── module.py               # Module system (Module, ModuleRegistry)
│   │   ├── router.py               # Versioned API routing
│   │   ├── response.py             # Standard response envelope
│   │   ├── di.py                   # Dependency injection container
│   │   ├── serializer.py           # FastSerializer (native/orjson/stdlib)
│   │   ├── executor.py             # Work-stealing executor
│   │   ├── concurrency.py          # Structured concurrency (VorteTaskGroup)
│   │   ├── sandbox.py              # WASM sandbox (wasmtime)
│   │   ├── tracing.py              # Request tracing
│   │   ├── typemirror.py           # TypeScript type generation
│   │   └── __init__.py
│   ├── modules/                    # 21 built-in modules
│   │   ├── ai/                     # Multi-provider AI
│   │   ├── agents/                 # AI agents, memory, RAG, pipelines
│   │   ├── auth/                   # JWT, OAuth, API keys, RBAC, MFA
│   │   ├── cache/                  # 4-layer cache
│   │   ├── database/               # SQLAlchemy ORM + query planner
│   │   ├── queue/                  # Background jobs
│   │   ├── storage/                # File storage (local/S3)
│   │   ├── search/                 # Full-text search
│   │   ├── mailer/                 # Email sending
│   │   ├── mpesa/                  # M-Pesa Daraja
│   │   ├── payments/               # Stripe/Paystack
│   │   ├── notifications/          # Multi-channel notifications
│   │   ├── security/               # Security middleware
│   │   ├── webhooks/               # Webhook management
│   │   ├── sockets/                # WebSocket support
│   │   ├── graphql/                # GraphQL API
│   │   ├── tenancy/                # Multi-tenancy
│   │   ├── features/               # Feature flags
│   │   ├── i18n/                   # Internationalization
│   │   ├── logging/                # Structured logging
│   │   └── dashboard/              # Admin dashboard
│   ├── middleware/                  # Middleware
│   │   ├── error_handler.py        # Global error handler
│   │   └── request_timing.py       # Request timing
│   ├── cli/                        # CLI tools
│   │   ├── main.py                 # 30+ CLI commands
│   │   └── manifest.py             # Manifest export/validate/types
│   └── testing/                    # Testing framework
│       └── __init__.py             # VorteTestClient, AIMocker, MpesaMocker
├── vorte-engine/                   # Rust native engine
│   ├── Cargo.toml                  # Workspace (8 crates)
│   └── crates/
│       ├── vorte-http/             # Zero-copy HTTP types
│       ├── vorte-router/           # Radix tree router
│       ├── vorte-core/             # Hyper/Tokio server
│       ├── vorte-py/               # PyO3 Python bindings
│       ├── vorte-scheduler/        # Priority task scheduler
│       ├── vorte-queue/            # Async queue engine
│       ├── vorte-serde/            # Multi-format serialization
│       └── vorte-graph/            # DAG execution graph
├── tests/                          # Test suite (22 files)
├── migrations/                     # Alembic migrations
├── .github/workflows/publish.yml   # CI/CD (3-platform wheel build + PyPI publish)
├── pyproject.toml                  # Project metadata
├── CHANGELOG.md                    # Version history
├── CONTRIBUTING.md                 # Contribution guide
└── LICENSE                         # MIT License
```

---

## Contributing

We welcome contributions! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:

- Setting up the development environment
- Code style and linting
- Running tests
- Submitting pull requests

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history.

---

## License

Vorte is released under the **MIT License**. See [LICENSE](LICENSE) for details.

---

<p align="center">
  Built with care for developers who value speed, aesthetics, and intelligence.
</p>
