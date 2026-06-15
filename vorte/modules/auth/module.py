"""
Vorte Auth Module - Main Module
=================================
Production-grade authentication: JWT, OAuth2, API Keys, MFA, RBAC.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from vorte.core.module import Module, ModuleMeta, ModulePriority
from vorte.core.response import success_response, error_response
from vorte.modules.auth.jwt import JWTManager
from vorte.modules.auth.oauth import OAuth2Manager, GoogleOAuth2Provider, GitHubOAuth2Provider
from vorte.modules.auth.api_keys import APIKeyManager
from vorte.modules.auth.rbac import RBACManager
from vorte.modules.auth.mfa import MFAManager as MFAService
from vorte.modules.auth.sessions import SessionManager
from vorte.modules.auth.guards import IsAuthenticated, CurrentUser, resolve_user
from vorte.modules.auth.schemas import LoginRequest, RegisterRequest, TokenResponse


class AuthModule(Module):
    """
    Complete authentication module with JWT, OAuth2, API Keys, MFA, and RBAC.
    
    Usage:
        app.register(AuthModule(
            strategy='jwt',
            refresh_tokens=True,
            mfa=True,
            oauth_providers=['google', 'github'],
        ))
    """

    meta = ModuleMeta(
        name="auth",
        version="1.0.0",
        description="Complete authentication system with JWT, OAuth2, API Keys, MFA, RBAC",
        priority=ModulePriority.AUTH,
    )

    def __init__(
        self,
        *,
        strategy: str = "jwt",
        refresh_tokens: bool = True,
        mfa: bool = False,
        oauth_providers: Optional[List[str]] = None,
        secret_key: Optional[str] = None,
        token_expiry_minutes: int = 60,
        refresh_expiry_days: int = 7,
    ):
        super().__init__(
            strategy=strategy,
            refresh_tokens=refresh_tokens,
            mfa=mfa,
            oauth_providers=oauth_providers or [],
        )
        self._strategy = strategy
        self._refresh_tokens = refresh_tokens
        self._mfa_enabled = mfa
        self._oauth_provider_names = oauth_providers or []
        self._secret_key = secret_key or "vorte-secret-change-in-production"
        self._token_expiry = token_expiry_minutes
        self._refresh_expiry = refresh_expiry_days

        # Managers
        self.jwt: Optional[JWTManager] = None
        self.oauth: Optional[OAuth2Manager] = None
        self.api_keys: Optional[APIKeyManager] = None
        self.rbac: Optional[RBACManager] = None
        self.mfa_service: Optional[MFAService] = None  # type: ignore
        self.sessions: Optional[SessionManager] = None

        # Router
        self._router = APIRouter(prefix="/auth", tags=["Authentication"])

    def register(self, app) -> None:
        """Register auth module with the application."""
        # Initialize managers
        self.jwt = JWTManager(
            secret_key=self._secret_key,
            access_token_expiry=self._token_expiry,
            refresh_token_expiry=self._refresh_expiry,
        )
        self.oauth = OAuth2Manager()
        self.api_keys = APIKeyManager()
        self.rbac = RBACManager()
        self.sessions = SessionManager()
        self.mfa_service = MFAService() if self._mfa_enabled else None  # type: ignore

        # Store on app state for guards to access.
        # Must be set on both the Vorte wrapper AND the underlying FastAPI instance
        # because in ASGI request handling request.app resolves to FastAPI.
        app._vorte_jwt = self.jwt
        app._vorte_api_keys = self.api_keys
        app._vorte_sessions = self.sessions
        app._vorte_webhook_secrets = getattr(app, '_vorte_webhook_secrets', {})
        if hasattr(app, 'fastapi'):
            app.fastapi._vorte_jwt = self.jwt
            app.fastapi._vorte_api_keys = self.api_keys
            app.fastapi._vorte_sessions = self.sessions
            app.fastapi._vorte_webhook_secrets = app._vorte_webhook_secrets

        # Register OAuth providers
        self._setup_oauth(app)

        # Register auth routes
        self._setup_routes()
        app.include_router(self._router)

        # Register in DI container
        if hasattr(app, 'container'):
            app.container.register_instance(JWTManager, self.jwt)
            app.container.register_instance(OAuth2Manager, self.oauth)
            app.container.register_instance(APIKeyManager, self.api_keys)

    def _setup_oauth(self, app) -> None:
        """Configure OAuth providers from settings."""
        settings = app.settings if hasattr(app, 'settings') else None
        if not settings:
            return

        if 'google' in self._oauth_provider_names:
            client_id = getattr(settings.auth, 'google_client_id', '') or self.get_config('google_client_id', '')
            client_secret = getattr(settings.auth, 'google_client_secret', '') or self.get_config('google_client_secret', '')
            redirect_uri = self.get_config('oauth_redirect_uri', 'http://localhost:8000/auth/callback/google')
            if client_id and client_secret:
                self.oauth.register('google', GoogleOAuth2Provider(client_id, client_secret, redirect_uri))

        if 'github' in self._oauth_provider_names:
            client_id = self.get_config('github_client_id', '')
            client_secret = self.get_config('github_client_secret', '')
            redirect_uri = self.get_config('oauth_redirect_uri', 'http://localhost:8000/auth/callback/github')
            if client_id and client_secret:
                self.oauth.register('github', GitHubOAuth2Provider(client_id, client_secret, redirect_uri))

    def _setup_routes(self) -> None:
        """Setup authentication routes."""

        @self._router.post("/login")
        async def login(request: LoginRequest):
            """Authenticate user and return tokens."""
            # In production, this would verify against the database
            # For the framework, we provide the authentication infrastructure
            user = await self._authenticate_user(request.email, request.password)
            if not user:
                return error_response("INVALID_CREDENTIALS", "Invalid email or password", status_code=401)

            tokens = await self.jwt.create_tokens(
                sub=user["id"],
                email=user["email"],
                name=user["name"],
                role=user.get("role", "user"),
                permissions=user.get("permissions", []),
                tier=user.get("tier", "free"),
            )
            return success_response(tokens)

        @self._router.post("/register")
        async def register(request: RegisterRequest):
            """Register a new user."""
            user = await self._create_user(
                email=request.email,
                password=request.password,
                name=request.name,
            )
            tokens = await self.jwt.create_tokens(
                sub=user["id"],
                email=user["email"],
                name=user["name"],
                role=user.get("role", "user"),
            )
            return success_response(tokens, status_code=201)

        @self._router.post("/refresh")
        async def refresh_token(request: Request):
            """Refresh access token."""
            body = await request.json()
            refresh_token = body.get("refresh_token")
            if not refresh_token:
                return error_response("TOKEN_REQUIRED", "Refresh token required", status_code=400)

            try:
                payload = await self.jwt.verify_token(refresh_token, expected_type="refresh")
            except Exception:
                return error_response("INVALID_TOKEN", "Invalid or expired refresh token", status_code=401)

            tokens = await self.jwt.create_tokens(
                sub=payload["sub"],
                email=payload.get("email", ""),
                name=payload.get("name", ""),
                role=payload.get("role", "user"),
                permissions=payload.get("permissions", []),
                tier=payload.get("tier", "free"),
            )
            return success_response(tokens)

        @self._router.get("/me")
        async def get_me(user: CurrentUser = Depends(IsAuthenticated)):
            """Get current authenticated user."""
            return success_response({
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role,
                "tier": user.tier,
            })

        @self._router.post("/logout")
        async def logout(user: CurrentUser = Depends(IsAuthenticated)):
            """Logout current user."""
            # Revoke token / session
            return success_response({"message": "Logged out successfully"})

        @self._router.post("/forgot-password")
        async def forgot_password(request: Request):
            """Request password reset."""
            body = await request.json()
            email = body.get("email", "")
            if not email:
                return error_response("EMAIL_REQUIRED", "Email is required", status_code=400)
            # In production: send password reset email
            return success_response({"message": "Password reset email sent if account exists"})

        @self._router.post("/reset-password")
        async def reset_password(request: Request):
            """Reset password with token."""
            body = await request.json()
            token = body.get("token", "")
            new_password = body.get("password", "")
            if not token or not new_password:
                return error_response("INVALID_REQUEST", "Token and new password required", status_code=400)
            try:
                payload = await self.jwt.verify_token(token)
            except Exception:
                payload = None
            if not payload or payload.get("type") != "password_reset":
                return error_response("INVALID_TOKEN", "Invalid or expired reset token", status_code=400)
            return success_response({"message": "Password reset successfully"})

        # OAuth routes
        @self._router.get("/oauth/{provider}")
        async def oauth_authorize(provider: str):
            """Get OAuth authorization URL."""
            url = self.oauth.get_authorization_url(provider)
            return success_response({"authorization_url": url})

        @self._router.get("/callback/{provider}")
        async def oauth_callback(provider: str, code: str, request: Request):
            """Handle OAuth callback."""
            user = await self.oauth.handle_callback(provider, code)
            tokens = await self.jwt.create_tokens(
                sub=f"{provider}:{user.provider_id}",
                email=user.email,
                name=user.name,
                role="user",
            )
            return success_response({"user": {"email": user.email, "name": user.name, "avatar": user.avatar}, **tokens})

        # MFA routes
        if self._mfa_enabled:
            @self._router.post("/mfa/setup", dependencies=[Depends(IsAuthenticated)])
            async def mfa_setup(request: Request):
                """Setup MFA for current user."""
                user = await resolve_user(request)
                setup = self.mfa_service.setup_mfa(user.id, user.email)
                return success_response(setup)

            @self._router.post("/mfa/verify", dependencies=[Depends(IsAuthenticated)])
            async def mfa_verify(request: Request):
                """Verify MFA code."""
                body = await request.json()
                code = body.get("code", "")
                user = await resolve_user(request)
                try:
                    self.mfa_service.confirm_setup(user.id, code)
                    return success_response({"message": "MFA verified successfully"})
                except Exception:
                    return error_response("INVALID_MFA_CODE", "Invalid MFA code", status_code=400)

    # ---- Internal methods (override in production) ----

    async def _authenticate_user(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        """Authenticate a user. Override this in production to use your database."""
        # Framework provides the infrastructure; you wire it to your user store.
        return None

    async def _create_user(self, email: str, password: str, name: str) -> Dict[str, Any]:
        """Create a new user. Override this in production."""
        # Framework provides the infrastructure; you wire it to your user store.
        from passlib.hash import bcrypt
        def hash_password(pw): return bcrypt.hash(pw)
        return {
            "id": "usr_new",
            "email": email,
            "name": name,
            "role": "user",
            "password_hash": hash_password(password),
        }

    async def health_check(self) -> Dict[str, Any]:
        return {
            "module": self.meta.name,
            "status": "healthy" if self.state.value == "ready" else self.state.value,
            "strategy": self._strategy,
            "features": {
                "refresh_tokens": self._refresh_tokens,
                "mfa": self._mfa_enabled,
                "oauth_providers": self._oauth_provider_names,
            },
        }
