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

import secrets
from datetime import timedelta

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user

from app.blueprints.order import bp
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


#: Named by the checkout service, which is also what clears them at
#: sign-out. Both live in the session because that is where this flow
#: already keeps state; 01-DOMAIN.md names six collections and a pending
#: checkout is not one of them.
CHECKOUT_TOKEN_KEY = checkout_service.CHECKOUT_TOKEN_KEY
LAST_ORDER_KEY = checkout_service.LAST_ORDER_KEY


def _checkout_token() -> str:
    """The token this customer's open checkout form carries.

    Issued when the form is rendered and consumed when an order is
    written, so the same form cannot be posted twice: a double-clicked
    Place order, or a browser retrying the POST after the first response,
    would otherwise write two orders and two ledger charges for one
    confirmation.

    What this does not cover is two requests that reach the server before
    either has replied — they carry the same cookie, so both see the same
    unspent token, and the session is the only store this slice has. A
    guard against that has to be a durable one (an idempotency key on the
    order, refused by a unique index), and that changes the order
    document, which 01-DOMAIN.md owns. The common case — a second click
    after the first response — is closed here.
    """
    token = session.get(CHECKOUT_TOKEN_KEY)
    if not token:
        token = secrets.token_urlsafe(16)
        session[CHECKOUT_TOKEN_KEY] = token
    return token


def _checkout_context(view, form=None) -> dict:
    """Everything the checkout form renders from, prefilled or re-filled."""
    today = checkout_service.business_today(
        current_app.config["BUSINESS_TIMEZONE"]
    )
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
        "checkout_token": _checkout_token(),
        # Recomputed on every render, never echoed back from the form:
        # it has to describe what this page is about to show.
        "review_digest": checkout_service.review_digest(view),
    }


def _cart_is_not_ready(view) -> bool:
    """True when the cart cannot be checked out as it stands.

    Checked on the way in as well as on the way out. A customer reaching
    `/checkout` from a bookmark or a tab left open since an item was
    withdrawn would otherwise be shown a working Place order button for a
    cart the POST is going to refuse — and, for an item deleted outright,
    a page that has no item to render at all.
    """
    return view.is_empty or view.is_blocked


@bp.get("/checkout")
@login_required
def checkout():
    """Review the order, choose a date and how it is collected."""
    view = cart_service.resolve_cart()
    if _cart_is_not_ready(view):
        flash(
            "Your cart is empty."
            if view.is_empty
            else "Remove the items that are no longer available to continue.",
            "error",
        )
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
    token_is_good = request.form.get("checkout_token", "") == session.get(
        CHECKOUT_TOKEN_KEY
    )

    if not token_is_good and session.get(LAST_ORDER_KEY):
        # This form has already been confirmed — a second click, or a
        # retried POST. The order it wrote is what the customer wants to
        # see, not a second one.
        reference = session[LAST_ORDER_KEY]
        flash(f"Order {reference} is already placed.", "success")
        return redirect(url_for("account.order_detail", reference=reference))

    if _cart_is_not_ready(view):
        flash(
            "Your cart is empty."
            if view.is_empty
            else "Remove the items that are no longer available to continue.",
            "error",
        )
        return redirect(url_for("order.cart"))

    if not token_is_good:
        # A form older than this session, or one already spent without an
        # order to show for it. Nothing is written on a form the server
        # did not issue.
        flash("Please review your order and confirm again.", "error")
        return redirect(url_for("order.checkout"))

    if request.form.get("review_digest", "") != checkout_service.review_digest(view):
        # Something moved while the page was open — a quantity changed in
        # another tab, an item added, a price edited. The customer agreed
        # to what they were shown, so they are shown it again rather than
        # charged for an order they never reviewed.
        flash(
            "Your cart changed while this page was open. Please check the "
            "order below and confirm again.",
            "error",
        )
        return (
            render_template(
                "order/checkout.html", **_checkout_context(view, request.form)
            ),
            409,
        )

    try:
        checkout_request = checkout_service.parse_checkout_form(
            request.form,
            user=current_user,
            today=checkout_service.business_today(
                current_app.config["BUSINESS_TIMEZONE"]
            ),
        )
        order = checkout_service.place_order(current_user, view, checkout_request)
    except checkout_service.CheckoutError as error:
        flash(str(error), "error")
        return (
            render_template(
                "order/checkout.html", **_checkout_context(view, request.form)
            ),
            400,
        )
    except checkout_service.ReferenceSpaceExhausted:
        current_app.logger.error(
            "reference space exhausted for the month; no order was written"
        )
        flash(
            "Your order could not be placed. Please contact the kitchen.",
            "error",
        )
        return (
            render_template(
                "order/checkout.html", **_checkout_context(view, request.form)
            ),
            503,
        )

    # Only now: the order is written, so the cart has done its job and
    # the token that authorised this confirmation is spent.
    cart_service.clear_cart()
    session.pop(CHECKOUT_TOKEN_KEY, None)
    session[LAST_ORDER_KEY] = order.reference
    flash(f"Order {order.reference} placed.", "success")
    return redirect(url_for("account.order_detail", reference=order.reference))


@bp.get("/orders/<reference>")
@login_required
def order_detail(reference: str):
    """The order this checkout produced, at its home in the account area.

    One order, one URL. 04-WORKFLOWS.md puts order history and order
    detail under `/account/orders`, and two pages rendering one order
    would drift the moment either gained a control the other lacked —
    the cancel button being the immediate example.

    This path is kept because it is what checkout redirected to before
    the account area existed, so a link a customer already has still
    lands on their order. It resolves nothing itself: the account route
    does the lookup, and an order that is not this customer's is a 404
    there, exactly as it was here.
    """
    return redirect(
        url_for("account.order_detail", reference=reference), code=301
    )
