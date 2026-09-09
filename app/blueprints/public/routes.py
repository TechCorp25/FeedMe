"""Public routes: landing, catalogue browse and item detail.

Every page here returns complete HTML on first request. JavaScript
enhances the tab strip; it never gates content (00-SYSTEM.md).
"""

from __future__ import annotations

from flask import abort, current_app, render_template, request, url_for
from flask_login import current_user

from app.blueprints.public import bp
from app.db.client import get_db
from app.security.decorators import public_route
from app.services import catalogue


#: Present in the query string of any URL that states its own filters,
#: including one that states none.
#:
#: 01-DOMAIN.md gives a customer `default_preference_filters` to
#: "pre-apply browse filters", and a GET form submitted with every
#: checkbox cleared sends no `preference` at all — indistinguishable, on
#: the wire, from arriving at the page fresh. Without a marker, clearing
#: the filters would silently re-apply the defaults and the control would
#: appear broken. The filter form carries this hidden field and every
#: "clear" link sets it, so a stated selection is always taken literally.
FILTERS_STATED = "filtered"

#: Every query-string key that states a filter on some browse surface.
#:
#: The marker above covers the form, but not a URL written before the
#: marker existed. `/components?exclude=milk` is somebody's bookmark, or
#: a link they sent to a friend, and it states exactly what it wants;
#: adding this customer's saved preferences on top would quietly return
#: something else than the link says. Any of these keys, present at all,
#: means the URL speaks for itself.
CATALOGUE_FILTER_KEYS = ("preference", "exclude", "category", "meal_type")


def _preference_selection() -> tuple[list[str], bool]:
    """The preference flags to apply, and whether they are the defaults.

    Defaults apply only to an arrival that states nothing at all: no
    filter key of any kind, and no marker saying the filters were chosen.
    They are the customer's own, they only ever narrow, and the page says
    so and offers the unfiltered catalogue — a shortened list that does
    not explain itself would read as the whole catalogue.
    """
    if FILTERS_STATED in request.args or any(
        key in request.args for key in CATALOGUE_FILTER_KEYS
    ):
        return request.args.getlist("preference"), False
    if not current_user.is_authenticated:
        return [], False
    return list(getattr(current_user, "default_preference_filters", [])), True



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
    preferences, from_defaults = _preference_selection()
    browse = catalogue.browse_components(
        category=request.args.get("category"),
        preference_flags=preferences,
        exclude_allergens=request.args.getlist("exclude"),
    )
    # Reported from what the catalogue accepted, not from what is
    # stored: profile choices are the union of both catalogues, so a
    # dish-only or retired flag is dropped when browsing components —
    # and a notice naming no filters over an unnarrowed list is worse
    # than no notice at all.
    defaults_applied = from_defaults and bool(browse.filters.preference_flags)
    return render_template(
        "catalogue/components.html",
        browse=browse,
        defaults_applied=defaults_applied,
        unfiltered_url=url_for("public.components", **{FILTERS_STATED: 1}),
    )


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
    preferences, from_defaults = _preference_selection()
    browse = catalogue.browse_dishes(
        meal_type=request.args.get("meal_type"),
        preference_flags=preferences,
        exclude_allergens=request.args.getlist("exclude"),
    )
    # Reported from what the catalogue accepted, not from what is
    # stored: profile choices are the union of both catalogues, so a
    # dish-only or retired flag is dropped when browsing components —
    # and a notice naming no filters over an unnarrowed list is worse
    # than no notice at all.
    defaults_applied = from_defaults and bool(browse.filters.preference_flags)
    return render_template(
        "catalogue/dishes.html",
        browse=browse,
        menu_meal_types=catalogue.list_menu_meal_types(),
        defaults_applied=defaults_applied,
        unfiltered_url=url_for("public.dishes", **{FILTERS_STATED: 1}),
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
    preferences, from_defaults = _preference_selection()
    browse = catalogue.browse_menu(
        meal_type_slug,
        preference_flags=preferences,
        exclude_allergens=request.args.getlist("exclude"),
    )
    if browse is None:
        abort(404)
    # Reported from what the catalogue accepted, not from what is
    # stored: profile choices are the union of both catalogues, so a
    # dish-only or retired flag is dropped on a menu that does not use
    # it — and a notice naming no filters over an unnarrowed list is
    # worse than no notice at all.
    defaults_applied = from_defaults and bool(browse.filters.preference_flags)
    return render_template(
        "catalogue/menu.html",
        browse=browse,
        defaults_applied=defaults_applied,
        unfiltered_url=url_for(
            "public.menu", meal_type_slug=meal_type_slug, **{FILTERS_STATED: 1}
        ),
    )


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
