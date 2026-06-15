"""
Vorte Controller Module
========================
Provides base class-based Controller and route decorators for clean modular endpoints.
"""

from typing import Any, Callable, Dict, List, Optional, Type, Union

class route:
    """Decorators for class-based controller routing."""
    
    @staticmethod
    def _add_route(method: str, path: str, **kwargs: Any) -> Callable:
        def decorator(func: Callable) -> Callable:
            if not hasattr(func, "_vorte_routes"):
                func._vorte_routes = []
            func._vorte_routes.append((method, path, kwargs))
            return func
        return decorator

    @classmethod
    def get(cls, path: str, **kwargs: Any) -> Callable:
        return cls._add_route("GET", path, **kwargs)

    @classmethod
    def post(cls, path: str, **kwargs: Any) -> Callable:
        return cls._add_route("POST", path, **kwargs)

    @classmethod
    def put(cls, path: str, **kwargs: Any) -> Callable:
        return cls._add_route("PUT", path, **kwargs)

    @classmethod
    def delete(cls, path: str, **kwargs: Any) -> Callable:
        return cls._add_route("DELETE", path, **kwargs)

    @classmethod
    def patch(cls, path: str, **kwargs: Any) -> Callable:
        return cls._add_route("PATCH", path, **kwargs)

    @classmethod
    def websocket(cls, path: str, **kwargs: Any) -> Callable:
        return cls._add_route("WEBSOCKET", path, **kwargs)


class Controller:
    """
    Base class for all class-based controllers in Vorte.
    
    Allows grouping related route endpoints into classes.
    
    Example::
    
        from vorte.core.controller import Controller, route
        
        class UserController(Controller):
            _vorte_prefix = "/users"
            _vorte_tags = ["Users"]
            
            @route.get("")
            async def list_users(self):
                return [{"id": 1, "name": "Alice"}]
    """
    _vorte_prefix: str = ""
    _vorte_tags: Optional[List[str]] = None
