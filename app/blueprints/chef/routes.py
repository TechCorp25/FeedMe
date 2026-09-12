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
from app.services import catalogue_admin, chef_orders, prep_sheet
from app.services.dates import business_today


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


@bp.get("/prep")
@chef_required
def prep_today():
    """Today's sheet, or the day a date form asked for.

    Two jobs, deliberately one endpoint. The nav links here without a
    date, so "today" is resolved at the moment the chef follows the link
    rather than baked into a cached page. And the date picker on the
    sheet is a plain GET form, which can only submit a query string —
    this turns `?on=` into the path segment that makes a sheet linkable
    and printable with its date in the URL. No JavaScript either way.
    """
    asked = request.args.get("on")
    chosen = prep_sheet.parse_sheet_date(asked) if asked else None
    return redirect(
        url_for("chef.prep", on=(chosen or business_today()).isoformat())
    )


@bp.get("/prep/<on>")
@chef_required
def prep(on: str) -> str:
    """One day's pick list, rolled up across dishes and components."""
    sheet_date = prep_sheet.parse_sheet_date(on)
    if sheet_date is None:
        # Not a date is not a page. A 404 rather than a redirect to today,
        # because silently showing a different day than the URL names is
        # how somebody preps the wrong date.
        abort(404)
    return render_template(
        "chef/prep.html",
        sheet=prep_sheet.build_sheet(sheet_date),
        today=business_today(),
    )


# --- catalogue editors ------------------------------------------------------
#
# Both catalogues share one set of views. `components` and `dishes` are
# separate catalogues with separate pages and separate ordering flows
# (01-DOMAIN.md), and they stay separate in the URL and on the screen —
# but the form machinery is the same shape, and writing it twice is how
# the two drift into behaving differently.


def _kind_or_404(plural: str) -> str:
    """The catalogue a URL segment names, or 404.

    The segment is the plural the customer-facing routes already use
    (`/components`, `/dishes`), and it is mapped rather than derived:
    "dish" + "s" is "dishs".
    """
    kind = catalogue_admin.KIND_BY_PLURAL.get(plural)
    if kind is None:
        abort(404)
    return kind


def _item_or_404(kind: str, item_id: str):
    item = catalogue_admin.get_item(kind, item_id)
    if item is None:
        abort(404)
    return item


def _list_redirect(kind: str):
    """Back to the list, still showing archived items if it was."""
    arguments = {}
    if request.form.get("include_archived"):
        arguments["archived"] = "1"
    return redirect(url_for(f"chef.{catalogue_admin.PLURAL[kind]}", **arguments))


@bp.get("/components")
@chef_required
def components() -> str:
    return _render_admin_list(catalogue_admin.COMPONENT)


@bp.get("/dishes")
@chef_required
def dishes() -> str:
    return _render_admin_list(catalogue_admin.DISH)


def _render_admin_list(kind: str) -> str:
    include_archived = bool(request.args.get("archived"))
    return render_template(
        "chef/catalogue_list.html",
        listing=catalogue_admin.admin_list(kind, include_archived=include_archived),
    )


@bp.get("/<plural>/new")
@chef_required
def new_item(plural: str) -> str:
    kind = _kind_or_404(plural)
    return render_template(
        "chef/catalogue_form.html", **catalogue_admin.form_context(kind, None)
    )


@bp.get("/<plural>/<item_id>/edit")
@chef_required
def edit_item(plural: str, item_id: str) -> str:
    kind = _kind_or_404(plural)
    item = _item_or_404(kind, item_id)
    return render_template(
        "chef/catalogue_form.html", **catalogue_admin.form_context(kind, item)
    )


@bp.post("/<plural>/save")
@bp.post("/<plural>/<item_id>/save")
@chef_required
def save_item(plural: str, item_id: str | None = None):
    """Create or update. A refusal re-renders the form, never a redirect.

    Redirecting on a refusal would throw away everything the chef typed —
    and this form is long enough that losing it once is losing an
    afternoon.
    """
    kind = _kind_or_404(plural)
    existing = _item_or_404(kind, item_id) if item_id else None

    try:
        item = catalogue_admin.save_item(kind, item_id, request.form)
    except catalogue_admin.ItemFormError as error:
        flash(str(error), "error")
        context = catalogue_admin.form_context(kind, existing)
        context["submitted"] = request.form
        return render_template("chef/catalogue_form.html", **context), 400

    flash(f"{item.name} is saved.", "success")
    return redirect(url_for("chef.edit_item", plural=plural, item_id=item.id))


@bp.post("/<plural>/<item_id>/availability")
@chef_required
def set_availability(plural: str, item_id: str):
    """Toggle availability. Publication is gated on the allergen review."""
    kind = _kind_or_404(plural)
    item = _item_or_404(kind, item_id)
    wanted = bool(request.form.get("available"))

    try:
        catalogue_admin.set_availability(kind, item, wanted)
    except catalogue_admin.ItemFormError as error:
        flash(str(error), "error")
        return _list_redirect(kind)

    flash(
        f"{item.name} is {'available to customers' if wanted else 'no longer available'}.",
        "success",
    )
    return _list_redirect(kind)


@bp.post("/<plural>/<item_id>/archive")
@chef_required
def set_archived(plural: str, item_id: str):
    """Archive or restore. Archived items never appear to customers."""
    kind = _kind_or_404(plural)
    item = _item_or_404(kind, item_id)
    wanted = bool(request.form.get("archive"))

    try:
        catalogue_admin.set_archived(kind, item, wanted)
    except catalogue_admin.ItemFormError as error:
        flash(str(error), "error")
        return _list_redirect(kind)

    flash(
        f"{item.name} is {'archived' if wanted else 'restored as a draft'}.",
        "success",
    )
    return _list_redirect(kind)


@bp.post("/<plural>/<item_id>/move")
@chef_required
def move_item(plural: str, item_id: str):
    """Reorder by swapping with a neighbour."""
    kind = _kind_or_404(plural)
    item = _item_or_404(kind, item_id)

    try:
        catalogue_admin.move(kind, item, request.form.get("direction", ""))
    except catalogue_admin.ItemFormError as error:
        flash(str(error), "error")

    return _list_redirect(kind)
