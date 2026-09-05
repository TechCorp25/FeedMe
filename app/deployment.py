"""Deployment-platform detection.

The same code runs on a workstation, inside a GitHub Codespace, on
Railway and on Render. Each of those announces itself through
environment variables, so the platform is *detected* rather than
configured, and the four facts that actually differ between them are
derived from that detection:

``base_url``
    The externally reachable origin. Codespaces forwards a port through
    a generated hostname, Railway and Render each publish their own.
``port``
    The port the process must bind. Railway and Render assign one.
``behind_proxy``
    Whether TLS is terminated in front of the process, which decides
    whether ``X-Forwarded-*`` may be trusted.
``default_env``
    Which ``FLASK_ENV`` to assume when none is set. A managed host
    defaults to production so a deployment without ``FLASK_ENV`` gets
    the hardened config — and, because production has no secret
    fallbacks, refuses to boot rather than serving a development secret
    on a public URL.

Detection never overrides an explicit variable: everything here is a
default that ``SECRET_KEY``, ``BASE_URL``, ``PORT``, ``FLASK_ENV`` and
``DEPLOY_PLATFORM`` in the environment take precedence over. Nothing in
this module reaches the network, so it is safe to call at import time
and in tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Mapping

__all__ = [
    "LOCAL",
    "CODESPACES",
    "RAILWAY",
    "RENDER",
    "Platform",
    "PlatformError",
    "detect",
    "describe",
]

LOCAL = "local"
CODESPACES = "codespaces"
RAILWAY = "railway"
RENDER = "render"

#: Codespaces publishes forwarded ports under this domain unless it says
#: otherwise through GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN.
_CODESPACES_DOMAIN = "app.github.dev"

_DEFAULT_PORTS = {
    LOCAL: 5000,
    CODESPACES: 5000,
    RAILWAY: 8080,
    RENDER: 10000,
}


class PlatformError(RuntimeError):
    """Raised when the environment describes a platform that cannot be used."""


@dataclass(frozen=True)
class Platform:
    """What the surrounding host tells us about itself."""

    name: str
    display_name: str
    port: int
    behind_proxy: bool
    serves_https: bool
    default_env: str
    base_url: str | None = None
    details: Mapping[str, str] = field(default_factory=dict)

    @property
    def external_base_url(self) -> str | None:
        """The origin the host publishes to the outside world, if any.

        A workstation publishes nothing, so this is None there. Production
        config uses it as the only fallback for `BASE_URL`, which keeps a
        localhost origin from ever satisfying a production requirement.
        """
        return None if self.name == LOCAL else self.base_url

    @property
    def is_managed(self) -> bool:
        """True where a platform, not a person, controls the process."""
        return self.name in {RAILWAY, RENDER}


def _port(environ: Mapping[str, str], default: int) -> int:
    raw = environ.get("PORT")
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        raise PlatformError(f"PORT must be an integer, got {raw!r}") from None


def _present(environ: Mapping[str, str], *names: str) -> bool:
    return any(environ.get(name) for name in names)


def _truthy(value: str | None) -> bool:
    return bool(value) and value.strip().lower() in {"1", "true", "yes", "on"}


def _detail(environ: Mapping[str, str], *names: str) -> dict[str, str]:
    """Non-secret identifiers, for the boot log and the health endpoint."""
    return {name: environ[name] for name in names if environ.get(name)}


def _codespaces(environ: Mapping[str, str]) -> Platform:
    port = _port(environ, _DEFAULT_PORTS[CODESPACES])
    name = environ.get("CODESPACE_NAME")
    domain = (
        environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN") or _CODESPACES_DOMAIN
    )
    # The forwarded hostname carries the port, so a Codespace on 5000 and
    # one on 8080 are different origins.
    base_url = f"https://{name}-{port}.{domain}" if name else None
    return Platform(
        name=CODESPACES,
        display_name="GitHub Codespaces",
        port=port,
        behind_proxy=True,
        serves_https=True,
        # A Codespace is a development machine that happens to be remote.
        default_env="development",
        base_url=base_url,
        details=_detail(environ, "CODESPACE_NAME", "GITHUB_USER"),
    )


def _railway(environ: Mapping[str, str]) -> Platform:
    domain = environ.get("RAILWAY_PUBLIC_DOMAIN")
    static_url = environ.get("RAILWAY_STATIC_URL")
    if domain:
        base_url = f"https://{domain}"
    elif static_url:
        base_url = static_url if "://" in static_url else f"https://{static_url}"
    else:
        # A service with no public domain is reachable only on the private
        # network; there is no external origin to advertise.
        base_url = None
    return Platform(
        name=RAILWAY,
        display_name="Railway",
        port=_port(environ, _DEFAULT_PORTS[RAILWAY]),
        behind_proxy=True,
        serves_https=True,
        default_env="production",
        base_url=base_url,
        details=_detail(
            environ,
            "RAILWAY_ENVIRONMENT_NAME",
            "RAILWAY_PROJECT_NAME",
            "RAILWAY_SERVICE_NAME",
            "RAILWAY_GIT_COMMIT_SHA",
        ),
    )


def _render(environ: Mapping[str, str]) -> Platform:
    external = environ.get("RENDER_EXTERNAL_URL")
    hostname = environ.get("RENDER_EXTERNAL_HOSTNAME")
    if external:
        base_url = external
    elif hostname:
        base_url = f"https://{hostname}"
    else:
        base_url = None
    return Platform(
        name=RENDER,
        display_name="Render",
        port=_port(environ, _DEFAULT_PORTS[RENDER]),
        behind_proxy=True,
        serves_https=True,
        default_env="production",
        base_url=base_url,
        details=_detail(
            environ,
            "RENDER_SERVICE_NAME",
            "RENDER_INSTANCE_ID",
            "RENDER_GIT_COMMIT",
        ),
    )


def _local(environ: Mapping[str, str]) -> Platform:
    port = _port(environ, _DEFAULT_PORTS[LOCAL])
    return Platform(
        name=LOCAL,
        display_name="local",
        port=port,
        behind_proxy=False,
        serves_https=False,
        default_env="development",
        base_url=f"http://localhost:{port}",
    )


#: Ordered: the first platform whose signal is present wins. Codespaces is
#: checked first because a Codespace can carry another platform's CLI
#: variables, but never the other way round.
_DETECTORS: tuple[tuple[str, Callable[[Mapping[str, str]], bool]], ...] = (
    (
        CODESPACES,
        lambda e: _truthy(e.get("CODESPACES")) or _present(e, "CODESPACE_NAME"),
    ),
    (
        RAILWAY,
        lambda e: _present(
            e, "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_SERVICE_ID"
        ),
    ),
    (RENDER, lambda e: _truthy(e.get("RENDER")) or _present(e, "RENDER_SERVICE_ID")),
)

_BUILDERS: dict[str, Callable[[Mapping[str, str]], Platform]] = {
    LOCAL: _local,
    CODESPACES: _codespaces,
    RAILWAY: _railway,
    RENDER: _render,
}


def detect(environ: Mapping[str, str] | None = None) -> Platform:
    """Identify the host this process is running on.

    `DEPLOY_PLATFORM` forces a result — useful to reproduce a platform's
    URL and cookie behaviour locally, and as the escape hatch when a host
    changes the variables it sets.
    """
    environ = os.environ if environ is None else environ

    forced = (environ.get("DEPLOY_PLATFORM") or "").strip().lower()
    if forced:
        try:
            build = _BUILDERS[forced]
        except KeyError:
            raise PlatformError(
                f"unknown DEPLOY_PLATFORM {forced!r}; "
                f"expected one of {sorted(_BUILDERS)}"
            ) from None
        return build(environ)

    for name, signals_present in _DETECTORS:
        if signals_present(environ):
            return _BUILDERS[name](environ)
    return _local(environ)


def describe(platform: Platform) -> dict[str, object]:
    """A log- and JSON-safe summary. Carries no secrets by construction."""
    return {
        "platform": platform.name,
        "port": platform.port,
        "base_url": platform.base_url,
        "behind_proxy": platform.behind_proxy,
        **platform.details,
    }
