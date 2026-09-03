"""JWT issuance for a future mobile client.

The web application does not use these tokens; it uses Flask-Login
sessions. Refresh-token rotation is deliberately not designed until a
mobile client exists (02-ARCHITECTURE.md).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import jwt
from flask import Flask, current_app

from app.models.base import utcnow
from app.models.users import Role


class TokenError(Exception):
    """Raised when a token cannot be issued or is not acceptable."""


def issue_access_token(user_id: str, role: Role, app: Flask | None = None) -> str:
    """Short-lived access token carrying `sub` and `role`."""
    app = app or current_app
    now = utcnow()
    lifetime = timedelta(seconds=app.config["JWT_ACCESS_TOKEN_SECONDS"])
    claims = {
        "sub": user_id,
        "role": role.value,
        "iat": int(now.timestamp()),
        "exp": int((now + lifetime).timestamp()),
        "typ": "access",
    }
    return jwt.encode(
        claims, app.config["JWT_SECRET"], algorithm=app.config["JWT_ALGORITHM"]
    )


def verify_access_token(token: str, app: Flask | None = None) -> dict[str, Any]:
    app = app or current_app
    try:
        claims = jwt.decode(
            token,
            app.config["JWT_SECRET"],
            algorithms=[app.config["JWT_ALGORITHM"]],
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims.get("typ") != "access":
        raise TokenError("not an access token")
    return claims


def issue_refresh_token(*_args: object, **_kwargs: object) -> str:
    """Deliberate stub. No refresh flow until a mobile client exists."""
    raise NotImplementedError(
        "refresh-token rotation is not designed until a mobile client exists"
    )
