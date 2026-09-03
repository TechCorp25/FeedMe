"""JSON API routes."""

from __future__ import annotations

from flask import request

from app.blueprints.api import bp
from app.db.repositories.users import get_user_by_email
from app.security.decorators import public_route
from app.security.passwords import verify_password
from app.security.tokens import issue_access_token


@bp.post("/auth/token")
@public_route
def issue_token() -> tuple[dict, int]:
    """Access token for a future mobile client. The web app does not use it.

    CSRF-exempt because it authenticates with credentials in the request
    body, not with a session cookie (see the factory).
    """
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", ""))
    password = str(payload.get("password", ""))

    user = get_user_by_email(email) if email else None
    if user is None or not user.is_active or not verify_password(
        user.password_hash, password
    ):
        # One message for every failure mode: never reveal which part failed.
        return {"error": "invalid_credentials"}, 401

    if user.id is None:  # unsaved user: cannot happen for a stored record
        return {"error": "invalid_credentials"}, 401

    return {
        "access_token": issue_access_token(user.id, user.role),
        "token_type": "Bearer",
    }, 200
