"""Deployment-platform detection.

The application must run unchanged on a workstation, in a Codespace, on
Railway and on Render. These tests pin the two things that make that
true: each platform is recognised from the variables it actually sets,
and a value stated explicitly always beats a detected one.
"""

from __future__ import annotations

import mongomock
import pytest

from app import create_app
from app.config import ProductionConfig, TestingConfig, load_config
from app.deployment import (
    CODESPACES,
    LOCAL,
    RAILWAY,
    RENDER,
    PlatformError,
    describe,
    detect,
)

CODESPACE_ENV = {
    "CODESPACES": "true",
    "CODESPACE_NAME": "curly-space-machine",
    "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev",
}
RAILWAY_ENV = {
    "RAILWAY_ENVIRONMENT": "production",
    "RAILWAY_ENVIRONMENT_NAME": "production",
    "RAILWAY_PUBLIC_DOMAIN": "feedme-production.up.railway.app",
    "PORT": "8080",
}
RENDER_ENV = {
    "RENDER": "true",
    "RENDER_SERVICE_ID": "srv-abc123",
    "RENDER_EXTERNAL_URL": "https://feedme.onrender.com",
    "PORT": "10000",
}


def test_a_workstation_is_the_local_platform():
    platform = detect({})
    assert platform.name == LOCAL
    assert platform.base_url == "http://localhost:5000"
    assert platform.behind_proxy is False
    assert platform.default_env == "development"


def test_local_publishes_no_external_origin():
    """Nothing about a workstation may satisfy a production requirement."""
    assert detect({}).external_base_url is None


def test_codespaces_is_detected_and_its_forwarded_url_derived():
    platform = detect({**CODESPACE_ENV, "PORT": "5000"})
    assert platform.name == CODESPACES
    # The forwarded hostname carries the port, so it must be in the URL.
    assert platform.base_url == "https://curly-space-machine-5000.app.github.dev"
    assert platform.serves_https is True
    # A Codespace is a development machine that happens to be remote.
    assert platform.default_env == "development"


def test_codespaces_honours_a_non_default_forwarding_domain():
    environ = {
        **CODESPACE_ENV,
        "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "example.dev",
    }
    assert detect(environ).base_url.endswith(".example.dev")


def test_codespaces_falls_back_to_the_published_domain():
    environ = {k: v for k, v in CODESPACE_ENV.items() if "FORWARDING" not in k}
    assert detect(environ).base_url == "https://curly-space-machine-5000.app.github.dev"


def test_railway_is_detected_from_its_environment_marker():
    platform = detect(RAILWAY_ENV)
    assert platform.name == RAILWAY
    assert platform.base_url == "https://feedme-production.up.railway.app"
    assert platform.port == 8080
    assert platform.is_managed is True
    assert platform.default_env == "production"


def test_railway_without_a_public_domain_advertises_no_origin():
    """A private service has no external URL to state, and must not invent one."""
    platform = detect({"RAILWAY_ENVIRONMENT": "production"})
    assert platform.name == RAILWAY
    assert platform.external_base_url is None


def test_render_is_detected_and_uses_the_url_it_publishes():
    platform = detect(RENDER_ENV)
    assert platform.name == RENDER
    assert platform.base_url == "https://feedme.onrender.com"
    assert platform.port == 10000
    assert platform.default_env == "production"


def test_render_falls_back_to_the_external_hostname():
    environ = {"RENDER": "true", "RENDER_EXTERNAL_HOSTNAME": "feedme.onrender.com"}
    assert detect(environ).base_url == "https://feedme.onrender.com"


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        (CODESPACE_ENV, CODESPACES),
        (RAILWAY_ENV, RAILWAY),
        (RENDER_ENV, RENDER),
    ],
)
def test_every_managed_platform_terminates_tls_in_front_of_the_process(
    environ, expected
):
    platform = detect(environ)
    assert platform.name == expected
    assert platform.behind_proxy is True
    assert platform.serves_https is True


def test_the_port_the_platform_assigns_wins_over_the_default():
    assert detect({**RENDER_ENV, "PORT": "3001"}).port == 3001


def test_a_non_numeric_port_fails_loudly():
    with pytest.raises(PlatformError, match="PORT"):
        detect({"PORT": "http"})


def test_deploy_platform_forces_a_result():
    """Reproduce a platform's URL and cookie behaviour anywhere."""
    platform = detect({"DEPLOY_PLATFORM": "render", "PORT": "10000"})
    assert platform.name == RENDER
    assert platform.behind_proxy is True


def test_an_unknown_forced_platform_is_rejected():
    with pytest.raises(PlatformError, match="unknown DEPLOY_PLATFORM"):
        detect({"DEPLOY_PLATFORM": "heroku"})


