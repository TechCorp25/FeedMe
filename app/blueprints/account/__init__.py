"""account blueprint: profile, order history, ledger.

Routes land with the account flow; the blueprint is registered now so the
factory and the route-marker check are wired.
"""

from flask import Blueprint

bp = Blueprint("account", __name__, url_prefix="/account")

from app.blueprints.account import routes  # noqa: E402,F401
