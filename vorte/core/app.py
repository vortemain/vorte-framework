"""
Vorte Framework Core Application
==================================
The main Vorte application class. This is the entry point for every Vorte application.

Usage:
    app = Vorte()
    app.register(AuthModule(strategy='jwt'))
    app.register(AIModule(default_model='gpt-4o'))
    
    @app.get('/hello')
    async def hello():
        return {'message': 'Welcome to Vorte!'}
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import asyncio
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Type, Union

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from vorte.core.config import Settings, settings
from vorte.core.module import Module, ModuleRegistry
from vorte.core.response import (
    VorteJSONResponse,
    error_response,
    ResponseMeta,
    _generate_request_id,
)
from vorte.core.router import VorteAPIRouter, VersioningMiddleware, VersioningStrategy
from vorte.core.di import Container, _global_container
from vorte.core.executor import VorteExecutor
from vorte.core.typemirror import TypeMirror
from vorte.modules.database.planner import QueryPlanner


class Vorte:
    """
    The Vorte Framework Application.
    
    A batteries-included, AI-first Python API framework built on top of FastAPI.
    Provides module system, API versioning, standard responses, auth, database,
    AI integration, caching, queues, and much more out of the box.
    
    Args:
        auto_load: If True, automatically registers all 22 built-in modules.
                   This means the framework is fully operational the moment you
                   create the app — no manual module registration needed.
        exclude_modules: List of module names to exclude when auto_load=True.
        dashboard: If True (default), mounts the built-in admin dashboard.
        
    Attributes:
        settings: Application settings
        modules: Module registry
        container: Dependency injection container
        fastapi: Underlying FastAPI instance
        events: Event bus for pub/sub
    
    Usage:
        # Full auto — all 22 modules + dashboard loaded instantly
        from vorte import Vorte
        app = Vorte(auto_load=True)
        
        # Cherry-pick
        app = Vorte()
        app.register(AuthModule(), AIModule())
        
        # Exclude specific modules
        app = Vorte(auto_load=True, exclude_modules=['mpesa', 'payments'])
    """

    # Core/main modules of the framework that are auto-loaded by default
    _CORE_MODULES = {
        'logging',
        'database',
        'cache',
        'queue',
        'security',
        'auth',
        'ai',
        'storage',
        'i18n',
        'dashboard',
    }
    
    # All built-in modules — imported lazily to avoid circular imports
    _BUILTIN_MODULES = None
    
    @classmethod
    def _get_builtin_modules(cls) -> Dict[str, Type[Module]]:
        """Lazy-load all built-in modules."""
        if cls._BUILTIN_MODULES is None:
            from vorte.modules.logging import LoggingModule
            from vorte.modules.database import DatabaseModule
            from vorte.modules.cache import CacheModule
            from vorte.modules.queue import QueueModule
            from vorte.modules.security import SecurityModule
            from vorte.modules.auth import AuthModule
            from vorte.modules.search import SearchModule
            from vorte.modules.sockets import SocketModule
            from vorte.modules.ai import AIModule
            from vorte.modules.agents import AgentsModule
            from vorte.modules.storage import StorageModule
            from vorte.modules.mailer import MailerModule
            from vorte.modules.notifications import NotificationsModule
            from vorte.modules.webhooks import WebhooksModule
            from vorte.modules.features import FeaturesModule
            from vorte.modules.graphql import GraphQLModule
            from vorte.modules.mpesa import MpesaModule
            from vorte.modules.payments import PaymentsModule
            from vorte.modules.tenancy import MultiTenancyModule
            from vorte.modules.i18n import I18nModule
            from vorte.modules.dashboard import DashboardModule
            cls._BUILTIN_MODULES = {
                'logging': LoggingModule,
                'database': DatabaseModule,
                'cache': CacheModule,
                'queue': QueueModule,
                'security': SecurityModule,
                'auth': AuthModule,
                'search': SearchModule,
                'sockets': SocketModule,
                'ai': AIModule,
                'agents': AgentsModule,
                'storage': StorageModule,
                'mailer': MailerModule,
                'notifications': NotificationsModule,
                'webhooks': WebhooksModule,
                'features': FeaturesModule,
                'graphql': GraphQLModule,
                'mpesa': MpesaModule,
                'payments': PaymentsModule,
                'tenancy': MultiTenancyModule,
                'i18n': I18nModule,
                'dashboard': DashboardModule,
            }
        return cls._BUILTIN_MODULES
    
    def __init__(
        self,
        settings: Optional[Settings] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        version: Optional[str] = None,
        auto_load: bool = False,
        exclude_modules: Optional[List[str]] = None,
        dashboard: bool = True,
        **kwargs,
    ):
        self._settings = settings or Settings.from_env()
        self._module_registry = ModuleRegistry()
        self._container = _global_container
        self._events: Dict[str, List[Callable]] = {}
        self._startup_hooks: List[Callable] = []
        self._shutdown_hooks: List[Callable] = []
        self._executor = VorteExecutor()
        self._type_mirror = TypeMirror()
        self._query_planner = QueryPlanner()
        self._versioning = VersioningMiddleware(
            default_version=self._settings.default_version,
            strategy=VersioningStrategy.URL,
        )
        self._request_start_times: Dict[str, float] = {}
        self._request_metrics: Dict[str, Any] = {
            'total': 0,
            'by_path': {},
            'by_method': {},
            'errors': 0,
            'last_requests': [],
        }
        # Generate runtime token for dashboard in development if not configured
        if self._settings.dashboard.auth_required and not self._settings.dashboard.token and not self._settings.app_key:
            if self._settings.app_env == "development":
                import secrets
                self._dashboard_runtime_token = secrets.token_hex(16)
        
        self._start_time: Optional[float] = None
        
        # Build the lifespan handler that drives startup/shutdown
        @asynccontextmanager
        async def _lifespan(fastapi_app):
            # ---- startup ----
            self._start_time = time.time()
            # Eager DI graph wiring — Blueprint §3 Compile-Time Graph Wiring
            await self._container.abuild()
            await self._module_registry.startup_all()
            for hook in self._startup_hooks:
                await hook()
            # Build TypeMirror from registered routes
            self._type_mirror = TypeMirror.from_app(self)
            
            # Start event loop lag detection in background
            lag_task = asyncio.create_task(self._detect_event_loop_lag())
            
            try:
                yield
            finally:
                # ---- shutdown ----
                lag_task.cancel()
                try:
                    await lag_task
                except asyncio.CancelledError:
                    pass
                
                for hook in self._shutdown_hooks:
                    await hook()
                await self._module_registry.shutdown_all()
                self._executor.shutdown(wait=False)

        # Create underlying FastAPI app
        self.fastapi = FastAPI(
            title=title or self._settings.app_name,
            description=description or "Built with Vorte Framework",
            version=version or "1.0.0",
            docs_url=None,
            redoc_url=None,
            lifespan=_lifespan,
            **kwargs,
        )
        
        self.fastapi.vorte = self
        self.fastapi.modules = self._module_registry
        
        # Register built-in middleware
        self._setup_middleware()
        
        # Register built-in routes
        self._setup_builtin_routes()
        
        # Register dashboard API routes (always available)
        self._setup_dashboard_api()
        
        # Auto-load all built-in modules
        if auto_load:
            self.use_all_modules(exclude=exclude_modules, dashboard=dashboard)
        elif dashboard:
            # If auto_load is False but dashboard is True (default), register only the DashboardModule
            from vorte.modules.dashboard import DashboardModule
            self.register(DashboardModule())
            
        # Register all modules (middleware and routes)
        self._module_registry.register_all(self)

        # Log dashboard runtime token in development if generated
        if hasattr(self, "_dashboard_runtime_token"):
            import sys
            sys.stderr.write(
                f"\n======================================================================\n"
                f"[Vorte DX] Security Warning: Dashboard is secured. Access it using token:\n"
                f"  Token: {self._dashboard_runtime_token}\n"
                f"  URL: {self._settings.dashboard.path}?token={self._dashboard_runtime_token}\n"
                f"======================================================================\n\n"
            )
            sys.stderr.flush()
    async def __call__(self, scope, receive, send):
        """ASGI entry point - proxies to FastAPI."""
        if scope["type"] != "http":
            await self.fastapi(scope, receive, send)
            return

        from vorte.core.tracing import set_trace_id, reset_trace_id
        
        request_id = _generate_request_id()
        start_time = time.time()
        self._request_start_times[request_id] = start_time
        
        # Add request_id to request state/scope
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["request_id"] = request_id
        
        token = set_trace_id(request_id)
        
        # For deprecation headers
        path = scope.get("path", "")
        deprecation_headers = self._versioning.get_deprecation_headers(path)
        
        # Don't record metrics for internal framework/dashboard endpoints
        is_internal = path.startswith("/_vorte") or path in ("/health", "/ready", "/live")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                # Inject timing and tracing headers
                headers = list(message.get("headers") or [])
                headers.append((b"X-Request-ID", request_id.encode()))
                headers.append((b"X-Powered-By", b"Vorte"))
                if deprecation_headers:
                    for key, value in deprecation_headers.items():
                        headers.append((key.encode(), value.encode()))
                latency_ms = int((time.time() - start_time) * 1000)
                headers.append((b"X-Response-Time", f"{latency_ms}ms".encode()))
                message["headers"] = headers
                
                # Keep status code for logging later
                scope["_response_status"] = message.get("status", 200)
                
            elif message["type"] == "http.response.body":
                more_body = message.get("more_body", False)
                if not more_body:
                    status = scope.get("_response_status", 200)
                    latency_ms = (time.time() - start_time) * 1000
                    if not is_internal:
                        self.record_request(
                            path,
                            scope.get("method", "GET"),
                            status,
                            latency_ms
                        )
            
            await send(message)

        try:
            await self.fastapi(scope, receive, send_wrapper)
        finally:
            self._request_start_times.pop(request_id, None)
            reset_trace_id(token)
    
    async def _detect_event_loop_lag(self):
        """
        Background task that measures event loop scheduling delay (GIL starvation).
        Emits warning logs if lag exceeds 50ms (0.05 seconds).
        """
        import logging
        logger = logging.getLogger("vorte.core")
        interval = 0.5  # Check every 500ms
        
        while True:
            try:
                start = time.perf_counter()
                await asyncio.sleep(interval)
                elapsed = time.perf_counter() - start
                lag = elapsed - interval
                if lag > 0.05: # 50ms
                    logger.warning(
                        f"[Vorte DX] Event loop lag detected: {lag*1000:.1f}ms. "
                        "This usually indicates GIL starvation caused by blocking synchronous Python code. "
                        "Consider offloading heavy blocking operations to VorteExecutor or run_in_executor."
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Vorte DX] Error in event loop lag detector: {e}")
                await asyncio.sleep(interval)
 
    def _setup_middleware(self) -> None:
        """Setup core middleware."""
        # CORS
        self.fastapi.add_middleware(
            CORSMiddleware,
            allow_origins=self._settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    
    # _setup_lifecycle is superseded by the lifespan context manager in __init__.
    
    def _setup_builtin_routes(self) -> None:
        """Setup built-in routes."""
        
        # Mount framework assets
        assets_dir = Path(__file__).parent.parent / "assets"
        if assets_dir.exists():
            self.fastapi.mount(
                "/_vorte/assets",
                StaticFiles(directory=str(assets_dir)),
                name="vorte_assets",
            )

        @self.fastapi.get("/favicon.ico", include_in_schema=False)
        async def favicon():
            from fastapi.responses import FileResponse
            favicon_path = Path(__file__).parent.parent / "assets" / "favicon" / "favicon.ico"
            if favicon_path.exists():
                return FileResponse(str(favicon_path))
            return Response(status_code=404)

        if self._settings.app_debug:
            from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
            
            @self.fastapi.get("/docs", include_in_schema=False)
            async def custom_swagger_ui_html():
                return get_swagger_ui_html(
                    openapi_url=self.fastapi.openapi_url,
                    title=self.fastapi.title + " - Swagger UI",
                    oauth2_redirect_url=self.fastapi.swagger_ui_oauth2_redirect_url,
                    swagger_favicon_url="/_vorte/assets/favicon/favicon.ico",
                )

            @self.fastapi.get("/redoc", include_in_schema=False)
            async def custom_redoc_html():
                # FastAPI's get_redoc_html() uses `redoc@next` which is a dead tag on
                # jsDelivr and returns 404, causing a blank page.  We pin to a stable
                # release instead and serve our own favicon.
                from fastapi.responses import HTMLResponse
                openapi_url = self.fastapi.openapi_url
                title = self.fastapi.title + " - ReDoc"
                html = f"""<!DOCTYPE html>
