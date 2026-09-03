"""Route auth markers and their enforcement.

Every endpoint carries exactly one marker. An unmarked endpoint is a
defect, and `assert_routes_marked` refuses to let the application boot
when one exists (02-ARCHITECTURE.md).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TypeVar

import flask_login
from flask import Flask, abort, current_app
from flask_login import current_user

from app.models.users import Role

AUTH_MARKER_ATTR = "_feedme_auth_marker"

MARKER_PUBLIC = "public"
MARKER_LOGIN_REQUIRED = "login_required"
MARKER_CHEF_REQUIRED = "chef_required"

F = TypeVar("F", bound=Callable[..., object])

#: Endpoints Flask registers itself; they carry no application marker.
FRAMEWORK_ENDPOINTS = frozenset({"static"})


def _mark(view: F, marker: str) -> F:
    existing = getattr(view, AUTH_MARKER_ATTR, None)
    if existing is not None and existing != marker:
        raise ValueError(
            f"{getattr(view, '__name__', view)!r} already carries the "
            f"{existing!r} marker; a route carries exactly one"
        )
    setattr(view, AUTH_MARKER_ATTR, marker)
    return view


def public_route(view: F) -> F:
    """Explicit 'no authentication' marker. Never implicit."""
    return _mark(view, MARKER_PUBLIC)


def login_required(view: F) -> F:
    """Any authenticated user. Wraps Flask-Login so the marker is set."""
    wrapped = flask_login.login_required(view)
    return _mark(wrapped, MARKER_LOGIN_REQUIRED)


def chef_required(view: F) -> F:
    """chef_admin only.

    An authenticated customer gets 404, not 403: a 403 would confirm that
    the route exists.
    """

    @functools.wraps(view)
    def wrapper(*args: object, **kwargs: object) -> object:
        if not current_user.is_authenticated:
            return current_app.login_manager.unauthorized()
        if getattr(current_user, "role", None) is not Role.CHEF_ADMIN:
            abort(404)
        return view(*args, **kwargs)

    return _mark(wrapper, MARKER_CHEF_REQUIRED)


def marker_for(view: Callable[..., object]) -> str | None:
    return getattr(view, AUTH_MARKER_ATTR, None)


class UnmarkedRouteError(RuntimeError):
    """Raised at boot when an endpoint carries no auth marker."""


def assert_routes_marked(app: Flask) -> None:
    """Fail to boot if any endpoint lacks an explicit auth marker."""
    unmarked = sorted(
        rule.endpoint
        for rule in app.url_map.iter_rules()
        if rule.endpoint not in FRAMEWORK_ENDPOINTS
        and marker_for(app.view_functions[rule.endpoint]) is None
    )
    if unmarked:
        raise UnmarkedRouteError(
            "endpoints without an auth marker (@public_route, "
            "@login_required or @chef_required): " + ", ".join(unmarked)
        )
