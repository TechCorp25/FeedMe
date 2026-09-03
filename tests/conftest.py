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
    from app.db.client import get_db

    with app.app_context():
        yield get_db()
