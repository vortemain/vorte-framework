"""
Vorte Router Module
====================
Provides routing utilities, versioning middleware, and route registration helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from enum import Enum

from fastapi import APIRouter, Depends, Request, Response
from fastapi.routing import APIRoute

from vorte.modules.database.planner import active_relations


def infer_relations(response_model: Any) -> Tuple[str, ...]:
    """Infer database relations recursively from a Pydantic response model's fields."""
    import typing
    from typing import Union
    try:
        from types import UnionType
    except ImportError:
        UnionType = None

    def _unwrap_type(t: Any) -> Any:
        origin = typing.get_origin(t)
        if origin is list or origin is typing.List:
            args = typing.get_args(t)
            if args:
                return _unwrap_type(args[0])
        elif origin is Union or (UnionType and origin is UnionType):
            args = typing.get_args(t)
            # Filter out None type
            args = [a for a in args if a is not type(None)]
            if args:
                return _unwrap_type(args[0])
        return t

    def _recurse(model: Any, parent_path: str = "") -> List[str]:
        model = _unwrap_type(model)
        if not model or not hasattr(model, "model_fields"):
            return []
        
        relations = []
        for name, field in model.model_fields.items():
            field_type = _unwrap_type(field.annotation)
            path = f"{parent_path}.{name}" if parent_path else name
            
            # If the field is a nested Pydantic model, it represents a database relationship
            if hasattr(field_type, "model_fields"):
                relations.append(path)
                relations.extend(_recurse(field_type, parent_path=path))
        return relations

    unwrapped_root = _unwrap_type(response_model)
    return tuple(_recurse(unwrapped_root))



class VorteAPIRoute(APIRoute):
    """Custom APIRoute that implements look-ahead query optimization."""
    
    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()
        
        inferred = infer_relations(self.response_model)
        manual = getattr(self.endpoint, "_vorte_relations", ())
        merged_relations = tuple(set(inferred + manual))
        
        self.endpoint._vorte_relations = merged_relations
        if merged_relations:
            self.endpoint._vorte_select_related = True

        async def custom_route_handler(request: Request) -> Response:
            token = active_relations.set(merged_relations)
            try:
                return await original_route_handler(request)
            finally:
                active_relations.reset(token)

        return custom_route_handler



class VersioningStrategy(str, Enum):
    URL = "url"
    HEADER = "header"


@dataclass
class VersionedRoute:
    """A route with versioning metadata."""
    path: str
    method: str
    endpoint: Callable
    version: str
    deprecated_in: Optional[str] = None
    removed_in: Optional[str] = None
    sunset_date: Optional[str] = None
    tags: List[str] = field(default_factory=list)


class VorteAPIRouter(APIRouter):
    """
    Extended FastAPI router with Vorte-specific features:
    - Auto-versioning
    - Deprecation headers
    - Route metadata
    """
    
    def __init__(self, prefix: str = "", tags: Optional[List[str]] = None, **kwargs):
        self._vorte_prefix = prefix
        self._vorte_tags = tags or []
        self._versioned_routes: List[VersionedRoute] = []
        kwargs.setdefault("route_class", VorteAPIRoute)
        super().__init__(prefix=prefix, tags=tags, **kwargs)
    
    def add_api_route(
        self,
        path: str,
        endpoint: Callable,
        *,
        methods: Optional[List[str]] = None,
        deprecated_in: Optional[str] = None,
        removed_in: Optional[str] = None,
        sunset_date: Optional[str] = None,
        version: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Add a route with versioning and deprecation support."""
        methods = methods or ["GET"]
        for method in methods:
            vr = VersionedRoute(
                path=path,
                method=method.upper(),
                endpoint=endpoint,
                version=version or "v1",
                deprecated_in=deprecated_in,
                removed_in=removed_in,
                sunset_date=sunset_date,
                tags=self._vorte_tags,
            )
            self._versioned_routes.append(vr)
        
        if "deprecated" in kwargs:
            del kwargs["deprecated"]
            
        super().add_api_route(
            path=path,
            endpoint=endpoint,
            methods=methods,
            deprecated=bool(deprecated_in),
            **kwargs,
        )


class VersioningMiddleware:
    """
    Middleware for API versioning.
    Supports URL path versioning and header-based versioning.
    """
    
    def __init__(self, default_version: str = "v1", strategy: VersioningStrategy = VersioningStrategy.URL):
        self.default_version = default_version
        self.strategy = strategy
        self._versions: Set[str] = {default_version}
        self._deprecated_routes: Dict[str, Dict[str, Any]] = {}
    
    def register_version(self, version: str) -> None:
        """Register a new API version."""
        self._versions.add(version)
    
    def register_deprecation(
        self,
        path: str,
        deprecated_in: str,
        removed_in: str,
        sunset_date: str,
        alternative_path: Optional[str] = None,
    ) -> None:
        """Register a deprecated route."""
        self._deprecated_routes[path] = {
            "deprecated_in": deprecated_in,
            "removed_in": removed_in,
            "sunset_date": sunset_date,
            "alternative": alternative_path,
        }
    
    def get_deprecation_headers(self, path: str) -> Optional[Dict[str, str]]:
        """Get deprecation headers for a route."""
        if path in self._deprecated_routes:
            dep = self._deprecated_routes[path]
            headers = {
                "Deprecation": "true",
                "Sunset": dep["sunset_date"],
            }
            if dep.get("alternative"):
                headers["Link"] = f'<{dep["alternative"]}>; rel="successor-version"'
            return headers
        return None
    
    def parse_version(self, request: Request) -> str:
        """Parse the API version from the request."""
        if self.strategy == VersioningStrategy.HEADER:
            return request.headers.get("API-Version", self.default_version)
        
        # URL-based: extract version from path
        parts = request.url.path.split("/")
        for part in parts:
            if part.startswith("v") and part[1:].isdigit():
                return part
        return self.default_version
    
    def get_versions(self) -> Set[str]:
        """Get all registered versions."""
        return set(self._versions)


# Convenience module-level router
router = VorteAPIRouter()
