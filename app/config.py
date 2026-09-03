"""Environment-driven configuration.

12-factor: no hardcoded hosts, no absolute paths, no cloud-provider
assumptions. Production refuses to start when a required variable is
missing and never falls back to a development secret.
"""

from __future__ import annotations

import os


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


def _require(name: str) -> str:
    value = os.environ.get(name)
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

    def __init__(self) -> None:
        self.SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-not-for-production")
        self.MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
        self.MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "feedme_dev")
        self.JWT_SECRET = os.environ.get("JWT_SECRET", "dev-jwt-not-for-production")
        self.STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
        self.STORAGE_LOCAL_PATH = os.environ.get("STORAGE_LOCAL_PATH", "var/uploads")
        self.BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
        self.SESSION_COOKIE_SECURE = _flag(
            "SESSION_COOKIE_SECURE", self.SESSION_COOKIE_SECURE
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

    def __init__(self) -> None:
        super().__init__()
        self.SECRET_KEY = "testing-secret"
        self.JWT_SECRET = "testing-jwt-secret"
        self.MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "feedme_test")


class ProductionConfig(BaseConfig):
    ENV = "production"
    DEBUG = False
    SESSION_COOKIE_SECURE = True

    def __init__(self) -> None:
        super().__init__()
        self.SECRET_KEY = _require("SECRET_KEY")
        self.MONGO_URI = _require("MONGO_URI")
        self.MONGO_DB_NAME = _require("MONGO_DB_NAME")
        self.JWT_SECRET = _require("JWT_SECRET")
        self.BASE_URL = _require("BASE_URL")
        self.SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE", True)


CONFIGS: dict[str, type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def load_config(env: str | None = None) -> BaseConfig:
    name = (env or os.environ.get("FLASK_ENV") or "development").strip().lower()
    try:
        config_class = CONFIGS[name]
    except KeyError:
        raise ConfigError(
            f"unknown FLASK_ENV {name!r}; expected one of {sorted(CONFIGS)}"
        ) from None
    return config_class()
