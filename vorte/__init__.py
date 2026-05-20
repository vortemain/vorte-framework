"""
Vorte Framework - The AI-First Python API Framework
=====================================================
Fast, intelligent, and modular. Built on FastAPI with batteries-included architecture,
multi-provider AI integration, and production-ready features.

Version: 1.0.0
License: MIT

Quick Start:
    from vorte import Vorte

    # Create app with all 21 modules auto-loaded
    app = Vorte(auto_load=True)

    # Or cherry-pick modules
    app = Vorte()
    app.register(AuthModule(), AIModule(), CacheModule())

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
"""

__version__ = "1.0.0"
__author__ = "Vorte Framework"
__license__ = "MIT"

from vorte.core.app import Vorte
from vorte.core.module import Module, ModuleRegistry, ModuleMeta, ModuleState, ModulePriority
from vorte.core.config import Settings, settings
from vorte.core.response import VorteResponse, success_response, error_response, VorteSSEResponse
from vorte.core.router import router
from vorte.core.di import Container, Depends, inject, wire
from vorte.core.serializer import FastSerializer, lazy_schema
from vorte.core.executor import VorteExecutor, safe_route
from vorte.core.typemirror import TypeMirror
from vorte.core.sandbox import WasmSandbox, sandboxed
from vorte.engine import VorteEngine
from vorte.modules.database.planner import N1Detector, select_related, QueryPlanner

# All 21 built-in modules — directly importable
from vorte.modules.auth import AuthModule
from vorte.modules.database import DatabaseModule
from vorte.modules.ai import AIModule
from vorte.modules.agents import AgentsModule
from vorte.modules.cache import CacheModule
from vorte.modules.queue import QueueModule
from vorte.modules.search import SearchModule
from vorte.modules.storage import StorageModule
from vorte.modules.mailer import MailerModule
from vorte.modules.notifications import NotificationsModule
from vorte.modules.mpesa import MpesaModule
from vorte.modules.payments import PaymentsModule
from vorte.modules.tenancy import MultiTenancyModule
from vorte.modules.i18n import I18nModule
from vorte.modules.security import SecurityModule
from vorte.modules.webhooks import WebhooksModule
from vorte.modules.features import FeaturesModule
from vorte.modules.graphql import GraphQLModule
from vorte.modules.logging import LoggingModule
from vorte.modules.sockets import SocketModule
from vorte.modules.dashboard import DashboardModule

__all__ = [
    # Core
    "Vorte",
    "Module",
    "ModuleRegistry",
    "ModuleMeta",
    "ModuleState",
    "ModulePriority",
    "Settings",
    "settings",
    "VorteResponse",
    "success_response",
    "error_response",
    "VorteSSEResponse",
    "router",
    "Container",
    "Depends",
    "inject",
    "wire",
    "VorteEngine",
    # Serialization
    "FastSerializer",
    "lazy_schema",
    # Concurrency
    "VorteExecutor",
    "safe_route",
    # Type Mirror
    "TypeMirror",
    # Sandbox
    "WasmSandbox",
    "sandboxed",
    # Query Planner
    "N1Detector",
    "select_related",
    "QueryPlanner",
    # Built-in Modules
    "AuthModule",
    "DatabaseModule",
    "AIModule",
    "AgentsModule",
    "CacheModule",
    "QueueModule",
    "SearchModule",
    "StorageModule",
    "MailerModule",
    "NotificationsModule",
    "MpesaModule",
    "PaymentsModule",
    "MultiTenancyModule",
    "I18nModule",
    "SecurityModule",
    "WebhooksModule",
    "FeaturesModule",
    "GraphQLModule",
    "LoggingModule",
    "SocketModule",
    "DashboardModule",
]
