"""
Vorte Storage Module — Module Registration
==========================================
Registers the StorageModule with the Vorte application.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from vorte.core.module import Module, ModuleMeta, ModulePriority

if TYPE_CHECKING:
    from vorte.core.app import Vorte

logger = logging.getLogger("vorte.modules.storage")


class StorageModule(Module):
    """
    Unified file storage module with pluggable drivers.

    Supported drivers: ``local``, ``s3``, ``gcs``, ``cloudinary``.

    Configuration:
        - driver: storage driver name
        - bucket: S3/GCS bucket name
        - access_key / secret_key: cloud credentials
        - region: cloud region
        - cdn_url: CDN base URL
        - local_path: local filesystem path
        - base_url: base URL for local driver
    """

    meta = ModuleMeta(
        name="storage",
        version="1.0.0",
        description="Unified file storage with local, S3, GCS, and Cloudinary drivers",
        priority=ModulePriority.DEFAULT,
        dependencies=[],
    )

    def __init__(self, **config: Any) -> None:
        super().__init__(**config)
        self._manager: Any = None

    def register(self, app: "Vorte") -> None:
        from vorte.modules.storage.storage import StorageManager

        self._manager = StorageManager(
            driver=self.get_config("driver", "local"),
            bucket=self.get_config("bucket", ""),
            access_key=self.get_config("access_key", ""),
            secret_key=self.get_config("secret_key", ""),
            region=self.get_config("region", "us-east-1"),
            endpoint_url=self.get_config("endpoint_url", ""),
            cdn_url=self.get_config("cdn_url", ""),
            local_path=self.get_config("local_path", "storage/uploads"),
            base_url=self.get_config("base_url", "/files"),
        )
        app.container.register_instance(StorageManager, self._manager)
        self._register_routes(app)
        logger.debug("Storage module registered (driver=%s)", self._manager.driver_name)

    async def on_startup(self) -> None:
        try:
            await self._manager.ping()
            logger.debug("Storage backend is reachable")
        except Exception as exc:
            logger.warning("Storage backend not reachable: %s", exc)

    async def on_shutdown(self) -> None:
        pass

    async def health_check(self) -> Dict[str, Any]:
        try:
            ok = await self._manager.ping()
            return {"module": self.meta.name, "status": "healthy" if ok else "degraded",
                    "driver": self._manager.driver_name}
        except Exception as exc:
            return {"module": self.meta.name, "status": "unhealthy", "error": str(exc)}

    def _register_routes(self, app: "Vorte") -> None:

        @app.post("/api/storage/upload", tags=["Storage"])
        async def upload_file(path: str, content: bytes) -> Dict[str, Any]:
            return await self._manager.put(path=path, content=content)

        @app.get("/api/storage/{path:path}", tags=["Storage"])
        async def get_file(path: str):
            from fastapi.responses import Response
            data = await self._manager.get(path)
            return Response(content=data)

        @app.delete("/api/storage/{path:path}", tags=["Storage"])
        async def delete_file(path: str) -> Dict[str, str]:
            await self._manager.delete(path)
            return {"status": "deleted", "path": path}

        @app.get("/api/storage/{path:path}/url", tags=["Storage"])
        async def get_file_url(path: str) -> Dict[str, str]:
            return {"url": await self._manager.url(path)}

    def get_manager(self) -> Any:
        return self._manager
