"""order blueprint: cart, checkout, order tracking."""

from flask import Blueprint

bp = Blueprint("order", __name__)

from app.blueprints.order import routes  # noqa: E402,F401
