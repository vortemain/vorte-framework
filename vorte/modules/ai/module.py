"""
Vorte AI Module
=================
The flagship AI Integration module – provider-agnostic interface to
100+ AI models with streaming, embeddings, structured output, and cost
tracking.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from vorte.core.module import Module, ModuleMeta, ModulePriority
from vorte.modules.ai.client import AIClient
from vorte.modules.ai.cost_tracker import CostTracker
from vorte.modules.ai.embeddings import EmbeddingEngine
from vorte.modules.ai.providers.registry import ProviderRegistry
from vorte.modules.ai.schemas import AIConfig, ProviderConfig

logger = logging.getLogger("vorte.modules.ai")


class AIModule(Module):
    """
    Vorte AI Integration Module.

    Provides a unified, provider-agnostic interface to multiple AI providers
    (OpenAI, Anthropic, Gemini, Mistral, and any OpenAI-compatible endpoint).

    Configuration (passed to constructor or via app config)::

        app = Vorte()
        app.use(AIModule, config={
            "default_model": "gpt-4o",
            "default_provider": "openai",
            "providers": {
                "openai": {"api_key": "sk-..."},
                "anthropic": {"api_key": "sk-ant-..."},
            },
        })

    Access via DI or ``app.ai``::

        @app.get("/")
        async def root(ai: AIClient = Depends("ai")):
            return await ai.complete("Hello!")
    """

    meta = ModuleMeta(
        name="ai",
        version="1.0.0",
        description=(
            "AI Integration module – unified interface to OpenAI, Anthropic, "
            "Gemini, Mistral and 100+ models with streaming, embeddings, "
            "structured output, and cost tracking."
        ),
        priority=ModulePriority.AI,
    )

    def __init__(self, **config: Any) -> None:
        super().__init__(**config)
        self._ai_config = self._build_config(config)
        self._registry: Optional[ProviderRegistry] = None
        self._client: Optional[AIClient] = None
        self._cost_tracker: Optional[CostTracker] = None
        self._embedding_engine: Optional[EmbeddingEngine] = None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _build_config(self, raw: Dict[str, Any]) -> AIConfig:
        providers = raw.pop("providers", {})

        # Build ProviderConfig objects
        provider_configs: Dict[str, ProviderConfig] = {}
        for name, pconf in providers.items():
            if isinstance(pconf, dict):
                provider_configs[name] = ProviderConfig(**pconf)

        return AIConfig(
            default_model=raw.get("default_model", "gpt-4o"),
            default_provider=raw.get("default_provider", "openai"),
            fallback_providers=raw.get("fallback_providers", ["anthropic", "gemini"]),
            cache_responses=raw.get("cache_responses", False),
            track_costs=raw.get("track_costs", True),
            max_tokens=raw.get("max_tokens", 4096),
            temperature=raw.get("temperature", 0.7),
            top_p=raw.get("top_p"),
            timeout=raw.get("timeout", 120.0),
            providers=provider_configs,
            retry_on_failure=raw.get("retry_on_failure", True),
            max_retries=raw.get("max_retries", 3),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def register(self, app: Any) -> None:
        """Register the AI module with the Vorte application."""
        self.app = app

        # Create components
        self._registry = ProviderRegistry()
        self._cost_tracker = CostTracker(
            budget_limit=self._ai_config.providers.get("budget", {}).get("limit"),
        )

        # Register providers
        self._register_providers()

        # Set fallback chain
        if self._ai_config.fallback_providers:
            self._registry.set_fallback_chain(self._ai_config.fallback_providers)

        # Create embedding engine
        self._embedding_engine = EmbeddingEngine(
            registry=self._registry,
            default_provider=self._ai_config.default_provider,
            default_model="text-embedding-3-small",
        )

        # Create the universal client
        self._client = AIClient(
            registry=self._registry,
            config=self._ai_config,
            cost_tracker=self._cost_tracker,
            embedding_engine=self._embedding_engine,
        )

        # Register in DI container so it's injectable
        if hasattr(app, "container") and app.container is not None:
            app.container.register("ai", self._client)
            app.container.register(AIClient, self._client)

        # Store on app for easy access
        if hasattr(app, "ai"):
            logger.warning("app.ai already set – overwriting with AI module client.")
        app.ai = self._client  # type: ignore[attr-defined]

        logger.debug(
            "AI module registered: default=%s/%s, providers=%s",
            self._ai_config.default_provider,
            self._ai_config.default_model,
            list(self._registry.provider_names),
        )

    def _register_providers(self) -> None:
        """Instantiate and register all configured providers."""
        # Always register the built-in providers if they have API keys
        for name, pconfig in self._ai_config.providers.items():
            try:
                provider_class = ProviderRegistry.get_provider_class(name)
                provider = provider_class(pconfig)
                self._registry.register(provider)
                logger.info("Registered AI provider: %s (model: %s)",
                            name, pconfig.default_model or provider.default_model)
            except ValueError:
                logger.warning("Unknown provider '%s' – skipping.", name)
            except Exception as exc:
                logger.error("Failed to register provider '%s': %s", name, exc)

    async def on_startup(self) -> None:
        """Validate provider connections on startup."""
        if not self._registry:
            return
        for name, provider in self._registry.get_all().items():
            try:
                healthy = await provider.health_check()
                if healthy.get("status") != "ok":
                    logger.warning("Provider '%s' health check: %s", name, healthy)
            except Exception as exc:
                logger.warning("Provider '%s' health check failed: %s", name, exc)
        logger.info("AI module startup complete. %d provider(s) ready.",
                     len(self._registry.provider_names))

    async def on_shutdown(self) -> None:
        """Clean up on shutdown."""
        if self._embedding_engine:
            self._embedding_engine.clear_cache()
        if self._cost_tracker:
            report = self._cost_tracker.report(period="all")
            if report:
                logger.info(
                    "AI module shutdown. Total cost: $%.4f across %d request(s).",
                    self._cost_tracker.total_cost,
                    self._cost_tracker.total_requests,
                )

    async def health_check(self) -> Dict[str, Any]:
        """Health check for the AI module and all providers."""
        base = await super().health_check()
        if self._client:
            base["providers"] = await self._client.health_check()
        return base

    # ------------------------------------------------------------------
    # Public API (delegates to AIClient)
    # ------------------------------------------------------------------

    @property
    def client(self) -> AIClient:
        """Access the underlying universal AI client."""
        if self._client is None:
            raise RuntimeError("AI module not yet registered. Call register() first.")
        return self._client

    @property
    def registry(self) -> ProviderRegistry:
        if self._registry is None:
            raise RuntimeError("AI module not yet registered.")
        return self._registry

    @property
    def cost_tracker(self) -> CostTracker:
        if self._cost_tracker is None:
            raise RuntimeError("AI module not yet registered.")
        return self._cost_tracker

    @property
    def config(self) -> AIConfig:
        return self._ai_config

    # Convenience methods (proxy to client)
    async def complete(self, prompt: str, **kwargs) -> Any:
        """Proxy to ``AIClient.complete``."""
        return await self.client.complete(prompt, **kwargs)

    async def chat(self, messages: list, **kwargs) -> Any:
        """Proxy to ``AIClient.chat``."""
        return await self.client.chat(messages, **kwargs)

    def stream(self, prompt: str, **kwargs):
        """Proxy to ``AIClient.stream``."""
        return self.client.stream(prompt, **kwargs)

    async def embed(self, text: str, **kwargs):
        """Proxy to ``AIClient.embed``."""
        return await self.client.embed(text, **kwargs)

    async def embed_many(self, texts: list, **kwargs):
        """Proxy to ``AIClient.embed_many``."""
        return await self.client.embed_many(texts, **kwargs)