def test_the_report_carries_no_secrets():
    environ = {**RENDER_ENV, "SECRET_KEY": "prod-secret", "MONGO_URI": "mongodb://x"}
    report = describe(detect(environ))
    assert "prod-secret" not in repr(report)
    assert "mongodb" not in repr(report)


class TestConfigIntegration:
    """What detection changes about the resolved configuration."""

    def test_production_takes_its_base_url_from_the_platform(self, monkeypatch):
        for name, value in {
            "SECRET_KEY": "s",
            "MONGO_URI": "mongodb://db:27017",
            "MONGO_DB_NAME": "feedme",
            "JWT_SECRET": "j",
        }.items():
            monkeypatch.setenv(name, value)
        monkeypatch.delenv("BASE_URL", raising=False)

        config = ProductionConfig(detect(RENDER_ENV))
        assert config.BASE_URL == "https://feedme.onrender.com"
        assert config.DEPLOY_PLATFORM == RENDER
        assert config.TRUST_PROXY_HEADERS is True

    def test_an_explicit_base_url_beats_the_detected_one(self, monkeypatch):
        monkeypatch.setenv("BASE_URL", "https://feedme.example")
        assert TestingConfig(detect(RENDER_ENV)).BASE_URL == "https://feedme.example"

    def test_a_proxy_platform_secures_cookies_outside_production(self, monkeypatch):
        """Codespaces serves HTTPS, so the session cookie may be Secure there."""
        monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
        assert TestingConfig(detect(CODESPACE_ENV)).SESSION_COOKIE_SECURE is True
        assert TestingConfig(detect({})).SESSION_COOKIE_SECURE is False

    def test_an_explicit_flag_beats_the_detected_one(self, monkeypatch):
        monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
        assert TestingConfig(detect(CODESPACE_ENV)).SESSION_COOKIE_SECURE is False

    def test_a_managed_platform_defaults_the_environment_to_production(
        self, monkeypatch
    ):
        """A deploy that forgets FLASK_ENV must get the hardened config."""
        for name, value in RAILWAY_ENV.items():
            monkeypatch.setenv(name, value)
        for name, value in {
            "SECRET_KEY": "s",
            "MONGO_URI": "mongodb://db:27017",
            "MONGO_DB_NAME": "feedme",
            "JWT_SECRET": "j",
        }.items():
            monkeypatch.setenv(name, value)
        monkeypatch.delenv("FLASK_ENV", raising=False)
        monkeypatch.delenv("BASE_URL", raising=False)

        config = load_config()
        assert config.ENV == "production"
        assert config.BASE_URL == "https://feedme-production.up.railway.app"

    def test_an_explicit_flask_env_beats_the_platform_default(self, monkeypatch):
        for name, value in RAILWAY_ENV.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("FLASK_ENV", "development")
        assert load_config().ENV == "development"


class TestForwardedHeaders:
    """`X-Forwarded-*` is read on a platform proxy and nowhere else."""

    def _boot(self, environ):
        return create_app(
            TestingConfig(detect(environ)),
            mongo_client=mongomock.MongoClient(tz_aware=True),
        )

    def test_a_workstation_does_not_trust_forwarded_headers(self):
        app = self._boot({})
        assert app.config["TRUST_PROXY_HEADERS"] is False
        assert type(app.wsgi_app).__name__ != "ProxyFix"

    @pytest.mark.parametrize("environ", [CODESPACE_ENV, RAILWAY_ENV, RENDER_ENV])
    def test_a_platform_proxy_is_trusted_for_one_hop(self, environ):
        app = self._boot(environ)
        assert type(app.wsgi_app).__name__ == "ProxyFix"

    def test_the_forwarded_scheme_and_host_reach_the_application(self):
        """Without this the app builds http:// URLs behind an HTTPS edge."""
        app = self._boot(RENDER_ENV)

        result = _probe(app).get_json()
        assert result == {"scheme": "https", "host": "feedme.onrender.com"}

    def test_a_forged_forwarded_header_is_ignored_off_platform(self):
        """Off a known proxy the headers are a client's claim, not a fact."""
        app = self._boot({})

        result = _probe(app).get_json()
        assert result["host"] != "feedme.onrender.com"
        assert result["scheme"] == "http"


def _probe(app):
    """Send a forwarded request and report what the application saw."""
    from flask import request

    from app.security.decorators import public_route

    @app.route("/__probe")
    @public_route
    def probe():  # noqa: ANN202
        return {"scheme": request.scheme, "host": request.host}

    return app.test_client().get(
        "/__probe",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "feedme.onrender.com",
        },
    )
