"""
Vorte JWT Manager
=================
JWT token creation, verification, and refresh logic built on PyJWT.

Supports:
- Access tokens (short-lived)
- Refresh tokens (long-lived, optional)
- Token blacklisting
- Audience / issuer claims
- Asymmetric (RS256) and symmetric (HS256) signing

Usage:
    jwt_manager = JWTManager(secret_key="your-secret", algorithm="HS256")

    token = await jwt_manager.create_access_token(user_id="...", email="...")
    payload = await jwt_manager.verify_token(token)
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import jwt as pyjwt
from jwt import PyJWTError


class JWTManager:
    """
    Manages JWT access and refresh tokens.

    Args:
        secret_key: Symmetric secret or RSA private key for signing.
        algorithm: Signing algorithm (HS256, RS256, ES256, etc.).
        access_token_expiry: Access token lifetime in seconds (default 15 minutes).
        refresh_token_expiry: Refresh token lifetime in seconds (default 7 days).
        issuer: JWT ``iss`` claim value.
        audience: JWT ``aud`` claim value (optional).
        enable_refresh_tokens: Whether to issue refresh tokens.
    """

    def __init__(
        self,
        secret_key: str,
        algorithm: str = "HS256",
        access_token_expiry: int = 900,
        refresh_token_expiry: int = 604800,
        issuer: str = "vorte-auth",
        audience: Optional[str] = None,
        enable_refresh_tokens: bool = True,
    ):
        self._secret_key = secret_key
        self._algorithm = algorithm
        self._access_token_expiry = access_token_expiry
        self._refresh_token_expiry = refresh_token_expiry
        self._issuer = issuer
        self._audience = audience
        self._enable_refresh_tokens = enable_refresh_tokens
        self._blacklisted_jtis: Set[str] = set()

    # ------------------------------------------------------------------
    # Token Creation
    # ------------------------------------------------------------------

    async def create_access_token(
        self,
        user_id: str,
        email: str,
        roles: Optional[List[str]] = None,
        permissions: Optional[List[str]] = None,
        tier: str = "free",
        mfa_verified: bool = False,
        extra_claims: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Create a short-lived access token.

        Args:
            user_id: The subject (user ID) claim.
            email: User email claim.
            roles: List of role names.
            permissions: List of permission strings.
            tier: Subscription tier.
            mfa_verified: Whether MFA has been verified for this token.
            extra_claims: Additional claims merged into the payload.

        Returns:
            Encoded JWT string.
        """
        now = int(time.time())
        payload: Dict[str, Any] = {
            "sub": user_id,
            "email": email,
            "roles": roles or [],
            "permissions": permissions or [],
            "tier": tier,
            "mfa_verified": mfa_verified,
            "type": "access",
            "iat": now,
            "exp": now + self._access_token_expiry,
            "jti": str(uuid.uuid4()),
        }

        if self._issuer:
            payload["iss"] = self._issuer
        if self._audience:
            payload["aud"] = self._audience

        if extra_claims:
            payload.update(extra_claims)

        return pyjwt.encode(payload, self._secret_key, algorithm=self._algorithm)

    async def create_refresh_token(
        self,
        user_id: str,
        extra_claims: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Create a long-lived refresh token.

        Args:
            user_id: The subject (user ID) claim.
            extra_claims: Additional claims merged into the payload.

        Returns:
            Encoded JWT string.

        Raises:
            RuntimeError: If refresh tokens are disabled.
        """
        if not self._enable_refresh_tokens:
            raise RuntimeError("Refresh tokens are disabled in this configuration.")

        now = int(time.time())
        payload: Dict[str, Any] = {
            "sub": user_id,
            "type": "refresh",
            "iat": now,
            "exp": now + self._refresh_token_expiry,
            "jti": str(uuid.uuid4()),
        }

        if self._issuer:
            payload["iss"] = self._issuer
        if self._audience:
            payload["aud"] = self._audience

        if extra_claims:
            payload.update(extra_claims)

        return pyjwt.encode(payload, self._secret_key, algorithm=self._algorithm)

    # ------------------------------------------------------------------
    # Token Verification
    # ------------------------------------------------------------------

    async def verify_token(
        self,
        token: str,
        *,
        expected_type: Optional[str] = None,
        check_blacklist: bool = True,
    ) -> Dict[str, Any]:
        """
        Verify and decode a JWT token.

        Args:
            token: Encoded JWT string.
            expected_type: If set, verify the ``type`` claim matches.
            check_blacklist: Whether to reject blacklisted tokens.

        Returns:
            Decoded token payload dictionary.

        Raises:
            TokenExpiredError: The token has expired.
            InvalidTokenError: The token is malformed or invalid.
            TokenBlacklistedError: The token has been revoked.
        """
        try:
            payload = pyjwt.decode(
                token,
                self._secret_key,
                algorithms=[self._algorithm],
                issuer=self._issuer if self._issuer else None,
                audience=self._audience if self._audience else None,
            )
        except pyjwt.ExpiredSignatureError:
            raise TokenExpiredError("Token has expired")
        except pyjwt.InvalidIssuerError:
            raise InvalidTokenError("Invalid token issuer")
        except pyjwt.InvalidAudienceError:
            raise InvalidTokenError("Invalid token audience")
        except PyJWTError as exc:
            raise InvalidTokenError(f"Invalid token: {exc}") from exc

        # Validate type claim
        if expected_type and payload.get("type") != expected_type:
            raise InvalidTokenError(
                f"Expected token type '{expected_type}', got '{payload.get('type')}'"
            )

        # Check blacklist
        jti = payload.get("jti")
        if check_blacklist and jti and jti in self._blacklisted_jtis:
            raise TokenBlacklistedError("Token has been revoked")

        return payload

    # ------------------------------------------------------------------
    # Token Refresh
    # ------------------------------------------------------------------

    async def refresh_access_token(self, refresh_token: str) -> str:
        """
        Exchange a valid refresh token for a new access token.

        Args:
            refresh_token: The refresh token to exchange.

        Returns:
            New access token string.

        Raises:
            TokenExpiredError: The refresh token has expired.
            InvalidTokenError: The refresh token is invalid.
        """
        payload = await self.verify_token(refresh_token, expected_type="refresh")

        # Extract user info from the refresh token
        user_id = payload.get("sub")

        # Issue new access token
        # Note: Caller should enrich with full user data from DB.
        # This provides a minimal new access token.
        return await self.create_access_token(
            user_id=user_id,
            email=payload.get("email", ""),
        )

    # ------------------------------------------------------------------
    # Token Blacklisting / Revocation
    # ------------------------------------------------------------------

    async def blacklist_token(self, token: str) -> None:
        """
        Add a token's JTI to the blacklist, revoking it immediately.

        Args:
            token: Encoded JWT string to revoke.
        """
        try:
            # Decode without full verification to extract JTI (may be expired)
            payload = pyjwt.decode(
                token,
                self._secret_key,
                algorithms=[self._algorithm],
                options={"verify_exp": False},
            )
            jti = payload.get("jti")
            if jti:
                self._blacklisted_jtis.add(jti)
        except PyJWTError:
            # If we cannot decode the token at all, silently ignore.
            pass

    def is_blacklisted(self, jti: str) -> bool:
        """Check if a token JTI is in the blacklist."""
        return jti in self._blacklisted_jtis

    def clear_blacklist(self) -> int:
        """
        Clear all blacklisted JTIs. Returns the number cleared.
        Useful for periodic cleanup.
        """
        count = len(self._blacklisted_jtis)
        self._blacklisted_jtis.clear()
        return count

    async def create_tokens(
        self,
        sub: str,
        email: str,
        name: Optional[str] = None,
        role: str = "user",
        permissions: Optional[List[str]] = None,
        tier: str = "free",
        mfa_verified: bool = False,
        extra_claims: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create both access and refresh tokens."""
        claims = dict(extra_claims or {})
        if name:
            claims["name"] = name

        access_token = await self.create_access_token(
            user_id=sub,
            email=email,
            roles=[role],
            permissions=permissions,
            tier=tier,
            mfa_verified=mfa_verified,
            extra_claims=claims,
        )
        refresh_token = None
        if self._enable_refresh_tokens:
            refresh_token = await self.create_refresh_token(
                user_id=sub,
                extra_claims=claims,
            )

        result = {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": self._access_token_expiry,
        }
        if refresh_token:
            result["refresh_token"] = refresh_token
        return result


    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @property
    def access_token_expiry_seconds(self) -> int:
        """Return the configured access token lifetime in seconds."""
        return self._access_token_expiry

    @property
    def refresh_token_expiry_seconds(self) -> int:
        """Return the configured refresh token lifetime in seconds."""
        return self._refresh_token_expiry

    @property
    def refresh_tokens_enabled(self) -> bool:
        """Whether refresh tokens are enabled."""
        return self._enable_refresh_tokens


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------

class AuthError(Exception):
    """Base exception for authentication errors."""
    def __init__(self, message: str = "Authentication error", code: str = "AUTH_ERROR"):
        self.message = message
        self.code = code
        super().__init__(self.message)


class TokenExpiredError(AuthError):
    """Raised when a JWT token has expired."""
    def __init__(self, message: str = "Token has expired"):
        super().__init__(message=message, code="TOKEN_EXPIRED")


class InvalidTokenError(AuthError):
    """Raised when a JWT token is malformed or invalid."""
    def __init__(self, message: str = "Invalid token"):
        super().__init__(message=message, code="INVALID_TOKEN")


class TokenBlacklistedError(AuthError):
    """Raised when a token has been revoked / blacklisted."""
    def __init__(self, message: str = "Token has been revoked"):
        super().__init__(message=message, code="TOKEN_REVOKED")


class AuthenticationFailedError(AuthError):
    """Raised when credentials are invalid."""
    def __init__(self, message: str = "Invalid email or password"):
        super().__init__(message=message, code="AUTHENTICATION_FAILED")


class MFAPendingError(AuthError):
    """Raised when MFA verification is required before proceeding."""
    def __init__(self, message: str = "MFA verification required", user_id: str = ""):
        self.user_id = user_id
        super().__init__(message=message, code="MFA_REQUIRED")


class MFAInvalidCodeError(AuthError):
    """Raised when an MFA code is invalid."""
    def __init__(self, message: str = "Invalid MFA code"):
        super().__init__(message=message, code="MFA_INVALID_CODE")


class PermissionDeniedError(AuthError):
    """Raised when the user lacks required permissions."""
    def __init__(self, message: str = "Permission denied"):
        super().__init__(message=message, code="PERMISSION_DENIED")


class UserNotFoundError(AuthError):
    """Raised when a user cannot be found."""
    def __init__(self, message: str = "User not found"):
        super().__init__(message=message, code="USER_NOT_FOUND")


class UserAlreadyExistsError(AuthError):
    """Raised when registration conflicts with an existing user."""
    def __init__(self, message: str = "User already exists"):
        super().__init__(message=message, code="USER_ALREADY_EXISTS")


class APIKeyInvalidError(AuthError):
    """Raised when an API key is invalid or revoked."""
    def __init__(self, message: str = "Invalid or revoked API key"):
        super().__init__(message=message, code="API_KEY_INVALID")


class SessionInvalidError(AuthError):
    """Raised when a session is invalid or expired."""
    def __init__(self, message: str = "Invalid or expired session"):
        super().__init__(message=message, code="SESSION_INVALID")
