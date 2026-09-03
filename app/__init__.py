"""Application factory.

Boot order matters: configuration is resolved first (production refuses
to start on a missing variable), then extensions, then blueprints, and
only then the route-marker check — which fails the boot if any endpoint
lacks an explicit auth marker (02-ARCHITECTURE.md).
"""

from __future__ import annotations

import logging

from flask import Flask, render_template
from pymongo import MongoClient

from app.config import BaseConfig, load_config
from app.db import client as db_client
from app.db.indexes import ensure_indexes
from app.extensions import csrf, login_manager
from app.security.decorators import assert_routes_marked

__all__ = ["create_app"]


def create_app(
    config: BaseConfig | str | None = None,
    *,
    mongo_client: MongoClient | None = None,
) -> Flask:
    """Build the application.

    `mongo_client` injects a driver (mongomock in tests) so the factory
    can be exercised end to end without a live database.
    """
    resolved = config if isinstance(config, BaseConfig) else load_config(config)

    from app.logging_config import configure_logging

    configure_logging(as_json=resolved.ENV == "production")

    app = Flask(__name__)
    app.config.from_object(resolved)
    app.config["BOOTSTRAP_INDEXES"] = getattr(resolved, "BOOTSTRAP_INDEXES", True)

    _init_extensions(app, mongo_client)
    _register_template_filters(app)
    _register_blueprints(app)
    _register_error_handlers(app)

    if app.config["BOOTSTRAP_INDEXES"]:
        with app.app_context():
            ensure_indexes(db_client.get_db())

    # Last: a route registered after this point would not be checked.
    assert_routes_marked(app)
    app.logger.info("application ready", extra={"env": resolved.ENV})
    return app


def _init_extensions(app: Flask, mongo_client: MongoClient | None) -> None:
    db_client.init_app(app, mongo_client)
    login_manager.init_app(app)
    csrf.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str):  # noqa: ANN202 — Flask-Login callback
        from app.db.repositories.users import get_user

        return get_user(user_id)


def _register_template_filters(app: Flask) -> None:
    """Presentation-only helpers. The rule they encode lives in a service."""
    from app.services.pricing import format_price_aud

    # Templates render money through the same formatter as everything
    # else, so integer minor units are never re-implemented in Jinja.
    app.jinja_env.filters["price"] = format_price_aud


def _register_blueprints(app: Flask) -> None:
    from app.blueprints.account import bp as account_bp
    from app.blueprints.api import bp as api_bp
    from app.blueprints.api.routes import issue_token
    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.chef import bp as chef_bp
    from app.blueprints.order import bp as order_bp
    from app.blueprints.public import bp as public_bp

    for blueprint in (public_bp, auth_bp, account_bp, order_bp, chef_bp, api_bp):
        app.register_blueprint(blueprint)

    # Credentials in the body, no session cookie: CSRF does not apply.
    csrf.exempt(issue_token)


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(403)
    def forbidden(_error):  # noqa: ANN202
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_error):  # noqa: ANN202
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(error):  # noqa: ANN202
        app.logger.error("unhandled error", exc_info=error)
        return render_template("errors/500.html"), 500

    logging.getLogger(__name__).debug("error handlers registered")
