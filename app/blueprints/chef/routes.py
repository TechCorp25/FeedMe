"""The chef-admin area. Every route here carries `@chef_required`.

`chef_required` answers an authenticated customer with 404 rather than
403: a 403 would confirm that the route exists (02-ARCHITECTURE.md).

Every mutation is a form POST that redirects, so the whole area works
with JavaScript disabled — the same rule the customer pages keep, and it
matters more here, because the chef is the one person who cannot fall
back to asking somebody else to do it.
"""

from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.chef import bp
from app.db.repositories import orders as orders_repo
from app.models.orders import OrderStatus
from app.security.decorators import chef_required
from app.services import chef_orders


def _queue_redirect():
    """Back to the queue, carrying whatever it was filtered by.

    A transition applied from a filtered queue returns to that queue. The
    filter is read back off the submitted form rather than remembered in
    the session: the page the chef posted from is the page they expect to
    land on, and a session would make two open tabs fight over it.
    """
    arguments = {
        key: value
        for key, value in (
            ("status", request.form.get("filter_status")),
            ("requested_for", request.form.get("filter_requested_for")),
        )
        if value
    }
    return redirect(url_for("chef.orders", **arguments))


@bp.get("/")
@chef_required
def index():
    """The chef's landing page is the work, not a dashboard."""
    return redirect(url_for("chef.orders"))


@bp.get("/orders")
@chef_required
def orders() -> str:
    """The order queue: what is outstanding, oldest requested date first."""
    filters = chef_orders.parse_filters(
        request.args.get("status"), request.args.get("requested_for")
    )
    return render_template(
        "chef/orders.html",
        view=chef_orders.queue_view(filters),
        # Every status, `placed` included: the filter strip is how the
        # chef looks at finished work too, and a control that cannot ask
        # for `collected` is a filter with a hole in it.
        statuses=list(OrderStatus),
        payment_statuses=chef_orders.PAYMENT_STATUS_LABELS,
    )


@bp.post("/orders/<order_id>/transition")
@chef_required
def transition(order_id: str):
    """Move one order along the allowed map.

    The order is read here and the write is conditional on the status
    this read returned, so the same button pressed twice moves it once.
    """
    order = orders_repo.chef_get_order(order_id)
    if order is None:
        abort(404)

    try:
        moved = chef_orders.apply_transition(
            current_user,
            order,
            request.form.get("target"),
            request.form.get("chef_note"),
        )
    except chef_orders.QueueActionError as error:
        flash(str(error), "error")
        return _queue_redirect()

    flash(
        f"Order {moved.reference} is now {moved.status.value}.",
        "success",
    )
    return _queue_redirect()


@bp.post("/orders/<order_id>/payment")
@chef_required
def payment(order_id: str):
    """Set `payment_status` by hand. No provider is contacted."""
    order = orders_repo.chef_get_order(order_id)
    if order is None:
        abort(404)

    try:
        status = chef_orders.set_payment_status(
            order, request.form.get("payment_status")
        )
    except chef_orders.QueueActionError as error:
        flash(str(error), "error")
        return _queue_redirect()

    flash(
        f"Order {order.reference} is marked {status.value}.",
        "success",
    )
    return _queue_redirect()
