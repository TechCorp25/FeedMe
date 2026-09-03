"""api blueprint: JSON endpoints only.

Scope is deliberately narrow — cart mutation and order-status polling
(00-SYSTEM.md) — plus token issuance for a future mobile client.
"""

from flask import Blueprint

bp = Blueprint("api", __name__, url_prefix="/api")

from app.blueprints.api import routes  # noqa: E402,F401
