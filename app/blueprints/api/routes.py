"""JSON API routes."""

from __future__ import annotations

from flask import request

from app.blueprints.api import bp
from app.db.repositories.users import get_user_by_email
from app.security.decorators import public_route
from app.security.passwords import verify_password
from app.security.tokens import issue_access_token
from app.services import cart as cart_service


@bp.post("/auth/token")
@public_route
def issue_token() -> tuple[dict, int]:
    """Access token for a future mobile client. The web app does not use it.

    CSRF-exempt because it authenticates with credentials in the request
    body, not with a session cookie (see the factory).
    """
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", ""))
    password = str(payload.get("password", ""))

    user = get_user_by_email(email) if email else None
    if user is None or not user.is_active or not verify_password(
        user.password_hash, password
    ):
        # One message for every failure mode: never reveal which part failed.
        return {"error": "invalid_credentials"}, 401

    if user.id is None:  # unsaved user: cannot happen for a stored record
        return {"error": "invalid_credentials"}, 401

    return {
        "access_token": issue_access_token(user.id, user.role),
        "token_type": "Bearer",
    }, 200


def _cart_payload() -> dict:
    """The whole cart, as the page needs to redraw it.

    Every price is computed here rather than by the caller: the client
    never does money arithmetic, and a JSON client sees the same integer
    minor units the server rendered (01-DOMAIN.md).
    """
    view = cart_service.resolve_cart()
    return {
        "item_count": view.item_count,
        "subtotal_cents": view.subtotal_cents,
        "blocked": view.is_blocked,
        "lines": [
            {
                "item_type": entry.item_type.value,
                "item_id": entry.item_id,
                "name": entry.name,
                "quantity": entry.quantity,
                "unit_price_cents": entry.unit_price_cents,
                "line_total_cents": entry.line_total_cents,
                "is_available": entry.is_available,
            }
            for entry in view.entries
        ],
    }


@bp.post("/cart")
@public_route
def mutate_cart() -> tuple[dict, int]:
    """Cart mutation for `cart.js`.

    The same three intents as the form routes, and the same rules: an
    unpublished item cannot be added, an unrecognised request is refused
    rather than quietly ignored, and the cart is never silently pruned.
    The form routes remain the fallback, so nothing here is the only way
    to reach the cart.

    Not CSRF-exempt: this is a session-cookie surface, so `cart.js` sends
    the token from the meta tag as `X-CSRFToken` (03-FRONTEND.md).
    """
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action", "")).strip()
    item_type = cart_service.parse_item_type(payload.get("item_type"))
    item_id = str(payload.get("item_id", "")).strip()

    if action not in {"add", "set", "remove"} or item_type is None or not item_id:
        return {"error": "invalid_request"}, 400

    cart = cart_service.load_cart()

    if action == "remove":
        cart_service.save_cart(
            cart_service.remove_line(cart, item_type, item_id)
        )
        return _cart_payload(), 200

    default = 1 if action == "add" else None
    quantity = cart_service.parse_quantity(payload.get("quantity"), default=default)
    if quantity is None or (action == "add" and quantity < 1):
        return {"error": "invalid_quantity"}, 400

    if action == "add":
        if cart_service.find_orderable_item(item_type, item_id) is None:
            return {"error": "unavailable"}, 404
        try:
            cart = cart_service.add_line(cart, item_type, item_id, quantity)
        except cart_service.CartFullError:
            return {"error": "cart_full", "max_lines": cart_service.MAX_LINES}, 409
    else:
        cart = cart_service.set_quantity(cart, item_type, item_id, quantity)

    cart_service.save_cart(cart)
    return _cart_payload(), 200
