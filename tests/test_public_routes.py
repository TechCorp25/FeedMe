"""Proof of life: the served HTML and the database round-trip."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def test_health_reports_a_live_database(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "database": "ok",
        # Detected, not configured: a workstation is the "local" platform.
        "platform": "local",
        "environment": "testing",
    }


def test_health_reports_degraded_when_the_database_is_unreachable(app, client):
    class Unreachable:
        def __getitem__(self, _name):
            return self

        def command(self, *_args, **_kwargs):
            raise ConnectionError("no server available")

    app.extensions["feedme_mongo_client"] = Unreachable()

    response = client.get("/health")
    assert response.status_code == 503
    assert response.get_json()["database"] == "unavailable"


def test_index_renders_without_javascript(client):
    """The page is complete HTML on first request; JS only enhances."""
    response = client.get("/")
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert "<h1" in html
    assert html.count("<h1") == 1
    assert "FeedMe" in html


def test_index_carries_the_mandatory_viewport_tag(client):
    html = client.get("/").get_data(as_text=True)
    assert (
        '<meta name="viewport" '
        'content="width=device-width, initial-scale=1, viewport-fit=cover">' in html
    )


def test_index_has_a_skip_link_and_a_main_landmark(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="#main"' in html
    assert 'id="main"' in html


def test_index_exposes_a_csrf_token_for_mutating_requests(client):
    html = client.get("/").get_data(as_text=True)
    assert re.search(r'<meta name="csrf-token" content="[^"]+"', html)


def test_the_theme_toggle_is_hidden_until_javascript_reveals_it(client):
    """A JS-less visitor is never shown a dead control."""
    html = client.get("/").get_data(as_text=True)
    assert "data-theme-toggle" in html
    assert re.search(r"data-theme-toggle\s+hidden", html)


def test_the_hidden_attribute_survives_the_compiled_stylesheet():
    """The markup alone is not enough to keep a `hidden` control hidden.

    A component-layer `display` from @apply beats the user-agent
    `[hidden] { display: none }` at equal specificity, so the stylesheet
    has to assert it. Without this rule the theme toggle paints for a
    visitor whose JavaScript never runs.
    """
    stylesheet = (
        Path(__file__).resolve().parents[1] / "app/static/css/app.css"
    ).read_text()
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important", stylesheet)


def test_unknown_page_renders_the_404_template(client):
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert "Not found" in response.get_data(as_text=True)
