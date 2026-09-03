"""auth blueprint: login, logout, register, password reset.

`login_manager.login_view` points at `auth.login`, which lands with the
auth flow. No route currently requires authentication, so nothing can
trigger that redirect yet.
"""

from flask import Blueprint

bp = Blueprint("auth", __name__)

from app.blueprints.auth import routes  # noqa: E402,F401
