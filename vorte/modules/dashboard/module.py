"""
Vorte Dashboard Module
=======================
Built-in admin dashboard that auto-mounts at /vorte/dashboard.
Provides a modern web UI for monitoring modules, routes, health, and metrics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, TYPE_CHECKING

from fastapi import Request
from vorte.core.module import Module, ModuleMeta, ModulePriority

if TYPE_CHECKING:
    from vorte.core.app import Vorte


class DashboardModule(Module):
    """
    Built-in admin dashboard module.

    Mounts a modern, responsive admin panel at /vorte/dashboard (configurable).
    The dashboard UI is a static Next.js build embedded in the package,
    so it works out of the box — no separate frontend build step needed.

    Configuration (via Settings):
        DASHBOARD_ENABLED: Enable/disable the dashboard (default: True)
        DASHBOARD_PATH: Mount path (default: /vorte/dashboard)
        DASHBOARD_AUTH: Require authentication (default: True)

    Usage:
        # Auto-loaded when auto_load=True
        app = Vorte(auto_load=True)

        # Or manually
        from vorte.modules.dashboard import DashboardModule
        app.register(DashboardModule())
    """

    meta = ModuleMeta(
        name="dashboard",
        version="1.0.0",
        description="Built-in admin dashboard with real-time monitoring",
        priority=ModulePriority.DASHBOARD,
        dependencies=[],
        auto_discover=True,
    )

    def register(self, app: "Vorte") -> None:
        """Register the dashboard with the application."""
        from fastapi import Depends
        from vorte.core.app import verify_dashboard_access
        
        dashboard_path = app.settings.dashboard.path
        static_dir = Path(__file__).parent / "static"

        if static_dir.exists() and app.settings.dashboard.enabled:
            # Mount the static dashboard files
            from fastapi.staticfiles import StaticFiles
            from fastapi.responses import FileResponse

            # Catch-all route for SPA navigation
            @app.fastapi.get(dashboard_path + "/{full_path:path}", include_in_schema=False, dependencies=[Depends(verify_dashboard_access)])
            async def dashboard_spa(full_path: str, request: Request):
                """Serve the dashboard SPA."""
                from fastapi.responses import RedirectResponse
                if full_path == "index.html":
                    query_params = request.url.query
                    suffix = f"?{query_params}" if query_params else ""
                    clean_path = dashboard_path.rstrip('/')
                    return RedirectResponse(url=f"{clean_path}/{suffix}")

                file_path = static_dir / full_path
                if file_path.exists() and file_path.is_file():
                    return FileResponse(str(file_path))
                # Fallback to index.html for SPA routing
                index_path = static_dir / "index.html"
                if index_path.exists():
                    return FileResponse(str(index_path))
                return {"error": "Dashboard not built. Run: vorte dashboard:build"}

            from fastapi.responses import RedirectResponse

            @app.fastapi.get(dashboard_path, include_in_schema=False, dependencies=[Depends(verify_dashboard_access)])
            async def dashboard_index(request: Request):
                """Redirect bare route to trailing slash route so relative assets load correctly."""
                query_params = request.url.query
                suffix = f"?{query_params}" if query_params else ""
                clean_path = dashboard_path.rstrip('/')
                return RedirectResponse(url=f"{clean_path}/{suffix}")
        else:
            # No static files — redirect to API docs
            @app.fastapi.get(dashboard_path, include_in_schema=False, dependencies=[Depends(verify_dashboard_access)])
            async def dashboard_placeholder():
                return {
                    "message": "Vorte Dashboard",
                    "note": "Static UI not built. The API endpoints at /_vorte/dashboard/* are available.",
                    "api_endpoints": [
                        "/_vorte/dashboard/overview",
                        "/_vorte/dashboard/modules",
                        "/_vorte/dashboard/routes",
                        "/_vorte/dashboard/health",
                        "/_vorte/dashboard/config",
                        "/_vorte/dashboard/events",
                        "/_vorte/dashboard/metrics",
                        "/_vorte/dashboard/logs",
                    ],
                }

        @app.fastapi.get("/_vorte/dashboard/logs", include_in_schema=False, dependencies=[Depends(verify_dashboard_access)])
        async def dashboard_logs():
            """Fetch recent logs from the logging module."""
            from vorte.modules.logging import logger
            # Ensure ring buffer handler is attached (in case LoggingModule wasn't registered)
            logger._attach_ring_handler()
            return {"logs": logger.get_logs()}


    async def health_check(self) -> Dict[str, Any]:
        return {
            "module": self.meta.name,
            "status": "healthy",
            "path": self.meta.name,
            "info": "Built-in admin dashboard",
        }
