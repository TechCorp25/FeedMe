"""public blueprint: landing, catalogue browse, item detail."""

from flask import Blueprint

bp = Blueprint("public", __name__)

from app.blueprints.public import routes  # noqa: E402,F401
