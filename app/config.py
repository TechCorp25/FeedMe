"""Environment-driven configuration.

12-factor: no hardcoded hosts, no absolute paths, no cloud-provider
assumptions. Production refuses to start when a required variable is
missing and never falls back to a development secret.

Where a value is genuinely a property of the host rather than of the
application — the external origin, the port to bind, whether TLS is
terminated upstream — it is taken from `app.deployment`, which reads it
off the platform's own environment variables. An explicit variable always
wins over a detected default, so nothing here becomes unoverridable.
"""

from __future__ import annotations

import os

from app.deployment import Platform, detect


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is required and has no default")
    return value


def _require_or(name: str, fallback: str | None) -> str:
    """`name` from the environment, else a platform-supplied value.

    Production still refuses to start when neither exists — detection
    removes the busywork of restating what the host already knows, not
    the guarantee that the value is present.
    """
    value = os.environ.get(name) or fallback
    if not value:
        raise ConfigError(f"{name} is required and has no default")
    return value


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class BaseConfig:
    """Shared defaults. Subclasses resolve environment on instantiation.

    Configuration is resolved when the config object is constructed, which
    `create_app` does before anything else touches the environment — the
    earliest point that does not break importing this module in a shell
    with no environment set.
    """

    ENV = "development"
    TESTING = False
    DEBUG = False

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None

    JWT_ALGORITHM = "HS256"
    JWT_ACCESS_TOKEN_SECONDS = 900

    MONGO_SERVER_SELECTION_TIMEOUT_MS = 5000
    STORAGE_BACKEND = "local"

    def __init__(self, platform: Platform | None = None) -> None:
        self.PLATFORM: Platform = platform or detect()
        self.DEPLOY_PLATFORM = self.PLATFORM.name
        self.PORT = self.PLATFORM.port
        # X-Forwarded-* is only worth reading where a proxy we did not
        # choose is guaranteed to set it. Trusting it otherwise lets a
        # client dictate its own scheme and host.
        self.TRUST_PROXY_HEADERS = _flag(
            "TRUST_PROXY_HEADERS", self.PLATFORM.behind_proxy
        )
        self.PREFERRED_URL_SCHEME = "https" if self.PLATFORM.serves_https else "http"

        self.SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-not-for-production")
        self.MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
        self.MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "feedme_dev")
        self.JWT_SECRET = os.environ.get("JWT_SECRET", "dev-jwt-not-for-production")
        self.STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
        self.STORAGE_LOCAL_PATH = os.environ.get("STORAGE_LOCAL_PATH", "var/uploads")
        self.BASE_URL = (
            os.environ.get("BASE_URL")
            or self.PLATFORM.base_url
            or "http://localhost:5000"
        )
        self.SESSION_COOKIE_SECURE = _flag(
            "SESSION_COOKIE_SECURE",
            self.SESSION_COOKIE_SECURE or self.PLATFORM.serves_https,
        )


class DevelopmentConfig(BaseConfig):
    ENV = "development"
    DEBUG = True


class TestingConfig(BaseConfig):
    #: pytest would otherwise try to collect this as a test class.
    __test__ = False

    ENV = "testing"
    TESTING = True
    WTF_CSRF_ENABLED = False

    def __init__(self, platform: Platform | None = None) -> None:
        super().__init__(platform)
        self.SECRET_KEY = "testing-secret"
        self.JWT_SECRET = "testing-jwt-secret"
        self.MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "feedme_test")


class ProductionConfig(BaseConfig):
    ENV = "production"
    DEBUG = False
    SESSION_COOKIE_SECURE = True

    def __init__(self, platform: Platform | None = None) -> None:
        super().__init__(platform)
        self.SECRET_KEY = _require("SECRET_KEY")
        self.MONGO_URI = _require("MONGO_URI")
        self.MONGO_DB_NAME = _require("MONGO_DB_NAME")
        self.JWT_SECRET = _require("JWT_SECRET")
        # The one required value a platform can answer for us: Railway and
        # Render both publish the origin they serve this service on.
        self.BASE_URL = _require_or("BASE_URL", self.PLATFORM.external_base_url)
        self.SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE", True)


CONFIGS: dict[str, type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def load_config(env: str | None = None) -> BaseConfig:
    """Resolve the config class, defaulting the environment to the platform.

    A managed host (Railway, Render) defaults to production, so a deploy
    that forgets `FLASK_ENV` gets the hardened configuration and fails
    loudly on a missing secret instead of quietly serving a development
    one on a public URL.
    """
    platform = detect()
    requested = env or os.environ.get("FLASK_ENV") or platform.default_env
    name = requested.strip().lower()
    try:
        config_class = CONFIGS[name]
    except KeyError:
        raise ConfigError(
            f"unknown FLASK_ENV {name!r}; expected one of {sorted(CONFIGS)}"
        ) from None
    return config_class(platform)
