"""
Vorte Auth Module - Route Guards
=================================
Dependency-injectable guards for protecting routes.
Works with FastAPI's Depends() system.
"""



import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from vorte.modules.auth.jwt import JWTManager
from vorte.modules.auth.api_keys import APIKeyManager
from vorte.modules.auth.rbac import RBACManager


# Security schemes
_bearer = HTTPBearer(auto_error=False)
_api_key_scheme = Security(HTTPBearer(auto_error=False))


@dataclass
class CurrentUser:
    """Represents the currently authenticated user."""
    id: str
    email: str
    name: str
    role: str = "user"
    permissions: List[str] = None
    tier: str = "free"
    api_key_id: Optional[str] = None
    is_authenticated: bool = True

    def __post_init__(self):
        if self.permissions is None:
            self.permissions = []

    def has_role(self, role: str) -> bool:
        return self.role == role

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions or self.role == "admin"

    def has_any_permission(self, permissions: List[str]) -> bool:
        return any(self.has_permission(p) for p in permissions)

    def has_tier(self, tier: str) -> bool:
        tier_hierarchy = {"free": 0, "basic": 1, "pro": 2, "enterprise": 3}
        return tier_hierarchy.get(self.tier, 0) >= tier_hierarchy.get(tier, 0)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


async def resolve_user(request: Request) -> Optional[CurrentUser]:
    """Resolve the current user from the request (JWT or API Key)."""
    # Try JWT Bearer token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        # Check if it's an API key format
        if token.startswith("vsk_"):
            return await _resolve_api_key(token, request)
        # Otherwise treat as JWT
        try:
            jwt_manager: Optional[JWTManager] = getattr(request.app, '_vorte_jwt', None)
            if jwt_manager:
                payload = await jwt_manager.verify_token(token)
                return CurrentUser(
                    id=payload.get("sub", ""),
                    email=payload.get("email", ""),
                    name=payload.get("name", ""),
                    role=payload.get("role") or (payload.get("roles") or ["user"])[0],
                    permissions=payload.get("permissions", []),
                    tier=payload.get("tier", "free"),
                )
        except Exception:
            pass

    # Try API key from X-API-Key header
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        return await _resolve_api_key(api_key, request)

    return None


async def _resolve_api_key(key: str, request: Request) -> Optional[CurrentUser]:
    """Resolve user from API key."""
    try:
        api_key_manager: Optional[APIKeyManager] = getattr(request.app, '_vorte_api_keys', None)
        if api_key_manager:
            key_data = await api_key_manager.validate(key)
            if key_data:
                return CurrentUser(
                    id=key_data.get("user_id", ""),
                    email=key_data.get("email", ""),
                    name=key_data.get("name", ""),
                    role=key_data.get("role", "user"),
                    permissions=key_data.get("permissions", []),
                    tier=key_data.get("tier", "free"),
                    api_key_id=key_data.get("key_id"),
                )
    except Exception:
        pass
    return None


class _IsAuthenticated:
    """Guard: Requires authentication (JWT or API Key)."""

    async def __call__(self, request: Request) -> CurrentUser:
        user = await resolve_user(request)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "UNAUTHORIZED", "message": "Authentication required"},
            )
        return user


class _HasRole:
    """Guard: Requires a specific role."""

    def __init__(self, role: str):
        self.role = role

    async def __call__(self, request: Request) -> CurrentUser:
        user = await resolve_user(request)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "UNAUTHORIZED", "message": "Authentication required"},
            )
        if not user.has_role(self.role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN", "message": f"Role '{self.role}' required"},
            )
        return user


class _HasPermission:
    """Guard: Requires a specific permission."""

    def __init__(self, permission: str):
        self.permission = permission

    async def __call__(self, request: Request) -> CurrentUser:
        user = await resolve_user(request)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "UNAUTHORIZED", "message": "Authentication required"},
            )
        if not user.has_permission(self.permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN", "message": f"Permission '{self.permission}' required"},
            )
        return user


