"""Configuration. Production never falls back to a development secret."""

from __future__ import annotations

import pytest

from app.config import ConfigError, DevelopmentConfig, ProductionConfig, load_config

REQUIRED = {
    "SECRET_KEY": "prod-secret",
    "MONGO_URI": "mongodb://db:27017",
    "MONGO_DB_NAME": "feedme",
    "JWT_SECRET": "prod-jwt",
    "BASE_URL": "https://example.test",
}


@pytest.fixture()
def clean_env(monkeypatch):
    for name in (*REQUIRED, "FLASK_ENV", "SESSION_COOKIE_SECURE", "STORAGE_LOCAL_PATH"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_production_refuses_to_start_without_a_required_variable(clean_env, missing):
    for name, value in REQUIRED.items():
        if name != missing:
            clean_env.setenv(name, value)

    with pytest.raises(ConfigError, match=missing):
        ProductionConfig()


def test_production_uses_the_supplied_secrets(clean_env):
    for name, value in REQUIRED.items():
        clean_env.setenv(name, value)

    config = ProductionConfig()
    assert config.SECRET_KEY == "prod-secret"
    assert config.SESSION_COOKIE_SECURE is True
    assert "dev" not in config.SECRET_KEY


def test_the_development_secret_can_never_reach_production(clean_env):
    """Development has a placeholder default; production has no default."""
    development = DevelopmentConfig()
    assert "dev" in development.SECRET_KEY
    assert "dev" in development.JWT_SECRET

    for name, value in REQUIRED.items():
        if name != "SECRET_KEY":
            clean_env.setenv(name, value)
    with pytest.raises(ConfigError, match="SECRET_KEY"):
        ProductionConfig()


def test_session_cookie_defaults_are_hardened(clean_env):
    config = DevelopmentConfig()
    assert config.SESSION_COOKIE_HTTPONLY is True
    assert config.SESSION_COOKIE_SAMESITE == "Lax"
    assert config.WTF_CSRF_ENABLED is True


def test_unknown_environment_is_rejected(clean_env):
    with pytest.raises(ConfigError, match="unknown FLASK_ENV"):
        load_config("staging")


def test_no_absolute_paths_or_hosts_are_hardcoded(clean_env):
    config = DevelopmentConfig()
    assert not config.STORAGE_LOCAL_PATH.startswith("/")
