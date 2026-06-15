import pytest
from vorte import Vorte, ModulePriority
from vorte.core.module import Module, ModuleMeta, ModuleState
from vorte.testing import VorteTestClient

class CustomTestModule(Module):
    meta = ModuleMeta(
        name="custom_test_module",
        description="A custom module for testing app integration",
        priority=ModulePriority.ROUTES,
    )

    def __init__(self, **config):
        super().__init__(**config)
        self.registered_called = False
        self.startup_called = False
        self.shutdown_called = False

    def register(self, app: Vorte) -> None:
        self.registered_called = True
        
        # Register a custom route for this module
        @app.get("/custom-module-route")
        async def custom_route():
            return {"status": "ok", "module": self.meta.name}

    async def on_startup(self) -> None:
        self.startup_called = True

    async def on_shutdown(self) -> None:
        self.shutdown_called = True


@pytest.mark.asyncio
async def test_app_init_without_autoload():
    """Test app initialization when auto_load=False."""
    app = Vorte(auto_load=False, title="NoAutoLoadApp", version="2.0.0", dashboard=False)
    assert app.fastapi.title == "NoAutoLoadApp"
    assert app.fastapi.version == "2.0.0"
    assert len(app.modules.get_all()) == 0


@pytest.mark.asyncio
async def test_app_init_with_autoload():
    """Test app initialization when auto_load=True."""
    # Exclude dashboard for faster and simpler test loading
    app = Vorte(auto_load=True, exclude_modules=["dashboard"])
    # Should load all modules except dashboard
    loaded_modules = app.modules.get_all()
    assert len(loaded_modules) > 0
    assert "dashboard" not in loaded_modules
    assert "auth" in loaded_modules
    assert "ai" in loaded_modules


@pytest.mark.asyncio
async def test_app_lifecycle_and_custom_module():
    """Test app lifecycle, startup/shutdown events, and module loading."""
    app = Vorte(auto_load=False)
    module = CustomTestModule()
    
    app.register(module)
    assert module.registered_called is True
    assert module.get_state() == ModuleState.READY
    assert module.app == app

    startup_hook_called = False
    shutdown_hook_called = False

    @app.on_startup
    async def app_startup():
        nonlocal startup_hook_called
        startup_hook_called = True

    @app.on_shutdown
    async def app_shutdown():
        nonlocal shutdown_hook_called
        shutdown_hook_called = True

    # Simulate ASGI startup via the testing helper
    await app._run_startup()

    assert module.startup_called is True
    assert startup_hook_called is True

    # Test the custom route registered by the module
    async with VorteTestClient(app) as client:
        response = await client.get("/custom-module-route")
        assert response.status_code == 200
        assert response.json_data == {"status": "ok", "module": "custom_test_module"}

    # Simulate ASGI shutdown via the testing helper
    await app._run_shutdown()

    assert module.shutdown_called is True
    assert shutdown_hook_called is True


@pytest.mark.asyncio
async def test_built_in_endpoints():
    """Test ready, live, and info endpoints."""
    app = Vorte(auto_load=False, dashboard=False)
    
    async with VorteTestClient(app) as client:
        # Readiness Probe
        resp = await client.get("/ready")
        assert resp.status_code == 200
        assert resp.json_data == {"status": "ready"}

        # Liveness Probe
        resp = await client.get("/live")
        assert resp.status_code == 200
        assert resp.json_data == {"status": "alive"}

        # Framework Info
        resp = await client.get("/_vorte/info")
        assert resp.status_code == 200
        data = resp.json_data
        assert data["framework"] == "Vorte"
        assert "python" in data
        assert "platform" in data
        assert data["modules_loaded"] == 0


@pytest.mark.asyncio
async def test_health_check_endpoint():
    """Test health check endpoint with active modules."""
    app = Vorte(auto_load=False)
    
    # Register custom module
    module = CustomTestModule()
    app.register(module)

    # Directly run startup to set app.events or other properties if any
    await app._run_startup()

    async with VorteTestClient(app) as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json_data
        assert data["status"] == "healthy"
        assert "custom_test_module" in data["modules"]
        assert data["modules"]["custom_test_module"]["status"] == "healthy"

    # Simulate a degraded module status
    class BrokenModule(Module):
        meta = ModuleMeta(name="broken_module")
        def register(self, app): pass
        async def health_check(self):
            return {"module": "broken_module", "status": "unhealthy", "error": "Database down"}

    app2 = Vorte(auto_load=False)
    app2.register(BrokenModule())
    
    async with VorteTestClient(app2) as client:
        resp = await client.get("/health")
        assert resp.status_code == 503
        data = resp.json_data
        assert data["status"] == "degraded"
        assert data["modules"]["broken_module"]["status"] == "unhealthy"


