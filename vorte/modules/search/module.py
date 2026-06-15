"""
Vorte Search Module — Module Registration
==========================================
Registers the SearchModule with the Vorte application, wires up
the search engine, and provides health-check support.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from vorte.core.module import Module, ModuleMeta, ModulePriority

if TYPE_CHECKING:
    from vorte.core.app import Vorte

logger = logging.getLogger("vorte.modules.search")


class SearchModule(Module):
    """
    Search module providing keyword, semantic, and hybrid search.

    Features:
        - Keyword search via Meilisearch or database full-text
        - Semantic / vector search via pgvector embeddings
        - Hybrid search combining keyword + semantic scores
        - Filters, facets, highlighting
        - SearchableMixin for easy model integration

    Configuration (passed via ``SearchModule(...)`` or app settings):
        - engine: ``"meilisearch"`` | ``"pgvector"`` | ``"database"``
        - meilisearch_url: URL of the Meilisearch instance
        - meilisearch_key: API key for Meilisearch
        - pgvector_connection: PostgreSQL + pgvector connection string
        - default_limit: default pagination size (default 20)
        - highlight_pre_tag: opening highlight tag
        - highlight_post_tag: closing highlight tag
    """

    meta = ModuleMeta(
        name="search",
        version="1.0.0",
        description="Full-text search with keyword, semantic, and hybrid modes",
        priority=ModulePriority.SEARCH,
        dependencies=[],
    )

    def __init__(self, **config: Any) -> None:
        super().__init__(**config)
        self._engine: Optional[Any] = None
        self._backend: Optional[Any] = None
        self._indexes: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def register(self, app: "Vorte") -> None:
        """Register search routes and initialise the backend."""
        from vorte.modules.search.search import HybridSearchEngine

        engine_type = self.get_config("engine") or "database"
        self._engine = HybridSearchEngine(
            backend=engine_type,
            meilisearch_url=self.get_config("meilisearch_url", ""),
            meilisearch_key=self.get_config("meilisearch_key", ""),
            pgvector_connection=self.get_config("pgvector_connection", ""),
        )
        self._backend = self._engine.backend

        # Register the engine in the DI container
        app.container.register_instance(HybridSearchEngine, self._engine)

        # Mount search API routes
        self._register_routes(app)
        logger.debug("Search module registered (engine=%s)", engine_type)

    async def on_startup(self) -> None:
        """Verify connectivity to the search backend."""
        try:
            if self._backend:
                await self._backend.ping()
            logger.debug("Search backend is reachable")
        except Exception as exc:
            logger.warning("Search backend not reachable: %s", exc)

    async def on_shutdown(self) -> None:
        """Close backend connections."""
        if self._backend:
            try:
                await self._backend.close()
            except Exception as exc:
                logger.warning("Error closing search backend: %s", exc)

    async def health_check(self) -> Dict[str, Any]:
        """Return health status of the search module."""
        try:
            if self._backend:
                await self._backend.ping()
                return {"module": self.meta.name, "status": "healthy"}
            return {"module": self.meta.name, "status": "degraded", "error": "No backend configured"}
        except Exception as exc:
            return {"module": self.meta.name, "status": "unhealthy", "error": str(exc)}

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def _register_routes(self, app: "Vorte") -> None:
        """Mount the search REST API."""

        @app.post("/api/search", tags=["Search"])
        async def search(
            query: str,
            index: Optional[str] = None,
            filters: Optional[Dict[str, Any]] = None,
            facets: Optional[List[str]] = None,
            limit: int = 20,
            offset: int = 0,
            highlight: bool = True,
        ) -> Dict[str, Any]:
            """Perform a search query."""
            result = await self._engine.search(
                query=query,
                index=index,
                filters=filters,
                facet_fields=facets,
                limit=limit,
                offset=offset,
                highlight=highlight,
            )
            return result.to_dict()

        @app.get("/api/search/indexes", tags=["Search"])
        async def list_indexes() -> List[str]:
            """List all search indexes."""
            if self._backend:
                return await self._backend.list_indexes()
            return []

        @app.post("/api/search/index/{index_name}", tags=["Search"])
        async def create_index(index_name: str, body: Dict[str, Any]) -> Dict[str, Any]:
            """Create a new search index."""
            if self._backend:
                return await self._backend.create_index(index_name, **body)
            return {"error": "No backend configured"}

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_engine(self) -> Any:
        """Return the hybrid search engine instance."""
        return self._engine