<html>
  <head>
    <title>{title}</title>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">
    <link rel="icon" type="image/x-icon" href="/_vorte/assets/favicon/favicon.ico">
    <style>body {{ margin: 0; padding: 0; }}</style>
  </head>
  <body>
    <redoc spec-url='{openapi_url}'></redoc>
    <script src="https://cdn.jsdelivr.net/npm/redoc@2.1.5/bundles/redoc.standalone.js"></script>
  </body>
</html>"""
                return HTMLResponse(html)


        @self.fastapi.get("/health", include_in_schema=False)
        async def health_check():
            """Full system health check."""
            results = await self._module_registry.health_check_all()
            all_healthy = all(
                r.get("status") in ("healthy", "ready") 
                for r in results.values()
            )
            return JSONResponse(
                content={
                    "status": "healthy" if all_healthy else "degraded",
                    "modules": results,
                },
                status_code=200 if all_healthy else 503,
            )
        
        @self.fastapi.get("/ready", include_in_schema=False)
        async def readiness_check():
            """Kubernetes readiness probe."""
            return JSONResponse(content={"status": "ready"})
        
        @self.fastapi.get("/live", include_in_schema=False)
        async def liveness_check():
            """Kubernetes liveness probe."""
            return JSONResponse(content={"status": "alive"})
        
        @self.fastapi.get("/_vorte/info", include_in_schema=False)
        async def vorte_info():
            """Framework and runtime information."""
            import platform
            import sys
            return JSONResponse(content={
                "framework": "Vorte",
                "version": "1.0.0",
                "python": platform.python_version(),
                "platform": platform.system(),
                "modules_loaded": len(self._module_registry.get_all()),
                "routes": len(self.get_routes()),
            })

        @self.fastapi.get("/_vorte/metrics", include_in_schema=False)
        async def prometheus_metrics():
            """Expose thread-safe Prometheus metrics."""
            try:
                from vorte._vorte_engine import MetricsCollector
                mc = MetricsCollector()
                serialization_time = mc.serialization_time_ns
                database_wait = mc.database_wait_time_ns
                scheduling_latency = mc.scheduling_latency_ns
                event_loop_lag = mc.event_loop_lag_ns
                buffered_spans = mc.buffered
                capacity = mc.capacity
            except ImportError:
                serialization_time = 0
                database_wait = 0
                scheduling_latency = 0
                event_loop_lag = 0
                buffered_spans = 0
                capacity = 0

            lines = [
                "# HELP vorte_serialization_time_ns Total JSON/MessagePack/CBOR serialization time in nanoseconds",
                "# TYPE vorte_serialization_time_ns counter",
                f"vorte_serialization_time_ns {serialization_time}",
                "",
                "# HELP vorte_database_wait_time_ns Total database query execution wait time in nanoseconds",
                "# TYPE vorte_database_wait_time_ns counter",
                f"vorte_database_wait_time_ns {database_wait}",
                "",
                "# HELP vorte_scheduling_latency_ns Total scheduler worker task queue wait/scheduling latency in nanoseconds",
                "# TYPE vorte_scheduling_latency_ns counter",
                f"vorte_scheduling_latency_ns {scheduling_latency}",
                "",
                "# HELP vorte_event_loop_lag_ns Total event-loop lag / scheduling latency in nanoseconds",
                "# TYPE vorte_event_loop_lag_ns counter",
                f"vorte_event_loop_lag_ns {event_loop_lag}",
                "",
                "# HELP vorte_buffered_spans_total Number of request trace spans currently in the native ring buffer",
                "# TYPE vorte_buffered_spans_total gauge",
                f"vorte_buffered_spans_total {buffered_spans}",
                "",
                "# HELP vorte_metrics_buffer_capacity_total Maximum ring buffer capacity of the metrics collector",
                "# TYPE vorte_metrics_buffer_capacity_total gauge",
                f"vorte_metrics_buffer_capacity_total {capacity}"
            ]
            content = "\n".join(lines) + "\n"
            return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")
    
    def _setup_dashboard_api(self) -> None:
        """Setup dashboard API routes for the admin panel."""
        from fastapi import Depends
        
        @self.fastapi.get("/_vorte/dashboard/overview", include_in_schema=False, dependencies=[Depends(verify_dashboard_access)])
        async def dashboard_overview():
            """Dashboard overview data — modules, routes, metrics, uptime."""
            import platform
            from vorte import __version__
            uptime = time.time() - self._start_time if self._start_time else 0
            modules = self._module_registry.list_modules()
            routes = self.get_routes()
            
            memory_mb = 0
            if platform.system() == "Windows":
                try:
                    import ctypes
                    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                        _fields_ = [
                            ("cb", ctypes.c_ulong),
                            ("PageFaultCount", ctypes.c_ulong),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t),
                        ]
                    GetProcessMemoryInfo = ctypes.windll.psapi.GetProcessMemoryInfo
                    GetCurrentProcess = ctypes.windll.kernel32.GetCurrentProcess
                    counters = PROCESS_MEMORY_COUNTERS()
                    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                    if GetProcessMemoryInfo(GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                        memory_mb = round(counters.WorkingSetSize / (1024 * 1024), 1)
                except Exception:
                    pass
            else:
                try:
                    import resource
                    memory_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
                except Exception:
                    pass

            return JSONResponse(content={
                "framework": {
                    "name": "Vorte",
                    "version": __version__,
                    "python": platform.python_version(),
                    "platform": platform.system(),
                },
                "app": {
                    "name": self._settings.app_name,
                    "env": self._settings.app_env,
                    "debug": self._settings.app_debug,
                    "uptime_seconds": round(uptime, 1),
                    "api_prefix": self._settings.api_prefix,
                    "default_version": self._settings.default_version,
                },
                "modules": {
                    "total": len(modules),
                    "healthy": sum(1 for m in modules if m.get("state") == "ready"),
                    "failed": sum(1 for m in modules if m.get("state") == "failed"),
                    "items": modules,
                },
                "routes": {
                    "total": len(routes),
                    "items": routes[:100],
                },
                "metrics": self._request_metrics,
                "system": {
                    "cpu_percent": 0,
                    "memory_mb": memory_mb,
                    "pid": __import__('os').getpid(),
                },
            })
        
        @self.fastapi.get("/_vorte/dashboard/modules", include_in_schema=False, dependencies=[Depends(verify_dashboard_access)])
        async def dashboard_modules():
            """Detailed module information."""
            modules = self._module_registry.list_modules()
            return JSONResponse(content={
                "total": len(modules),
                "modules": modules,
            })
        
        @self.fastapi.get("/_vorte/dashboard/routes", include_in_schema=False, dependencies=[Depends(verify_dashboard_access)])
        async def dashboard_routes():
            """All registered routes."""
            routes = self.get_routes()
            return JSONResponse(content={
                "total": len(routes),
                "routes": routes,
            })
        
        @self.fastapi.get("/_vorte/dashboard/health", include_in_schema=False, dependencies=[Depends(verify_dashboard_access)])
        async def dashboard_health():
            """Health check details."""
            results = await self._module_registry.health_check_all()
            return JSONResponse(content={
                "status": "healthy" if all(
                    r.get("status") in ("healthy", "ready") for r in results.values()
                ) else "degraded",
                "modules": results,
            })
        
        @self.fastapi.get("/_vorte/dashboard/config", include_in_schema=False, dependencies=[Depends(verify_dashboard_access)])
        async def dashboard_config():
            """Non-sensitive configuration."""
            s = self._settings
            return JSONResponse(content={
                "app_name": s.app_name,
                "app_env": s.app_env,
                "app_debug": s.app_debug,
                "app_url": s.app_url,
                "api_prefix": s.api_prefix,
                "default_version": s.default_version,
                "timezone": s.timezone,
                "cors_origins": s.cors_origins,
                "database": {"pool_size": s.database.pool_size, "echo": s.database.echo},
                "auth": {"strategy": s.auth.strategy, "mfa": s.auth.mfa, "refresh_tokens": s.auth.refresh_tokens},
                "ai": {"default_model": s.ai.default_model, "max_tokens": s.ai.max_tokens, "temperature": s.ai.temperature},
                "cache": {"driver": s.cache.driver, "default_ttl": s.cache.default_ttl},
                "queue": {"driver": s.queue.driver, "concurrency": s.queue.concurrency},
                "storage": {"driver": s.storage.driver},
                "security": {"helmet": s.security.helmet, "csrf": s.security.csrf, "rate_limit": s.security.rate_limit},
                "dashboard": {"enabled": s.dashboard.enabled, "path": s.dashboard.path},
            })
        
        @self.fastapi.get("/_vorte/dashboard/events", include_in_schema=False, dependencies=[Depends(verify_dashboard_access)])
        async def dashboard_events():
            """Registered events and listeners."""
            return JSONResponse(content={
                "events": {
                    name: len(handlers) 
                    for name, handlers in self._events.items()
                }
            })
        
        @self.fastapi.get("/_vorte/dashboard/metrics", include_in_schema=False, dependencies=[Depends(verify_dashboard_access)])
        async def dashboard_metrics():
            """Request metrics."""
            return JSONResponse(content=self._request_metrics)
    
    @property
    def settings(self) -> Settings:
        """Get application settings."""
        return self._settings
    
    @property
    def modules(self) -> ModuleRegistry:
        """Get module registry."""
        return self._module_registry
    
    @property
    def container(self) -> Container:
        """Get dependency injection container."""
        return self._container

    @property
    def executor(self) -> VorteExecutor:
        """Work-stealing executor for sync/async route dispatch."""
        return self._executor

    @property
    def type_mirror(self) -> TypeMirror:
        """TypeScript type mirror populated at startup from route schemas."""
        return self._type_mirror

    @property
    def query_planner(self) -> QueryPlanner:
        """N+1 look-ahead query planner."""
        return self._query_planner
    
    @property
    def events(self) -> Dict[str, List[Callable]]:
        """Get event bus."""
        return self._events
    
    # ---- Module Registration ----
    
    def register(self, module_or_modules: Union[Module, List[Module]]) -> "Vorte":
        """Register one or more modules with the application."""
        modules = module_or_modules if isinstance(module_or_modules, list) else [module_or_modules]
        
        for mod in modules:
            self._module_registry.register(mod)
            
            # If the app is already initializing or ready, we need to register this module immediately
            # This allows late registration of modules.
            if hasattr(self, '_module_registry'):
                from vorte.core.module import ModuleState
                try:
                    mod.app = self
                    mod.state = ModuleState.INITIALIZING
                    mod.register(self)
                    mod.state = ModuleState.READY
                except Exception as e:
                    mod.state = ModuleState.FAILED
                    print(f"Warning: Late registration of module '{mod.meta.name}' failed: {e}")
                    
        return self
    
    # ---- Route Registration (FastAPI pass-through) ----
    
    def get(self, path: str, **kwargs):
        """Register a GET route."""
        return self.fastapi.get(path, **kwargs)
    
    def post(self, path: str, **kwargs):
        """Register a POST route."""
        return self.fastapi.post(path, **kwargs)
    
    def put(self, path: str, **kwargs):
        """Register a PUT route."""
        return self.fastapi.put(path, **kwargs)
    
    def patch(self, path: str, **kwargs):
        """Register a PATCH route."""
        return self.fastapi.patch(path, **kwargs)
    
    def delete(self, path: str, **kwargs):
        """Register a DELETE route."""
        return self.fastapi.delete(path, **kwargs)
    
    def socket(self, path: str, **kwargs):
        """Register a WebSocket route."""
        return self.fastapi.websocket(path, **kwargs)
    
    def include_router(self, router: Any, **kwargs):
        """Include a router."""
        self.fastapi.include_router(router, **kwargs)
        return self
    
    def middleware(self, middleware_type: str):
        """Decorator to add middleware to the application.
        
        Usage:
            @app.middleware("http")
            async def my_middleware(request, call_next):
                ...
        """
        return self.fastapi.middleware(middleware_type)
    
    def exception_handler(self, exc_class_or_code: Union[int, Type[Exception]]):
        """Decorator to add an exception handler to the application.
        
        Usage:
            @app.exception_handler(StarletteHTTPException)
            async def http_exception_handler(request, exc):
                ...
        """
        return self.fastapi.exception_handler(exc_class_or_code)
    
    # ---- Event System ----
    
    def on(self, event_name: str) -> Callable:
        """Decorator to listen for an event.
        
        Usage:
            @app.on('order.created')
            async def handle_order_created(event):
                ...
        """
        def decorator(func: Callable) -> Callable:
            if event_name not in self._events:
                self._events[event_name] = []
            self._events[event_name].append(func)
            return func
        return decorator
    
    async def emit(self, event_name: str, data: Any = None, room: Optional[str] = None) -> None:
        """Emit an event to all listeners."""
        if event_name not in self._events:
            return
        
        for handler in self._events[event_name]:
            try:
                result = handler(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                # Log error but don't fail other handlers
                print(f"Event handler error for '{event_name}': {e}")
    
    # ---- Lifecycle Hooks ----
    
    def on_startup(self, func: Callable) -> Callable:
        """Add a startup hook."""
        self._startup_hooks.append(func)
        return func
    
    def on_shutdown(self, func: Callable) -> Callable:
        """Add a shutdown hook."""
        self._shutdown_hooks.append(func)
        return func

    # ---- Testing Helpers ----

    async def _run_startup(self) -> None:
        """Trigger the startup sequence manually.

        Intended for use in tests that need to simulate the ASGI startup
        phase without actually serving HTTP traffic.  Mirrors the logic
        inside the lifespan context manager.
        """
        import time as _time
        self._start_time = _time.time()
        await self._module_registry.startup_all()
        for hook in self._startup_hooks:
            await hook()

    async def _run_shutdown(self) -> None:
        """Trigger the shutdown sequence manually.

        Pair to :meth:`_run_startup` for test teardown.
        """
        for hook in self._shutdown_hooks:
            await hook()
        await self._module_registry.shutdown_all()
    
    # ---- Configuration ----
    
    def configure(self, **kwargs) -> "Vorte":
        """Configure the application."""
        for key, value in kwargs.items():
            if hasattr(self._settings, key):
                setattr(self._settings, key, value)
        return self
    
    def use_config(self, config_module_path: str) -> "Vorte":
        """Load configuration from a Python module path.
        
        Usage:
            app.use_config('config.app')
        """
        import importlib
        config = importlib.import_module(config_module_path)
        for key in dir(config):
            if key.isupper() and not key.startswith("_"):
                setattr(self._settings, key, getattr(config, key))
        return self
    
    # ---- Utility Methods ----
    
    def add_middleware(self, middleware_class: type, **kwargs) -> "Vorte":
        """Add middleware to the application."""
        self.fastapi.add_middleware(middleware_class, **kwargs)
        return self
    
    def mount(self, path: str, app: Any, **kwargs) -> "Vorte":
        """Mount a sub-application."""
        self.fastapi.mount(path, app, **kwargs)
        return self
    
    def get_routes(self) -> List[Dict[str, Any]]:
        """Get all registered routes with complete metadata for DX dashboard."""
        routes = []
        for route in self.fastapi.routes:
            if hasattr(route, "path"):
                methods_list = []
                if hasattr(route, "methods") and route.methods:
                    methods_list = list(route.methods)
                
                # Determine primary HTTP method (defaulting to GET, or WS for websockets)
                primary_method = "GET"
                if methods_list:
                    # Filter out HEAD/OPTIONS to find the primary method
                    filtered_methods = [m for m in methods_list if m not in ("HEAD", "OPTIONS")]
                    if filtered_methods:
                        primary_method = filtered_methods[0]
                    else:
                        primary_method = methods_list[0]
                elif route.__class__.__name__ in ("APIWebSocketRoute", "WebSocketRoute"):
                    primary_method = "WS"
                
                # Determine handler name and module name
                handler_name = "-"
                module_name = "core"
                if hasattr(route, "endpoint") and route.endpoint:
                    if hasattr(route.endpoint, "__name__"):
                        handler_name = route.endpoint.__name__
                    elif hasattr(route.endpoint, "__class__"):
                        handler_name = route.endpoint.__class__.__name__
                        
                    # Determine Vorte module name
                    if hasattr(route.endpoint, "__module__") and route.endpoint.__module__:
                        mod_parts = route.endpoint.__module__.split(".")
                        if len(mod_parts) > 2 and mod_parts[0] == "vorte" and mod_parts[1] == "modules":
                            module_name = mod_parts[2]
                
                routes.append({
                    "path": route.path,
                    "methods": methods_list,
                    "method": primary_method,
                    "name": getattr(route, "name", "-"),
                    "handler": handler_name,
                    "module": module_name,
                    "tags": getattr(route, "tags", []),
                })
        return routes
    
    def get_module_health(self) -> Dict[str, Any]:
        """Get health status of all modules."""
        return self._module_registry.health_check_all()
    
    # ---- Auto-Load ----
    
    def use_all_modules(
        self,
        exclude: Optional[List[str]] = None,
        dashboard: bool = True,
        load_extra: bool = False,
    ) -> "Vorte":
        """
        Register built-in modules with the application.
        
        By default (when load_extra=False), only registers the core modules:
        logging, database, cache, queue, security, auth, ai, storage, i18n, and dashboard.
        When load_extra=True, all 22 built-in modules are registered.
        
        Args:
            exclude: Module names to skip (e.g. ['mpesa', 'payments']).
            dashboard: Whether to include the DashboardModule.
            load_extra: Whether to load all extra modules as well.
        
        Returns:
            self for chaining.
        
        Usage:
            app = Vorte()
            app.use_all_modules(exclude=['mpesa'])
        """
        exclude = set(exclude or [])
        if not dashboard:
            exclude.add('dashboard')
        
        builtin = self._get_builtin_modules()
        for name, module_cls in builtin.items():
            if not load_extra and name not in self._CORE_MODULES:
                continue
            if name not in exclude:
                try:
                    self._module_registry.register(module_cls())
                except Exception as e:
                    print(f"Warning: Could not auto-load module '{name}': {e}")
        
        return self
    
    def record_request(self, path: str, method: str, status_code: int, latency_ms: float) -> None:
        """Record a request metric for the dashboard."""
        self._request_metrics['total'] += 1
        if status_code >= 400:
            self._request_metrics['errors'] += 1
        
        path_key = path
        if path_key not in self._request_metrics['by_path']:
            self._request_metrics['by_path'][path_key] = {'count': 0, 'errors': 0, 'total_ms': 0}
        self._request_metrics['by_path'][path_key]['count'] += 1
        if status_code >= 400:
            self._request_metrics['by_path'][path_key]['errors'] += 1
        self._request_metrics['by_path'][path_key]['total_ms'] += latency_ms
        
        method_key = method
        if method_key not in self._request_metrics['by_method']:
            self._request_metrics['by_method'][method_key] = 0
        self._request_metrics['by_method'][method_key] += 1
        
        # Keep last 50 requests
        self._request_metrics['last_requests'].append({
            'path': path,
            'method': method,
            'status': status_code,
            'latency_ms': round(latency_ms, 2),
            'time': time.strftime('%H:%M:%S'),
        })
        self._request_metrics['last_requests'] = self._request_metrics['last_requests'][-50:]


async def verify_dashboard_access(request: Request) -> None:
    """FastAPI dependency to secure dashboard routes."""
    from fastapi import HTTPException, status
    app = request.app
    # If app is FastAPI instance but vorte is attached (or if app is Vorte itself)
    vorte_app = getattr(app, "vorte", None) or app
    settings = vorte_app.settings

    # Bypass static asset requests
    path = request.url.path
    if path.endswith((".css", ".js", ".png", ".ico", ".jpg", ".jpeg", ".svg")):
        return

    if not settings.dashboard.auth_required:
        return

    # 1. Try token-based auth
    token = request.headers.get("X-Dashboard-Token")
    if not token:
        # Check query param
        token = request.query_params.get("token")
    if not token:
        # Check Authorization Bearer header
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    # Match token
    expected_token = settings.dashboard.token or settings.app_key
    
    # If no expected token is configured, check if we generated a runtime one
    if not expected_token and hasattr(vorte_app, "_dashboard_runtime_token"):
        expected_token = vorte_app._dashboard_runtime_token

    if expected_token and token == expected_token:
        return

    # 2. Try admin user auth (using resolved user)
    try:
        from vorte.modules.auth.guards import resolve_user
        user = await resolve_user(request)
        if user and (user.role == "admin" or user.is_admin):
            return
    except Exception:
        pass

    # Unauthorized
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "UNAUTHORIZED",
            "message": "Dashboard access denied. Provide a valid X-Dashboard-Token header, token query parameter, or authenticate as an admin user.",
        },
    )

