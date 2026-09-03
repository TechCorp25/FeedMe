"""Public routes: landing, catalogue browse and item detail.

Every page here returns complete HTML on first request. JavaScript
enhances the tab strip; it never gates content (00-SYSTEM.md).
"""

from __future__ import annotations

from flask import abort, current_app, render_template, request

from app.blueprints.public import bp
from app.db.client import get_db
from app.security.decorators import public_route
from app.services import catalogue


@bp.get("/")
@public_route
def index() -> str:
    return render_template("index.html")


@bp.get("/components")
@public_route
def components() -> str:
    """Components catalogue.

    Filters arrive as a plain GET form, so the page works with JavaScript
    disabled and every filtered view is a linkable URL.
    """
    browse = catalogue.browse_components(
        category=request.args.get("category"),
        preference_flags=request.args.getlist("preference"),
    )
    return render_template("catalogue/components.html", browse=browse)


@bp.get("/components/<slug>")
@public_route
def component_detail(slug: str) -> str:
    """One component, with all four tab panels in the served HTML."""
    item = catalogue.get_component_detail(slug)
    if item is None:
        abort(404)
    return render_template("catalogue/component_detail.html", item=item)


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
