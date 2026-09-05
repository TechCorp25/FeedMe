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
        exclude_allergens=request.args.getlist("exclude"),
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


@bp.get("/dishes")
@public_route
def dishes() -> str:
    """Dish catalogue.

    Filters arrive as a plain GET form, so the page works with JavaScript
    disabled and every filtered view is a linkable URL. Meal type travels
    as a slug for the same reason.
    """
    browse = catalogue.browse_dishes(
        meal_type=request.args.get("meal_type"),
        preference_flags=request.args.getlist("preference"),
        exclude_allergens=request.args.getlist("exclude"),
    )
    return render_template(
        "catalogue/dishes.html",
        browse=browse,
        menu_meal_types=catalogue.list_menu_meal_types(),
    )


@bp.get("/dishes/<slug>")
@public_route
def dish_detail(slug: str) -> str:
    """One dish, with all four tab panels in the served HTML.

    The dish's own tabs are authoritative; referenced components appear as
    provenance links only and never alter what the tabs say (01-DOMAIN.md).
    """
    detail = catalogue.get_dish_detail(slug)
    if detail is None:
        abort(404)
    return render_template("catalogue/dish_detail.html", detail=detail)


@bp.get("/menu/<meal_type_slug>")
@public_route
def menu(meal_type_slug: str) -> str:
    """One meal type's dishes — the third ordering entry point.

    The same dishes, cards and detail pages as `/dishes`; the meal type
    is how the customer arrived, not a different catalogue
    (04-WORKFLOWS.md). A slug that names no meal type is a 404: a path
    that names nothing is not the same as a filter value that does not
    apply, and serving the whole catalogue under a heading the customer
    did not ask for would be worse than saying so.
    """
    browse = catalogue.browse_menu(
        meal_type_slug,
        preference_flags=request.args.getlist("preference"),
        exclude_allergens=request.args.getlist("exclude"),
    )
    if browse is None:
        abort(404)
    return render_template("catalogue/menu.html", browse=browse)


@bp.get("/health")
@public_route
def health() -> tuple[dict, int]:
    """Liveness plus a real database round-trip.

    Returns 503 when the database cannot be reached, so a deployment
    check fails loudly rather than serving a half-wired application.

    The detected platform and environment are reported so a deploy can be
    confirmed to have resolved its host correctly without reading the
    logs. Neither is a secret, and neither is read from user input.
    """
    database = "ok"
    status_code = 200
    try:
        get_db().command("ping")
    except Exception:  # noqa: BLE001 — any driver failure is unhealthy
        current_app.logger.exception("health check: database ping failed")
        database = "unavailable"
        status_code = 503
    return {
        "status": "ok" if status_code == 200 else "degraded",
        "database": database,
        "platform": current_app.config["DEPLOY_PLATFORM"],
        "environment": current_app.config["ENV"],
    }, status_code
