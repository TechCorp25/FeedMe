"""JSON API routes."""

from __future__ import annotations

from flask import request
from flask_login import current_user

from app.blueprints.api import bp
from app.security.decorators import login_required, public_route
from app.security.tokens import issue_access_token
from app.services import account as account_service
from app.services import accounts
from app.services import cart as cart_service


@bp.post("/auth/token")
@public_route
def issue_token() -> tuple[dict, int]:
    """Access token for a future mobile client. The web app does not use it.

    CSRF-exempt because it authenticates with credentials in the request
    body, not with a session cookie (see the factory).

    Credentials are checked by `accounts.authenticate` — the same call
    the sign-in form makes. Two surfaces authenticating the same
    customers against the same collection must not drift: checking the
    password here as well would leave a rule added to one silently
    missing from the other, as the length bound, the Argon2 rehash and
    the `last_login_at` stamp were (02-ARCHITECTURE.md).
    """
    payload = request.get_json(silent=True) or {}

    user = accounts.authenticate(payload.get("email"), payload.get("password"))
    if user is None or user.id is None:
        # One message for every failure mode: never reveal which part failed.
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


@bp.get("/orders/<reference>/status")
@login_required
def order_status(reference: str) -> tuple[dict, int]:
    """One of the customer's own orders, as a status the page can poll.

    The second of the two JSON surfaces 00-SYSTEM.md allows, and it is an
    enhancement: `/account/orders` renders every status server-side on
    first request, so a customer without JavaScript sees the same thing
    one refresh later.

    Scoped by `user_id` in the repository like every other read of an
    order, and a reference that is not this customer's is 404 — the same
    answer the HTML page gives, for the same reason.
    """
    order = account_service.get_order(current_user.get_id(), reference)
    if order is None:
        return {"error": "not_found"}, 404
    return {
        "reference": order.reference,
        "status": order.status.value,
        "status_label": order.status.value.capitalize(),
        # What the poller needs to know to stop asking. A terminal order
        # never changes again, so a page that keeps polling one is asking
        # a question that is already answered.
        "is_terminal": order.is_terminal,
        "can_cancel": account_service.can_customer_cancel(order),
    }, 200
