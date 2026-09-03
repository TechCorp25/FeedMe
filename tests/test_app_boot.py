"""The factory must boot end to end: config, Mongo, indexes, routes."""

from __future__ import annotations

import pytest

from app import create_app
from app.config import TestingConfig
from app.db.indexes import INDEX_SPECS
from app.security.decorators import (
    UnmarkedRouteError,
    assert_routes_marked,
    marker_for,
    public_route,
)


def test_app_boots(app):
    assert app.config["TESTING"] is True


def test_index_bootstrap_ran(app, mongo_client):
    """Every declared index exists after boot, and re-running is safe."""
    database = mongo_client[app.config["MONGO_DB_NAME"]]
    for collection, specs in INDEX_SPECS.items():
        existing = set(database[collection].index_information())
        for _keys, options in specs:
            assert options["name"] in existing

    # Idempotent: booting a second app against the same client must not raise.
    create_app(TestingConfig(), mongo_client=mongo_client)


def test_every_route_carries_an_auth_marker(app):
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        assert marker_for(app.view_functions[rule.endpoint]) is not None, (
            f"{rule.endpoint} has no auth marker"
        )


def test_unmarked_route_fails_the_boot(app):
    @app.route("/unmarked-route")
    def unmarked():  # noqa: ANN202
        return ""

    with pytest.raises(UnmarkedRouteError, match="unmarked"):
        assert_routes_marked(app)


def test_a_route_carries_exactly_one_marker():
    from app.security.decorators import login_required

    def view():  # noqa: ANN202
        return ""

    with pytest.raises(ValueError, match="already carries"):
        login_required(public_route(view))
