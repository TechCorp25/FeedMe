"""Test fixtures.

Tests run against mongomock, never a live database (02-ARCHITECTURE.md).
"""

from __future__ import annotations

import mongomock
import pytest

from app import create_app
from app.config import TestingConfig


@pytest.fixture()
def mongo_client() -> mongomock.MongoClient:
    return mongomock.MongoClient(tz_aware=True)


@pytest.fixture()
def app(mongo_client):
    application = create_app(TestingConfig(), mongo_client=mongo_client)
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    """The test database, without holding an application context open.

    Deliberately not `with app.app_context(): yield get_db()`. Flask
    reuses an already-pushed context for a request on the same app, so a
    context held for the length of a test makes every request in it share
    one `g` — and Flask-Login caches the signed-in user there. A test that
    changed a user between two requests would then be served the first
    request's cached user and pass while the application did the wrong
    thing. Each request pushes its own context now, exactly as a served
    request does.
    """
    from app.db.client import get_db

    return get_db(app)