@pytest.mark.asyncio
async def test_metrics_and_dashboard_api():
    """Test request recording metrics and dashboard endpoint metrics."""
    app = Vorte(auto_load=False)
    app.settings.dashboard.auth_required = False
    
    # Record a successful request
    app.record_request("/api/users", "GET", 200, 45.2)
    assert app._request_metrics["total"] == 1
    assert app._request_metrics["errors"] == 0
    assert app._request_metrics["by_path"]["/api/users"]["count"] == 1
    assert app._request_metrics["by_path"]["/api/users"]["total_ms"] == 45.2

    # Record a failed request
    app.record_request("/api/users", "POST", 500, 120.8)
    assert app._request_metrics["total"] == 2
    assert app._request_metrics["errors"] == 1
    assert app._request_metrics["by_path"]["/api/users"]["count"] == 2
    assert app._request_metrics["by_path"]["/api/users"]["errors"] == 1
    assert app._request_metrics["by_method"]["POST"] == 1

    # Check dashboard config endpoint
    async with VorteTestClient(app) as client:
        resp = await client.get("/_vorte/dashboard/config")
        assert resp.status_code == 200
        assert resp.json_data["app_name"] == "VorteApp"

        # Check dashboard metrics endpoint
        resp = await client.get("/_vorte/dashboard/metrics")
        assert resp.status_code == 200
        assert resp.json_data["total"] == 2
        assert resp.json_data["errors"] == 1

        # Check dashboard overview endpoint
        resp = await client.get("/_vorte/dashboard/overview")
        assert resp.status_code == 200
        assert resp.json_data["app"]["name"] == "VorteApp"
        assert resp.json_data["metrics"]["total"] == 2


def test_cli_version(capsys):
    """Test vorte --version and -v outputs."""
    from vorte.cli.main import cli
    import sys
    
    # Mock sys.argv
    orig_argv = sys.argv
    try:
        sys.argv = ["vorte", "--version"]
        cli()
        captured = capsys.readouterr()
        assert "Vorte Framework v" in captured.out
        
        sys.argv = ["vorte", "-v"]
        cli()
        captured = capsys.readouterr()
        assert "Vorte Framework v" in captured.out
    finally:
        sys.argv = orig_argv


def test_non_api_route_registration():
    """Test that Route, WebSocketRoute, and Mount register properly."""
    from fastapi import FastAPI
    from starlette.routing import Route, WebSocketRoute, Mount
    from vorte.engine import VorteEngine
    
    app = FastAPI()
    
    async def dummy_endpoint():
        return {"ok": True}
        
    app.routes.append(Route("/starlette-route", dummy_endpoint, methods=["GET"]))
    app.routes.append(WebSocketRoute("/ws-route", dummy_endpoint))
    app.routes.append(Mount("/mount-route", app=FastAPI()))
    
    engine = VorteEngine(app)
    # 1 route for Route + 1 for WebSocketRoute + 14 for Mount = 16
    assert engine.route_count >= 16


@pytest.mark.asyncio
async def test_custom_docs_favicon():
    """Test custom docs and redoc pages serve the Vorte favicon URL."""
    app = Vorte(auto_load=False)
    app.configure(app_debug=True)
    
    async with VorteTestClient(app) as client:
        # Check /docs UI HTML content
        docs_resp = await client.get("/docs")
        assert docs_resp.status_code == 200
        assert b"/_vorte/assets/favicon/favicon.ico" in docs_resp._response.content
        
        # Check /redoc UI HTML content
        redoc_resp = await client.get("/redoc")
        assert redoc_resp.status_code == 200
        assert b"/_vorte/assets/favicon/favicon.ico" in redoc_resp._response.content

        # Check /favicon.ico response is reachable
        favicon_resp = await client.get("/favicon.ico")
        assert favicon_resp.status_code in (200, 404)  # 404 is okay if asset doesn't exist, but it should not error out


