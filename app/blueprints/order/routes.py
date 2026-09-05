"""Cart routes.

Every mutation is a form POST that redirects, so the cart works with
JavaScript disabled (03-FRONTEND.md). `cart.js` posts the same intents to
`POST /api/cart` instead and updates the badge in place; it is an
enhancement over these routes, never a replacement for them.

The cart is a guest surface: there is no login yet, and 04-WORKFLOWS.md
has a guest cart merging into the user's on login rather than a login
standing between a customer and their cart. So these are public routes.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from flask import flash, redirect, render_template, request, url_for

from app.blueprints.order import bp
from app.security.decorators import public_route
from app.services import cart as cart_service


def _safe_return_to(raw: str | None) -> str | None:
    """A same-site path from the form, or None.

    The value decides where the customer is sent next, so it is treated
    as untrusted: only a path on this site is honoured, and anything
    carrying a scheme, a host or a backslash is discarded. Without this
    the field is an open redirect with a friendly name.

    The fragment travels in this hidden field rather than being read back
    from `Referer`, because a fragment never reaches the server: it is
    the only way the customer lands back at the control they used.
    """
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return None
    if "\\" in candidate or any(character in candidate for character in "\r\n\t"):
        return None
    parts = urlsplit(candidate)
    if parts.scheme or parts.netloc:
        return None
    return candidate


def _back(default_endpoint: str = "order.cart"):
    """Redirect to the page the form came from, else to the cart."""
    return redirect(_safe_return_to(request.form.get("return_to")) or url_for(
        default_endpoint
    ))


@bp.get("/cart")
@public_route
def cart() -> str:
    """The cart, resolved against the catalogue as it stands now."""
    return render_template("order/cart.html", view=cart_service.resolve_cart())


@bp.post("/cart/add")
@public_route
def add_to_cart():
    """Add one item to the cart, then return to the page it came from."""
    item_type = cart_service.parse_item_type(request.form.get("item_type"))
    item_id = (request.form.get("item_id") or "").strip()
    quantity = cart_service.parse_quantity(request.form.get("quantity"), default=1)

    if item_type is None or not item_id or not quantity:
        # A mutation that quietly does nothing is worse than one that
        # says so: the customer would believe the item was added.
        flash("That could not be added to your cart.", "error")
        return _back()

    item = cart_service.find_orderable_item(item_type, item_id)
    if item is None:
        flash("That item is no longer available.", "error")
        return _back()

    try:
        updated = cart_service.add_line(
            cart_service.load_cart(), item_type, item_id, quantity
        )
    except cart_service.CartFullError:
        flash(
            "Your cart already holds "
            f"{cart_service.MAX_LINES} different items.",
            "error",
        )
        return _back()

    cart_service.save_cart(updated)
    flash(f"{item.name} added to your cart.", "success")
    return _back()


@bp.post("/cart/update")
@public_route
def update_cart():
    """Set a line's quantity. Zero removes it, which is the customer's call."""
    item_type = cart_service.parse_item_type(request.form.get("item_type"))
    item_id = (request.form.get("item_id") or "").strip()
    quantity = cart_service.parse_quantity(request.form.get("quantity"))

    if item_type is None or not item_id or quantity is None:
        flash("That quantity could not be applied.", "error")
        return _back()

    cart_service.save_cart(
        cart_service.set_quantity(
            cart_service.load_cart(), item_type, item_id, quantity
        )
    )
    return _back()


@bp.post("/cart/remove")
@public_route
def remove_from_cart():
    """Remove a line. The only way an item leaves a cart on its own."""
    item_type = cart_service.parse_item_type(request.form.get("item_type"))
    item_id = (request.form.get("item_id") or "").strip()

    if item_type is None or not item_id:
        flash("That item could not be removed.", "error")
        return _back()

    cart_service.save_cart(
        cart_service.remove_line(cart_service.load_cart(), item_type, item_id)
    )
    return _back()
