"""
TaskFlow – Vorte Batteries-Included Integration App
=====================================================

Exercises all the core modules working together in a realistic scenario:

  Module             What it tests
  ─────────────────  ─────────────────────────────────────────────────────────
  DatabaseModule     VorteModel, ActiveRecord CRUD, QueryBuilder, pagination
  AuthModule         JWT creation/verification, IsAuthenticated guard, login
  CacheModule        @cache decorator, manual get/set, tag invalidation
  QueueModule        Job.dispatch(), Job.handle(), background worker
  Controller / route  Controller-class routing with @route decorator
  DI / Container     Resolving CacheManager, QueueManager from container
  VorteResponse      success_response / error_response envelope
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from fastapi import Depends
from pydantic import BaseModel

from vorte import (
    Vorte,
    DatabaseModule,
    CacheModule,
    QueueModule,
    AuthModule,
    Controller,
    route,
    cache,
)
from vorte.core.response import success_response, error_response
from vorte.core.router import VorteAPIRouter
from vorte.modules.auth.guards import IsAuthenticated, CurrentUser
from vorte.modules.auth.jwt import JWTManager
from vorte.modules.cache.cache import CacheManager
from vorte.modules.database.model import VorteModel, StringField, BooleanField, IntegerField
from vorte.modules.queue.job import Job, register_job
from vorte.modules.queue.queue import QueueManager

# ─────────────────────────────────────────────────────────────────────────────
# 1. ORM Models
# ─────────────────────────────────────────────────────────────────────────────

class Project(VorteModel):
    __tablename__ = "tf_projects"
    name = StringField(max_length=255, nullable=False)
    description = StringField(max_length=1000, nullable=True)
    owner_id = StringField(max_length=64, nullable=False)
    is_active = BooleanField(default=True)


class Task(VorteModel):
    __tablename__ = "tf_tasks"
    title = StringField(max_length=255, nullable=False)
    body = StringField(max_length=5000, nullable=True)
    project_id = StringField(max_length=64, nullable=False)
    assignee_id = StringField(max_length=64, nullable=True)
    priority = IntegerField(default=0)
    done = BooleanField(default=False)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Background Jobs
# ─────────────────────────────────────────────────────────────────────────────

# Shared list to observe job side-effects in tests
_job_audit_log: List[Dict[str, Any]] = []


@register_job
class SendWelcomeEmail(Job):
    """Sends a welcome email (simulated) when a new project is created."""
    queue = "emails"
    retries = 2
    priority = 5

    async def handle(self, project_name: str, owner_email: str, **kwargs) -> None:
        _job_audit_log.append({
            "job": "SendWelcomeEmail",
            "project_name": project_name,
            "owner_email": owner_email,
        })


@register_job
class RecalculateProjectStats(Job):
    """Recalculates task statistics for a project asynchronously."""
    queue = "analytics"
    retries = 3
    priority = 3

    async def handle(self, project_id: str, **kwargs) -> None:
        _job_audit_log.append({
            "job": "RecalculateProjectStats",
            "project_id": project_id,
        })


# ─────────────────────────────────────────────────────────────────────────────
# 3. Pydantic request schemas
# ─────────────────────────────────────────────────────────────────────────────

class CreateProjectRequest(BaseModel):
    name: str
    description: Optional[str] = None


class CreateTaskRequest(BaseModel):
    title: str
    body: Optional[str] = None
    priority: int = 0


class LoginRequest(BaseModel):
    email: str
    password: str


# ─────────────────────────────────────────────────────────────────────────────
# 4. Controllers  (use _vorte_prefix — the correct Controller attribute)
# ─────────────────────────────────────────────────────────────────────────────

class AuthController(Controller):
    """Issues JWT tokens (demo login — not production-safe)."""
    _vorte_prefix = "/api"
    _vorte_tags = ["Auth"]

    @route.post("/login")
    async def login(self, body: LoginRequest):
        from vorte.core.di import _global_container
        try:
            jwt = _global_container.resolve(JWTManager)
        except Exception:
            return error_response("AUTH_ERROR", "JWT manager not available", 500)

        token = await jwt.create_access_token(
            user_id=f"usr_{body.email.split('@')[0]}",
            email=body.email,
            roles=["user"],
            tier="pro",
        )
        refresh = await jwt.create_refresh_token(
            user_id=f"usr_{body.email.split('@')[0]}"
        )
        return success_response({
            "access_token": token,
            "refresh_token": refresh,
            "token_type": "bearer",
        })


class HealthController(Controller):
    """Public health & diagnostics endpoints (no auth required)."""
    _vorte_prefix = "/api"
    _vorte_tags = ["Health"]

    @route.get("/health")
    async def health(self):
        return success_response({
            "status": "ok",
            "service": "taskflow",
            "modules": ["database", "auth", "cache", "queue"],
        })

    @route.get("/queue/stats")
    async def queue_stats(self):
        try:
            from vorte.core.di import _global_container
            qm = _global_container.resolve(QueueManager)
            stats = await qm.stats()
            return success_response(stats)
        except Exception as exc:
            return error_response("QUEUE_ERROR", str(exc), 500)

    @route.get("/audit-log")
    async def audit_log(self):
        return success_response(_job_audit_log)


class ProjectsController(Controller):
    """Full CRUD for Projects — exercises DB + Cache + Queue + Auth."""
    _vorte_prefix = "/api/projects"
    _vorte_tags = ["Projects"]

    @route.get("/")
    async def list_projects(self, user: CurrentUser = Depends(IsAuthenticated)):
        """List all active projects."""
        projects = await Project.find_all()
        return success_response([p.to_dict() for p in projects])

    @route.post("/")
    async def create_project(
        self,
        body: CreateProjectRequest,
        user: CurrentUser = Depends(IsAuthenticated),
    ):
        """Create a project and dispatch a welcome-email background job."""
        project = await Project.create({
            "name": body.name,
            "description": body.description,
            "owner_id": user.id,
            "is_active": True,
        })

        # Invalidate projects cache
        try:
            from vorte.core.di import _global_container
            cache_mgr = _global_container.resolve(CacheManager)
            await cache_mgr.invalidate_tag("projects")
        except Exception:
            pass

        # Dispatch background job
        await SendWelcomeEmail.dispatch(
            project_name=body.name,
            owner_email=user.email,
        )

        return success_response(project.to_dict(), status_code=201)

    @route.get("/{project_id}")
    async def get_project(
        self,
        project_id: str,
        user: CurrentUser = Depends(IsAuthenticated),
    ):
        project = await Project.find(project_id)
        if not project:
            return error_response("NOT_FOUND", f"Project {project_id!r} not found", 404)
        return success_response(project.to_dict())

    @route.delete("/{project_id}")
    async def delete_project(
        self,
        project_id: str,
        user: CurrentUser = Depends(IsAuthenticated),
    ):
        project = await Project.find(project_id)
        if not project:
            return error_response("NOT_FOUND", "Project not found", 404)
        project.is_active = False
        await project.save()

        try:
            from vorte.core.di import _global_container
            cache_mgr = _global_container.resolve(CacheManager)
            await cache_mgr.invalidate_tag("projects")
        except Exception:
            pass

        return success_response({"deleted": True})


class TasksController(Controller):
    """Task sub-resource management."""
    _vorte_prefix = "/api/projects/{project_id}/tasks"
    _vorte_tags = ["Tasks"]

    @route.get("/")
    async def list_tasks(
        self,
        project_id: str,
        user: CurrentUser = Depends(IsAuthenticated),
    ):
        tasks = await Task.query().where(Task.project_id == project_id).all()  # type: ignore[attr-defined]
        return success_response([t.to_dict() for t in tasks])

    @route.post("/")
    async def create_task(
        self,
        project_id: str,
        body: CreateTaskRequest,
        user: CurrentUser = Depends(IsAuthenticated),
    ):
        project = await Project.find(project_id)
        if not project:
            return error_response("NOT_FOUND", "Project not found", 404)

        task = await Task.create({
            "title": body.title,
            "body": body.body,
            "project_id": project_id,
            "assignee_id": user.id,
            "priority": body.priority,
            "done": False,
        })

        await RecalculateProjectStats.dispatch(project_id=project_id)
        return success_response(task.to_dict(), status_code=201)

    @route.patch("/{task_id}/complete")
    async def complete_task(
        self,
        project_id: str,
        task_id: str,
        user: CurrentUser = Depends(IsAuthenticated),
    ):
        task = await Task.find(task_id)
        if not task or task.project_id != project_id:
            return error_response("NOT_FOUND", "Task not found", 404)
        task.done = True
        await task.save()
        await RecalculateProjectStats.dispatch(project_id=project_id)
        return success_response(task.to_dict())


# ─────────────────────────────────────────────────────────────────────────────
# 5. App factory
# ─────────────────────────────────────────────────────────────────────────────

def create_app(db_url: str = "sqlite+aiosqlite:///./taskflow_test.db") -> Vorte:
    """Build and configure the TaskFlow Vorte application."""
    app = Vorte(auto_load=False, title="TaskFlow API", version="1.0.0")

    from vorte.modules.logging import LoggingModule

    # ── Register modules (register() accepts a list) ──
    app.register([
        LoggingModule(level="INFO"),
        DatabaseModule(url=db_url, auto_create_tables=True),
        AuthModule(
            strategy="jwt",
            secret_key="taskflow-test-secret",
            token_expiry_minutes=60,
        ),
        CacheModule(driver="memory"),
        QueueModule(
            driver="rust",
            capacity=1_000,
            concurrency=4,
            queues=["default", "emails", "analytics"],
        ),
    ])

    # ── Register controllers via VorteAPIRouter ──
    api = VorteAPIRouter()
    api.register_controller(AuthController())
    api.register_controller(HealthController())
    api.register_controller(ProjectsController())
    api.register_controller(TasksController())
    app.include_router(api)

    return app
