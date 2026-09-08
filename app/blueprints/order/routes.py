"""Cart, checkout and one placed order.

Every mutation is a form POST that redirects, so the cart works with
JavaScript disabled (03-FRONTEND.md). `cart.js` posts the same intents to
`POST /api/cart` instead and updates the badge in place; it is an
enhancement over these routes, never a replacement for them.

The cart itself is a public surface: 04-WORKFLOWS.md has a guest cart
merging into the customer's on sign-in rather than a login standing
between somebody and the food they are choosing. Checkout is where the
account becomes necessary — an order belongs to a `user_id` — so that is
the first route on this blueprint that requires one, and an unauthenticated
customer is sent to sign in and brought straight back.
"""

from __future__ import annotations

from datetime import timedelta

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.order import bp
from app.db.repositories import orders as orders_repo
from app.models.base import utcnow
from app.models.orders import Fulfilment
from app.security.decorators import login_required, public_route
from app.security.redirects import safe_path
from app.services import cart as cart_service
from app.services import checkout as checkout_service


def _back(default_endpoint: str = "order.cart"):
    """Redirect to the page the form came from, else to the cart.

    `return_to` is untrusted and is filtered by `safe_path`; the fragment
    travels in that hidden field rather than being read back from
    `Referer`, because a fragment never reaches the server and it is the
    only way the customer lands back at the control they used.
    """
    return redirect(
        safe_path(request.form.get("return_to")) or url_for(default_endpoint)
    )


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


# --- checkout ---------------------------------------------------------------


def _checkout_context(view, form=None) -> dict:
    """Everything the checkout form renders from, prefilled or re-filled."""
    today = utcnow().date()
    submitted = form or {}
    return {
        "view": view,
        "today": today.isoformat(),
        "latest": (
            today + timedelta(days=checkout_service.MAX_LEAD_DAYS)
        ).isoformat(),
        "fulfilments": list(Fulfilment),
        "requested_for": submitted.get("requested_for", ""),
        "fulfilment": submitted.get("fulfilment", Fulfilment.COLLECTION.value),
        "customer_note": submitted.get("customer_note", ""),
        "delivery_address": submitted.get(
            "delivery_address", current_user.delivery_address or ""
        ),
        "note_limit": checkout_service.MAX_NOTE_LENGTH,
    }


@bp.get("/checkout")
@login_required
def checkout():
    """Review the order, choose a date and how it is collected."""
    view = cart_service.resolve_cart()
    if view.is_empty:
        flash("Your cart is empty.", "error")
        return redirect(url_for("order.cart"))
    return render_template("order/checkout.html", **_checkout_context(view))


@bp.post("/checkout")
@login_required
def place_order():
    """Confirm. Prices and allergen declarations are frozen at this point.

    The cart is resolved once, here, and that same resolution is what is
    snapshotted: re-reading the catalogue between the check and the write
    would let an item change underneath the order.
    """
    view = cart_service.resolve_cart()
    try:
        checkout_request = checkout_service.parse_checkout_form(
            request.form, user=current_user, today=utcnow().date()
        )
        order = checkout_service.place_order(current_user, view, checkout_request)
    except checkout_service.CheckoutError as error:
        flash(str(error), "error")
        if view.is_empty or view.is_blocked:
            return redirect(url_for("order.cart"))
        return (
            render_template(
                "order/checkout.html", **_checkout_context(view, request.form)
            ),
            400,
        )

    # Only now: the order is written, so the cart has done its job.
    cart_service.clear_cart()
    flash(f"Order {order.reference} placed.", "success")
    return redirect(url_for("order.order_detail", reference=order.reference))


@bp.get("/orders/<reference>")
@login_required
def order_detail(reference: str) -> str:
    """One placed order, as the customer was shown it.

    Scoped by `user_id` in the repository, and a miss is 404 rather than
    403: a 403 would confirm that somebody else's order exists
    (02-ARCHITECTURE.md).
    """
    order = orders_repo.get_order_by_reference(current_user.get_id(), reference)
    if order is None:
        abort(404)
    return render_template("order/order_detail.html", order=order)
