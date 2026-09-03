"""Public routes.

Proof of life for the scaffold: `/` exercises the Jinja pipeline and the
base template; `/health` exercises the Mongo client. Catalogue browse and
item detail land with the catalogue slice.
"""

from __future__ import annotations

from flask import current_app, render_template

from app.blueprints.public import bp
from app.db.client import get_db
from app.security.decorators import public_route


@bp.get("/")
@public_route
def index() -> str:
    return render_template("index.html")


@bp.get("/health")
@public_route
def health() -> tuple[dict, int]:
    """Liveness plus a real database round-trip.

    Returns 503 when the database cannot be reached, so a deployment
    check fails loudly rather than serving a half-wired application.
    """
    database = "ok"
    status_code = 200
    try:
        get_db().command("ping")
    except Exception:  # noqa: BLE001 — any driver failure is unhealthy
        current_app.logger.exception("health check: database ping failed")
        database = "unavailable"
        status_code = 503
    return {"status": "ok" if status_code == 200 else "degraded",
            "database": database}, status_code
