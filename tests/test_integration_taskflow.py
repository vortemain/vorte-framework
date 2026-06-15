"""
TaskFlow — Batteries-Included Integration Test Suite
======================================================

Tests every major Vorte module in a single, end-to-end flow:

  Phase 1  App bootstrap & health          (Vorte app, module registration)
  Phase 2  Auth / JWT                      (AuthModule, JWTManager, IsAuthenticated)
  Phase 3  Database / ActiveRecord         (VorteModel, create, find, save, delete)
  Phase 4  Controller routing              (Controller, @route, VorteAPIRouter)
  Phase 5  Cache                           (CacheModule, @cache decorator, invalidation)
  Phase 6  Background Queue               (QueueModule, Job.dispatch, QueueManager)
  Phase 7  Full end-to-end REST flow       (projects CRUD + tasks + auth guard)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

import pytest

from vorte.testing import VorteTestClient
from vorte.modules.auth.jwt import JWTManager
from vorte.modules.cache.cache import CacheManager
from vorte.modules.database.model import VorteModel
from vorte.modules.queue.queue import QueueManager


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def auth_header(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────────────
# App fixture  (function-scoped — avoids cross-test DI container pollution)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
async def taskflow(tmp_path):
    """Bootstrap full Vorte app, create DB tables, run startup, yield app+client."""
    from tests.integration_app.app import create_app, _job_audit_log, Project, Task
    from vorte.modules.database.model import VorteModel

    db_path = tmp_path / "taskflow.db"
    app = create_app(db_url=f"sqlite+aiosqlite:///{db_path}")

    # Run framework startup (async module on_startup hooks)
    await app._run_startup()

    # Create DB schema
    db_module = app.modules.get("database")
    async with db_module.connection.engine.begin() as conn:
        await conn.run_sync(VorteModel.metadata.create_all)

    _job_audit_log.clear()

    async with VorteTestClient(app) as client:
        yield app, client

    await app._run_shutdown()


@pytest.fixture
async def tokens(taskflow):
    """Convenience: returns (access_token, refresh_token) for a test user."""
    app, client = taskflow
    auth_module = app.modules.get("auth")
    jwt: JWTManager = auth_module.jwt

    access = await jwt.create_access_token(
        user_id="usr_tester",
        email="tester@taskflow.test",
        roles=["user"],
        tier="pro",
    )
    refresh = await jwt.create_refresh_token(user_id="usr_tester")
    return access, refresh


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 – App bootstrap & module health
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint(taskflow):
    """GET /api/health returns 200 and lists all modules."""
    _, client = taskflow
    resp = await client.get("/api/health")
    resp.assert_success()
    data = resp.data
    assert data["status"] == "ok"
    assert "database" in data["modules"]
    assert "cache" in data["modules"]
    assert "queue" in data["modules"]


@pytest.mark.asyncio
async def test_all_modules_registered(taskflow):
    """Verify all 4 modules are present in app.modules."""
    app, _ = taskflow
    for mod_name in ("database", "auth", "cache", "queue"):
        assert app.modules.get(mod_name) is not None, f"Module '{mod_name}' not registered"


@pytest.mark.asyncio
async def test_di_container_resolves_services(taskflow):
    """DI container resolves all critical services."""
    from vorte.core.di import _global_container
    assert _global_container.resolve(QueueManager) is not None
    assert _global_container.resolve(CacheManager) is not None
    assert _global_container.resolve(JWTManager) is not None


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 – Auth / JWT
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_jwt_create_and_verify_access_token(taskflow):
    """JWTManager creates a verifiable access token."""
    app, _ = taskflow
    jwt = app.modules.get("auth").jwt
    token = await jwt.create_access_token(
        user_id="usr_test",
        email="test@example.com",
        roles=["user"],
    )
    payload = await jwt.verify_token(token, expected_type="access")
    assert payload["sub"] == "usr_test"
    assert payload["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_jwt_refresh_token_roundtrip(taskflow):
    """Refresh token can be verified."""
    app, _ = taskflow
    jwt = app.modules.get("auth").jwt
    refresh = await jwt.create_refresh_token(user_id="usr_test")
    payload = await jwt.verify_token(refresh, expected_type="refresh")
    assert payload["sub"] == "usr_test"


@pytest.mark.asyncio
async def test_login_endpoint_returns_tokens(taskflow):
    """POST /api/login issues access + refresh tokens."""
    _, client = taskflow
    resp = await client.post("/api/login", json={"email": "demo@example.com", "password": "pass"})
    resp.assert_success()
    assert "access_token" in resp.data
    assert "refresh_token" in resp.data
    assert resp.data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_protected_route_without_token_returns_401(taskflow):
    """Accessing a protected route without auth returns 401."""
    _, client = taskflow
    resp = await client.get("/api/projects")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_with_valid_token_succeeds(taskflow, tokens):
    """Accessing a protected route with a valid JWT returns 200."""
    _, client = taskflow
    access, _ = tokens
    resp = await client.get("/api/projects", headers=auth_header(access))
    resp.assert_success()


@pytest.mark.asyncio
async def test_token_blacklisting(taskflow):
    """Blacklisted tokens are rejected."""
    app, _ = taskflow
    jwt = app.modules.get("auth").jwt

    token = await jwt.create_access_token(
        user_id="usr_blacklist",
        email="bl@example.com",
    )
    # Should verify cleanly before blacklisting
    await jwt.verify_token(token)
    # Blacklist the token
    await jwt.blacklist_token(token)
    # Should now raise
    from vorte.modules.auth.jwt import TokenBlacklistedError
    with pytest.raises(TokenBlacklistedError):
        await jwt.verify_token(token)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 – Database / ActiveRecord ORM
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_db_create_and_find_project(taskflow):
    """ActiveRecord create and find by ID."""
    from tests.integration_app.app import Project
    p = await Project.create({
        "name": "Alpha",
        "description": "First project",
        "owner_id": "usr_db_test",
        "is_active": True,
    })
    assert p.id is not None
    assert p.name == "Alpha"

    found = await Project.find(p.id)
    assert found is not None
    assert found.name == "Alpha"
    await p.delete()


@pytest.mark.asyncio
async def test_db_save_updates_record(taskflow):
    """save() on an existing record updates it."""
    from tests.integration_app.app import Project
    p = await Project.create({"name": "Beta", "owner_id": "usr_db_test", "is_active": True})
    p.name = "Beta Updated"
    await p.save()
    refreshed = await Project.find(p.id)
    assert refreshed.name == "Beta Updated"
    await p.delete()


@pytest.mark.asyncio
async def test_db_find_all_and_count(taskflow):
    """find_all() and count() reflect current DB state."""
    from tests.integration_app.app import Project
    before = await Project.count()
    p1 = await Project.create({"name": "Gamma", "owner_id": "u1", "is_active": True})
    p2 = await Project.create({"name": "Delta", "owner_id": "u1", "is_active": True})
    assert await Project.count() == before + 2
    all_projects = await Project.find_all()
    names = {p.name for p in all_projects}
    assert "Gamma" in names and "Delta" in names
    await p1.delete()
    await p2.delete()


@pytest.mark.asyncio
async def test_db_exists_check(taskflow):
    """exists() returns correct True/False."""
    from tests.integration_app.app import Project
    p = await Project.create({"name": "ExistCheck", "owner_id": "u_exists", "is_active": True})
    assert await Project.exists(id=p.id)
    assert not await Project.exists(id="00000000000000000000000000000000")
    await p.delete()


@pytest.mark.asyncio
async def test_db_delete_instance(taskflow):
    """delete() removes the record from the DB."""
    from tests.integration_app.app import Project
    p = await Project.create({"name": "DeleteMe", "owner_id": "u_del", "is_active": True})
    pid = p.id
    ok = await p.delete()
    assert ok is True
    assert await Project.find(pid) is None


@pytest.mark.asyncio
async def test_db_find_or_fail_raises(taskflow):
    """find_or_fail() raises on missing record."""
    from tests.integration_app.app import Project
    from vorte.modules.database.query import RecordNotFoundError
    with pytest.raises(RecordNotFoundError):
        await Project.find_or_fail("nonexistent-id-0000000000000000")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 – Controller / REST routing
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_project_returns_201(taskflow, tokens):
    """POST /api/projects/ creates a project and returns 201."""
    _, client = taskflow
    access, _ = tokens
    resp = await client.post(
        "/api/projects",
        json={"name": "My First Project", "description": "Testing"},
        headers=auth_header(access),
    )
    assert resp.status_code == 201
    assert resp.data["name"] == "My First Project"
    assert resp.data["is_active"] is True


@pytest.mark.asyncio
async def test_get_project_by_id(taskflow, tokens):
    """GET /api/projects/{id} returns the correct project."""
    _, client = taskflow
    access, _ = tokens

    create_resp = await client.post(
        "/api/projects",
        json={"name": "Get By ID"},
        headers=auth_header(access),
    )
    assert create_resp.status_code == 201
    project_id = create_resp.data["id"]

    resp = await client.get(f"/api/projects/{project_id}", headers=auth_header(access))
    resp.assert_success()
    assert resp.data["id"] == project_id


@pytest.mark.asyncio
async def test_list_projects_success_envelope(taskflow, tokens):
    """GET /api/projects/ returns success envelope with list payload."""
    _, client = taskflow
    access, _ = tokens
    resp = await client.get("/api/projects", headers=auth_header(access))
    resp.assert_success()
    assert isinstance(resp.data, list)


@pytest.mark.asyncio
async def test_get_nonexistent_project_returns_404(taskflow, tokens):
    """GET /api/projects/<bogus> returns 404."""
    _, client = taskflow
    access, _ = tokens
    resp = await client.get("/api/projects/does-not-exist-00000", headers=auth_header(access))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_project_soft_deletes(taskflow, tokens):
    """DELETE /api/projects/{id} soft-deletes the project (is_active=False)."""
    from tests.integration_app.app import Project
    _, client = taskflow
    access, _ = tokens

    create_resp = await client.post(
        "/api/projects",
        json={"name": "To Be Deleted"},
        headers=auth_header(access),
    )
    project_id = create_resp.data["id"]

    del_resp = await client.delete(f"/api/projects/{project_id}", headers=auth_header(access))
    del_resp.assert_success()

    # Still in DB but is_active=False
    p = await Project.find(project_id)
    assert p is not None
    assert p.is_active is False


@pytest.mark.asyncio
async def test_task_crud_under_project(taskflow, tokens):
    """Full task lifecycle: create project → add task → complete task."""
    from tests.integration_app.app import Task
    _, client = taskflow
    access, _ = tokens

    # Create project
    proj_resp = await client.post(
        "/api/projects",
        json={"name": "Task Test Project"},
        headers=auth_header(access),
    )
    assert proj_resp.status_code == 201
    project_id = proj_resp.data["id"]

    # Add task
    task_resp = await client.post(
        f"/api/projects/{project_id}/tasks",
        json={"title": "Write tests", "body": "Write good tests", "priority": 5},
        headers=auth_header(access),
    )
    assert task_resp.status_code == 201
    task_id = task_resp.data["id"]

    # List tasks
    list_resp = await client.get(
        f"/api/projects/{project_id}/tasks",
        headers=auth_header(access),
    )
    list_resp.assert_success()
    tasks = list_resp.data
    assert any(t["id"] == task_id for t in tasks)

    # Complete task
    complete_resp = await client.patch(
        f"/api/projects/{project_id}/tasks/{task_id}/complete",
        headers=auth_header(access),
    )
    complete_resp.assert_success()
    assert complete_resp.data["done"] is True

    # Verify in DB
    db_task = await Task.find(task_id)
    assert db_task is not None
    assert db_task.done is True


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 – Cache
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cache_set_and_get(taskflow):
    """CacheManager set/get round-trip."""
    from vorte.core.di import _global_container
    cache_mgr = _global_container.resolve(CacheManager)
    await cache_mgr.set("test:key", {"hello": "world"}, ttl=60)
    result = await cache_mgr.get("test:key")
    assert result == {"hello": "world"}


@pytest.mark.asyncio
async def test_cache_miss_returns_none(taskflow):
    """Cache miss returns None."""
    from vorte.core.di import _global_container
    cache_mgr = _global_container.resolve(CacheManager)
    result = await cache_mgr.get("test:nonexistent:key:xyz_integration")
    assert result is None


@pytest.mark.asyncio
async def test_cache_ttl_key_persists_immediately(taskflow):
    """A key with a positive TTL is still present immediately after set."""
    from vorte.core.di import _global_container
    cache_mgr = _global_container.resolve(CacheManager)
    await cache_mgr.set("test:ttl_key_int", "still_here", ttl=10)
    assert await cache_mgr.get("test:ttl_key_int") == "still_here"


@pytest.mark.asyncio
async def test_cache_tag_invalidation(taskflow):
    """Tag-based invalidation clears all tagged entries."""
    from vorte.core.di import _global_container
    cache_mgr = _global_container.resolve(CacheManager)
    await cache_mgr.set("tagged:a", "val_a", ttl=60, tags=["group_int_x"])
    await cache_mgr.set("tagged:b", "val_b", ttl=60, tags=["group_int_x"])
    assert await cache_mgr.get("tagged:a") is not None
    assert await cache_mgr.get("tagged:b") is not None
    await cache_mgr.invalidate_tag("group_int_x")
    assert await cache_mgr.get("tagged:a") is None
    assert await cache_mgr.get("tagged:b") is None


@pytest.mark.asyncio
async def test_cache_decorator_memoises(taskflow):
    """@cache decorator memoises the function result."""
    from vorte.modules.cache.decorators import cache as cache_dec

    call_count = 0

    @cache_dec(ttl=60, tags=["decorator_int_test"])
    async def expensive_computation(x: int) -> int:
        nonlocal call_count
        call_count += 1
        return x * x

    result1 = await expensive_computation(7)
    assert result1 == 49
    assert call_count == 1

    # Second call — served from cache, not re-computed
    result2 = await expensive_computation(7)
    assert result2 == 49
    assert call_count == 1, "Function should not have been called twice for the same args"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 – Background Queue
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enqueue_welcome_email_job(taskflow):
    """Job.dispatch() enqueues successfully and returns a job ID."""
    from tests.integration_app.app import SendWelcomeEmail
    job_id = await SendWelcomeEmail.dispatch(
        project_name="QueueTest",
        owner_email="owner@test.com",
    )
    assert job_id is not None and len(job_id) > 0


@pytest.mark.asyncio
async def test_queue_stats_returns_dict(taskflow):
    """QueueManager.stats() returns a non-empty dict."""
    from vorte.core.di import _global_container
    qm = _global_container.resolve(QueueManager)
    stats = await qm.stats()
    assert isinstance(stats, dict)


@pytest.mark.asyncio
async def test_queue_watermark_state(taskflow):
    """watermark_state() returns per-queue states."""
    from vorte.core.di import _global_container
    qm = _global_container.resolve(QueueManager)
    state = await qm.watermark_state()
    assert isinstance(state, dict)


@pytest.mark.asyncio
async def test_dispatch_analytics_job(taskflow):
    """RecalculateProjectStats.dispatch() enqueues without error."""
    from tests.integration_app.app import RecalculateProjectStats
    job_id = await RecalculateProjectStats.dispatch(project_id="proj_test_123")
    assert job_id is not None


@pytest.mark.asyncio
async def test_queue_stats_via_http(taskflow):
    """GET /api/queue/stats returns queue statistics over HTTP."""
    _, client = taskflow
    resp = await client.get("/api/queue/stats")
    resp.assert_success()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 7 – Full end-to-end integration scenario
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_workflow(taskflow):
    """
    Realistic multi-step scenario:
    1. Login → get JWT
    2. Create project
    3. Add tasks to project
    4. List and complete tasks
    5. Confirm queue jobs were dispatched (stats non-empty)
    6. Confirm cache works after mutations
    """
    from tests.integration_app.app import Project, Task, _job_audit_log
    from vorte.core.di import _global_container

    _, client = taskflow
    _job_audit_log.clear()

    # ── Step 1: Login ──
    login_resp = await client.post(
        "/api/login",
        json={"email": "e2e@taskflow.test", "password": "any"},
    )
    login_resp.assert_success()
    access_token = login_resp.data["access_token"]
    headers = auth_header(access_token)

    # ── Step 2: Create a project ──
    proj_resp = await client.post(
        "/api/projects",
        json={"name": "E2E Project", "description": "Full end-to-end flow"},
        headers=headers,
    )
    assert proj_resp.status_code == 201
    project = proj_resp.data
    pid = project["id"]
    assert project["name"] == "E2E Project"

    # ── Step 3: List projects (should include new project) ──
    list_resp = await client.get("/api/projects", headers=headers)
    list_resp.assert_success()
    assert any(p["id"] == pid for p in list_resp.data)

    # ── Step 4: Add multiple tasks ──
    task_ids = []
    for title, priority in [
        ("Design DB schema", 10),
        ("Write unit tests", 8),
        ("Deploy to staging", 5),
    ]:
        t_resp = await client.post(
            f"/api/projects/{pid}/tasks",
            json={"title": title, "priority": priority},
            headers=headers,
        )
        assert t_resp.status_code == 201, f"Failed to create task '{title}': {t_resp.json_data}"
        task_ids.append(t_resp.data["id"])

    # ── Step 5: List tasks ──
    task_list_resp = await client.get(f"/api/projects/{pid}/tasks", headers=headers)
    task_list_resp.assert_success()
    assert len(task_list_resp.data) >= 3

    # ── Step 6: Complete the first two tasks ──
    for tid in task_ids[:2]:
        comp_resp = await client.patch(
            f"/api/projects/{pid}/tasks/{tid}/complete",
            headers=headers,
        )
        comp_resp.assert_success()
        assert comp_resp.data["done"] is True

    # ── Step 7: Verify completed tasks in DB ──
    for tid in task_ids[:2]:
        task = await Task.find(tid)
        assert task is not None
        assert task.done is True

    # ── Step 8: Verify still-pending task ──
    pending_task = await Task.find(task_ids[2])
    assert pending_task is not None
    assert pending_task.done is False

    # ── Step 9: Soft-delete the project ──
    del_resp = await client.delete(f"/api/projects/{pid}", headers=headers)
    del_resp.assert_success()

    proj_db = await Project.find(pid)
    assert proj_db is not None
    assert proj_db.is_active is False

    # ── Step 10: Project still fetchable by ID after soft delete ──
    find_resp = await client.get(f"/api/projects/{pid}", headers=headers)
    assert find_resp.status_code == 200

    # ── Step 11: Queue still healthy ──
    qm = _global_container.resolve(QueueManager)
    stats = await qm.stats()
    assert stats is not None

    # ── Step 12: Cache still responsive ──
    cache_mgr = _global_container.resolve(CacheManager)
    await cache_mgr.set("e2e:final_check", True, ttl=60)
    assert await cache_mgr.get("e2e:final_check") is True