@pytest.mark.asyncio
async def test_dashboard_security():
    """Test dashboard route security: auth required vs bypass, tokens, and role-based access."""
    # 1. Test Auth Bypass
    app_bypass = Vorte(auto_load=False)
    app_bypass.settings.dashboard.auth_required = False
    async with VorteTestClient(app_bypass) as client:
        resp = await client.get("/_vorte/dashboard/overview")
        assert resp.status_code == 200

    # 2. Test Auth Required - Unauthorized (No Token)
    app_auth = Vorte(auto_load=False)
    app_auth.settings.dashboard.auth_required = True
    app_auth.settings.dashboard.token = "secure-dash-token"
    async with VorteTestClient(app_auth) as client:
        resp = await client.get("/_vorte/dashboard/overview")
        assert resp.status_code == 401
        assert "Dashboard access denied" in resp.json_data["detail"]["message"]

    # 3. Test Auth Required - Authorized via Header
    async with VorteTestClient(app_auth) as client:
        resp = await client.get("/_vorte/dashboard/overview", headers={"X-Dashboard-Token": "secure-dash-token"})
        assert resp.status_code == 200

    # 4. Test Auth Required - Authorized via Query Param
    async with VorteTestClient(app_auth) as client:
        resp = await client.get("/_vorte/dashboard/overview", params={"token": "secure-dash-token"})
        assert resp.status_code == 200

    # 5. Test Auth Required - Authorized via Bearer Token
    async with VorteTestClient(app_auth) as client:
        resp = await client.get("/_vorte/dashboard/overview", headers={"Authorization": "Bearer secure-dash-token"})
        assert resp.status_code == 200

    # 6. Test Auth Required - Authorized via APP_KEY fallback
    app_key_auth = Vorte(auto_load=False)
    app_key_auth.settings.dashboard.auth_required = True
    app_key_auth.settings.dashboard.token = ""
    app_key_auth.settings.app_key = "vorte-app-key"
    async with VorteTestClient(app_key_auth) as client:
        # Invalid token
        resp = await client.get("/_vorte/dashboard/overview", headers={"X-Dashboard-Token": "wrong-token"})
        assert resp.status_code == 401
        # Valid app key
        resp = await client.get("/_vorte/dashboard/overview", headers={"X-Dashboard-Token": "vorte-app-key"})
        assert resp.status_code == 200

    # 7. Test Auth Required - Dynamic token generated in development environment by default
    app_default = Vorte(auto_load=False)
    assert hasattr(app_default, "_dashboard_runtime_token")
    assert len(app_default._dashboard_runtime_token) == 32

    # 8. Test Auth Required - Admin user authorization fallback
    from unittest.mock import patch
    from vorte.modules.auth.guards import CurrentUser

    app_admin = Vorte(auto_load=False)
    app_admin.settings.dashboard.auth_required = True
    app_admin.settings.dashboard.token = "secure-dash-token" # Non-matching token to force user auth fallback
    
    # 8a. Admin user should be allowed
    admin_user = CurrentUser(id="1", email="admin@vorte.dev", name="Admin User", role="admin")
    with patch("vorte.modules.auth.guards.resolve_user", return_value=admin_user):
        async with VorteTestClient(app_admin) as client:
            resp = await client.get("/_vorte/dashboard/overview")
            assert resp.status_code == 200

    # 8b. Regular user should be denied
    regular_user = CurrentUser(id="2", email="user@vorte.dev", name="Regular User", role="user")
    with patch("vorte.modules.auth.guards.resolve_user", return_value=regular_user):
        async with VorteTestClient(app_admin) as client:
            resp = await client.get("/_vorte/dashboard/overview")
            assert resp.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_assets_serving():
    """Test that dashboard SPA files (index.html, CSS, JS) are correctly served."""
    app = Vorte(auto_load=False)
    app.settings.dashboard.auth_required = False
    
    async with VorteTestClient(app) as client:
        # 1. Main index route
        resp = await client.get("/vorte/dashboard", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Vorte Admin Dashboard" in resp._response.content

        # 2. CSS asset route
        resp = await client.get("/vorte/dashboard/dashboard.css")
        assert resp.status_code == 200
        assert b"Vorte Admin Dashboard Stylesheet" in resp._response.content

        # 3. JS asset route
        resp = await client.get("/vorte/dashboard/dashboard.js")
        assert resp.status_code == 200
        assert b"Vorte Admin Dashboard Controller" in resp._response.content

        # 4. Fallback/spa routing check
        resp = await client.get("/vorte/dashboard/some-nonexistent-subpage")
        assert resp.status_code == 200
        assert b"Vorte Admin Dashboard" in resp._response.content

        # 5. Test that static assets bypass auth checks even when auth_required = True
        app_secure = Vorte(auto_load=False)
        app_secure.settings.dashboard.auth_required = True
        app_secure.settings.dashboard.token = "secure-dash-token"
        async with VorteTestClient(app_secure) as secure_client:
            # Main page should be unauthorized
            resp = await secure_client.get("/vorte/dashboard")
            assert resp.status_code == 401
            # CSS asset should bypass and succeed
            resp = await secure_client.get("/vorte/dashboard/dashboard.css")
            assert resp.status_code == 200
            # JS asset should bypass and succeed
            resp = await secure_client.get("/vorte/dashboard/dashboard.js")
            assert resp.status_code == 200