class _HasApiKey:
    """Guard: Requires valid API key."""

    async def __call__(self, request: Request) -> CurrentUser:
        api_key = request.headers.get("X-API-Key", "")
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer vsk_"):
            api_key = auth_header[7:]

        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "API_KEY_REQUIRED", "message": "Valid API key required"},
            )

        user = await _resolve_api_key(api_key, request)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_API_KEY", "message": "Invalid or expired API key"},
            )
        return user


class _WithinTier:
    """Guard: Checks user subscription tier."""

    def __init__(self, min_tier: str):
        self.min_tier = min_tier

    async def __call__(self, request: Request) -> CurrentUser:
        user = await resolve_user(request)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "UNAUTHORIZED", "message": "Authentication required"},
            )
        if not user.has_tier(self.min_tier):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "TIER_REQUIRED",
                    "message": f"Minimum tier '{self.min_tier}' required. Current tier: '{user.tier}'",
                },
            )
        return user


class _IsAdmin:
    """Guard: Requires admin role (shorthand for HasRole('admin'))."""

    async def __call__(self, request: Request) -> CurrentUser:
        return await _HasRole("admin").__call__(request)


class _ValidWebhookSignature:
    """Guard: Validates incoming webhook signatures."""

    def __init__(self, provider: str):
        self.provider = provider.lower()

    async def __call__(self, request: Request) -> Dict[str, Any]:
        # Get the appropriate secret from app state
        secrets = getattr(request.app, '_vorte_webhook_secrets', {})
        secret = secrets.get(self.provider, "")
        if not secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "WEBHOOK_NOT_CONFIGURED", "message": f"Webhook for {self.provider} not configured"},
            )

        # Get signature from headers
        signature = request.headers.get("X-Webhook-Signature", "") or \
                    request.headers.get("X-Hub-Signature-256", "")
        if not signature:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "SIGNATURE_MISSING", "message": "Webhook signature missing"},
            )

        # Read body
        body = await request.body()

        # Verify signature
        expected = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(f"sha256={expected}", signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_SIGNATURE", "message": "Invalid webhook signature"},
            )

        import json
        return json.loads(body)


class _ValidSignature:
    """Guard: Generic signature verification using SHA256."""

    def __init__(self, algorithm: str = "sha256"):
        self.algorithm = algorithm

    async def __call__(self, request: Request) -> Dict[str, Any]:
        signature = request.headers.get("X-Signature", "")
        if not signature:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "SIGNATURE_MISSING", "message": "Request signature missing"},
            )

        body = await request.body()
        secret = getattr(request.app, '_vorte_signing_secret', "")
        if not secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "SIGNING_NOT_CONFIGURED", "message": "Signing secret not configured"},
            )

        expected = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_SIGNATURE", "message": "Invalid request signature"},
            )

        import json
        return json.loads(body)


# Instantiate guards as callables for use with Depends()
IsAuthenticated = _IsAuthenticated()


def HasRole(role: str) -> _HasRole:
    """Create a role-checking guard. Usage: Depends(HasRole('admin'))"""
    return _HasRole(role)


def HasPermission(permission: str) -> _HasPermission:
    """Create a permission-checking guard. Usage: Depends(HasPermission('posts.create'))"""
    return _HasPermission(permission)


HasApiKey = _HasApiKey()


def WithinTier(min_tier: str) -> _WithinTier:
    """Create a tier-checking guard. Usage: Depends(WithinTier('pro'))"""
    return _WithinTier(min_tier)


IsAdmin = _IsAdmin()


def ValidWebhookSignature(provider: str) -> _ValidWebhookSignature:
    """Create a webhook signature guard. Usage: Depends(ValidWebhookSignature('stripe'))"""
    return _ValidWebhookSignature(provider)


def ValidSignature(algorithm: str = "sha256") -> _ValidSignature:
    """Create a signature guard. Usage: Depends(ValidSignature('sha256'))"""
    return _ValidSignature(algorithm)
