"""chef blueprint: catalogue CRUD, order queue, allergen editor."""

from flask import Blueprint

bp = Blueprint("chef", __name__, url_prefix="/chef")

from app.blueprints.chef import routes  # noqa: E402,F401
