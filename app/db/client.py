"""MongoClient lifecycle.

One client per process, created lazily on first use and stored on the
Flask application so tests can inject a substitute (mongomock) without
touching module-level state.
"""

from __future__ import annotations

from typing import Any

from flask import Flask, current_app
from pymongo import MongoClient
from pymongo.database import Database

_CLIENT_KEY = "feedme_mongo_client"


def init_app(app: Flask, client: MongoClient | None = None) -> None:
    """Attach a client to `app`. Pass `client` to inject one in tests.

    The driver pools connections itself, so there is nothing to tear down
    per request.
    """
    app.extensions[_CLIENT_KEY] = client


def get_client(app: Flask | None = None) -> MongoClient:
    app = app or current_app
    client = app.extensions.get(_CLIENT_KEY)
    if client is None:
        client = MongoClient(
            app.config["MONGO_URI"],
            tz_aware=True,
            serverSelectionTimeoutMS=app.config["MONGO_SERVER_SELECTION_TIMEOUT_MS"],
        )
        app.extensions[_CLIENT_KEY] = client
    return client


def get_db(app: Flask | None = None) -> Database[dict[str, Any]]:
    app = app or current_app
    return get_client(app)[app.config["MONGO_DB_NAME"]]
