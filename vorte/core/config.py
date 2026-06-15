"""
Vorte Configuration Module
============================
Centralized configuration management with environment variable support,
typed settings, and module-level configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path


def env(key: str, default: Optional[str] = None, prefix: str = "VORTE_") -> str:
    """Fetch an environment variable with optional VORTE_ prefix fallback."""
    full_key = f"{prefix}{key}"
    return os.environ.get(full_key) or os.environ.get(key) or default or ""


def env_bool(key: str, default: bool = False) -> bool:
    """Fetch a boolean environment variable."""
    val = env(key).lower()
    return val in ("true", "1", "yes", "on") if val else default


def env_int(key: str, default: int = 0) -> int:
    """Fetch an integer environment variable."""
    try:
        return int(env(key, str(default)))
    except (ValueError, TypeError):
        return default


def env_list(key: str, default: Optional[List[str]] = None, separator: str = ",") -> List[str]:
    """Fetch a list environment variable."""
    val = env(key)
    if not val:
        return default or []
    return [item.strip() for item in val.split(separator) if item.strip()]


@dataclass
class DatabaseConfig:
    """Database configuration."""
    url: str = field(default_factory=lambda: env("DATABASE_URL", "postgresql+asyncpg://localhost/vorte"))
    pool_size: int = field(default_factory=lambda: env_int("DB_POOL_SIZE", 20))
    max_overflow: int = field(default_factory=lambda: env_int("DB_MAX_OVERFLOW", 10))
    echo: bool = field(default_factory=lambda: env_bool("DB_ECHO", False))
    read_replica_urls: List[str] = field(default_factory=list)


@dataclass
class RedisConfig:
    """Redis configuration."""
    url: str = field(default_factory=lambda: env("REDIS_URL", "redis://localhost:6379/0"))
    cache_url: str = field(default_factory=lambda: env("REDIS_CACHE_URL", ""))
    queue_url: str = field(default_factory=lambda: env("REDIS_QUEUE_URL", ""))


@dataclass
class AuthConfig:
    """Authentication configuration."""
    strategy: str = field(default_factory=lambda: env("AUTH_STRATEGY", "jwt"))
    secret_key: str = field(default_factory=lambda: env("AUTH_SECRET_KEY", "vorte-secret-change-in-production"))
    refresh_tokens: bool = field(default_factory=lambda: env_bool("AUTH_REFRESH_TOKENS", True))
    mfa: bool = field(default_factory=lambda: env_bool("AUTH_MFA", False))
    oauth_providers: List[str] = field(default_factory=lambda: env_list("AUTH_OAUTH_PROVIDERS"))
    token_expiry_minutes: int = field(default_factory=lambda: env_int("AUTH_TOKEN_EXPIRY", 60))
    refresh_expiry_days: int = field(default_factory=lambda: env_int("AUTH_REFRESH_EXPIRY", 7))


@dataclass
class AIConfig:
    """AI integration configuration."""
    default_model: str = field(default_factory=lambda: env("AI_DEFAULT_MODEL", "gpt-4o"))
    fallback_providers: List[str] = field(default_factory=lambda: env_list("AI_FALLBACK", ["openai", "anthropic", "gemini"]))
    cache_responses: bool = field(default_factory=lambda: env_bool("AI_CACHE_RESPONSES", True))
    track_costs: bool = field(default_factory=lambda: env_bool("AI_TRACK_COSTS", True))
    max_tokens: int = field(default_factory=lambda: env_int("AI_MAX_TOKENS", 4096))
    temperature: float = field(default_factory=lambda: float(env("AI_TEMPERATURE", "0.7")))
    providers: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class CacheConfig:
    """Cache configuration."""
    driver: str = field(default_factory=lambda: env("CACHE_DRIVER", "redis"))
    default_ttl: int = field(default_factory=lambda: env_int("CACHE_TTL", 300))
    l1_enabled: bool = True
    l1_max_size: int = field(default_factory=lambda: env_int("CACHE_L1_MAX_SIZE", 1000))
    l2_enabled: bool = field(default_factory=lambda: env_bool("CACHE_L2_ENABLED", True))
    l3_cdn_url: str = field(default_factory=lambda: env("CACHE_CDN_URL", ""))
    l4_db_cache: bool = field(default_factory=lambda: env_bool("CACHE_DB_ENABLED", False))


@dataclass
class QueueConfig:
    """Queue / background jobs configuration."""
    driver: str = field(default_factory=lambda: env("QUEUE_DRIVER", "redis"))
    default_retries: int = field(default_factory=lambda: env_int("QUEUE_RETRIES", 3))
    default_retry_delay: int = field(default_factory=lambda: env_int("QUEUE_RETRY_DELAY", 5))
    concurrency: int = field(default_factory=lambda: env_int("QUEUE_CONCURRENCY", 10))


@dataclass
class StorageConfig:
    """File storage configuration."""
    driver: str = field(default_factory=lambda: env("STORAGE_DRIVER", "local"))
    bucket: str = field(default_factory=lambda: env("STORAGE_BUCKET", "vorte-uploads"))
    cdn_url: str = field(default_factory=lambda: env("STORAGE_CDN_URL", ""))
    local_path: str = field(default_factory=lambda: env("STORAGE_LOCAL_PATH", "storage/uploads"))
    access_key: str = field(default_factory=lambda: env("STORAGE_ACCESS_KEY", ""))
    secret_key: str = field(default_factory=lambda: env("STORAGE_SECRET_KEY", ""))
    region: str = field(default_factory=lambda: env("STORAGE_REGION", "us-east-1"))


@dataclass
class MailerConfig:
    """Mailer configuration."""
    driver: str = field(default_factory=lambda: env("MAILER_DRIVER", "smtp"))
    host: str = field(default_factory=lambda: env("MAIL_HOST", "localhost"))
    port: int = field(default_factory=lambda: env_int("MAIL_PORT", 587))
    username: str = field(default_factory=lambda: env("MAIL_USERNAME", ""))
    password: str = field(default_factory=lambda: env("MAIL_PASSWORD", ""))
    from_address: str = field(default_factory=lambda: env("MAIL_FROM", "noreply@vorte.dev"))
    from_name: str = field(default_factory=lambda: env("MAIL_FROM_NAME", "Vorte App"))


@dataclass
class MpesaConfig:
    """M-Pesa configuration."""
    environment: str = field(default_factory=lambda: env("MPESA_ENV", "sandbox"))
    consumer_key: str = field(default_factory=lambda: env("MPESA_CONSUMER_KEY", ""))
    consumer_secret: str = field(default_factory=lambda: env("MPESA_CONSUMER_SECRET", ""))
    shortcode: str = field(default_factory=lambda: env("MPESA_SHORTCODE", ""))
    passkey: str = field(default_factory=lambda: env("MPESA_PASSKEY", ""))
    callback_url: str = field(default_factory=lambda: env("MPESA_CALLBACK_URL", ""))


@dataclass
class PaymentsConfig:
    """Payments configuration."""
    provider: str = field(default_factory=lambda: env("PAYMENTS_PROVIDER", "stripe"))
    currency: str = field(default_factory=lambda: env("PAYMENTS_CURRENCY", "USD"))
    webhook_secret: str = field(default_factory=lambda: env("PAYMENTS_WEBHOOK_SECRET", ""))
    api_key: str = field(default_factory=lambda: env("PAYMENTS_API_KEY", ""))


@dataclass
class SecurityConfig:
    """Security configuration."""
    helmet: bool = field(default_factory=lambda: env_bool("SECURITY_HELMET", True))
    csrf: bool = field(default_factory=lambda: env_bool("SECURITY_CSRF", True))
    xss: bool = field(default_factory=lambda: env_bool("SECURITY_XSS", True))
    rate_limit: bool = field(default_factory=lambda: env_bool("SECURITY_RATE_LIMIT", True))
    rate_limit_max: int = field(default_factory=lambda: env_int("SECURITY_RATE_LIMIT_MAX", 100))
    rate_limit_window: int = field(default_factory=lambda: env_int("SECURITY_RATE_LIMIT_WINDOW", 60))
    bot_detection: bool = field(default_factory=lambda: env_bool("SECURITY_BOT_DETECTION", True))
    geo_blocking: List[str] = field(default_factory=list)


@dataclass
class SearchConfig:
    """Search configuration."""
    engine: str = field(default_factory=lambda: env("SEARCH_ENGINE", "database"))
    meilisearch_url: str = field(default_factory=lambda: env("MEILISEARCH_URL", "http://localhost:7700"))
    meilisearch_key: str = field(default_factory=lambda: env("MEILISEARCH_KEY", ""))
    pgvector_connection: str = field(default_factory=lambda: env("PGVECTOR_URL", ""))


@dataclass
class TenancyConfig:
    """Multi-tenancy configuration."""
    strategy: str = field(default_factory=lambda: env("TENANCY_STRATEGY", "header"))
    isolation: str = field(default_factory=lambda: env("TENANCY_ISOLATION", "schema"))


@dataclass
class I18nConfig:
    """Internationalization configuration."""
    default_locale: str = field(default_factory=lambda: env("I18N_DEFAULT_LOCALE", "en"))
    fallback_locale: str = field(default_factory=lambda: env("I18N_FALLBACK_LOCALE", "en"))
    locales_dir: str = field(default_factory=lambda: env("I18N_LOCALES_DIR", "locales"))


@dataclass
class FeatureFlagsConfig:
    """Feature flags configuration."""
    driver: str = field(default_factory=lambda: env("FF_DRIVER", "database"))


@dataclass
class GraphQLConfig:
    """GraphQL configuration."""
    enabled: bool = field(default_factory=lambda: env_bool("GRAPHQL_ENABLED", False))
    auto_schema: bool = field(default_factory=lambda: env_bool("GRAPHQL_AUTO_SCHEMA", True))
    playground: bool = field(default_factory=lambda: env_bool("GRAPHQL_PLAYGROUND", True))
    subscriptions: bool = field(default_factory=lambda: env_bool("GRAPHQL_SUBSCRIPTIONS", True))


@dataclass
class PerformanceConfig:
    """Performance configuration."""
    http2: bool = field(default_factory=lambda: env_bool("HTTP2_ENABLED", False))
    brotli: bool = field(default_factory=lambda: env_bool("BROTLI_ENABLED", True))
    brotli_threshold: int = field(default_factory=lambda: env_int("BROTLI_THRESHOLD", 1024))
    protobuf: bool = field(default_factory=lambda: env_bool("PROTOBUF_ENABLED", False))


@dataclass
class DashboardConfig:
    """Dashboard configuration."""
    enabled: bool = field(default_factory=lambda: env_bool("DASHBOARD_ENABLED", True))
    path: str = "/vorte/dashboard"
    auth_required: bool = field(default_factory=lambda: env_bool("DASHBOARD_AUTH", True))
    token: str = field(default_factory=lambda: env("DASHBOARD_TOKEN", ""))


@dataclass
class Settings:
    """
    Central Vorte application settings.
    All configuration is managed here with environment variable support.
    """
    # App
    app_name: str = field(default_factory=lambda: env("APP_NAME", "VorteApp"))
    app_env: str = field(default_factory=lambda: env("APP_ENV", "development"))
    app_debug: bool = field(default_factory=lambda: env_bool("APP_DEBUG", True))
    app_url: str = field(default_factory=lambda: env("APP_URL", "http://localhost:8000"))
    app_key: str = field(default_factory=lambda: env("APP_KEY", ""))
    api_prefix: str = "/api"
    default_version: str = "v1"
    timezone: str = field(default_factory=lambda: env("APP_TIMEZONE", "UTC"))
    cors_origins: List[str] = field(default_factory=lambda: env_list("CORS_ORIGINS", ["*"]))

    # Module configs
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    mailer: MailerConfig = field(default_factory=MailerConfig)
    mpesa: MpesaConfig = field(default_factory=MpesaConfig)
    payments: PaymentsConfig = field(default_factory=PaymentsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    tenancy: TenancyConfig = field(default_factory=TenancyConfig)
    i18n: I18nConfig = field(default_factory=I18nConfig)
    features: FeatureFlagsConfig = field(default_factory=FeatureFlagsConfig)
    graphql: GraphQLConfig = field(default_factory=GraphQLConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)

    # Paths
    base_dir: str = "."
    modules_dir: str = "app/modules"
    storage_path: str = "storage"
    logs_path: str = "storage/logs"

    @classmethod
    def from_env(cls) -> "Settings":
        """Create settings from environment variables."""
        from dotenv import load_dotenv
        load_dotenv()
        return cls()

    def is_production(self) -> bool:
        return self.app_env == "production"

    def is_development(self) -> bool:
        return self.app_env == "development"

    def is_testing(self) -> bool:
        return self.app_env == "testing"


# Global settings singleton
settings = Settings.from_env()
