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
from app.services import (
    allergen_editor,
    catalogue_admin,
    chef_credentials,
    chef_ledger,
    chef_orders,
    meal_type_admin,
    prep_sheet,
)
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


# --- meal types -------------------------------------------------------------
#
# `/menu/<meal_type_slug>` is one of the three ordering entry points
# (04-WORKFLOWS.md) and the dish editor offers meal types as checkboxes.
# Neither worked before this page existed, because nothing wrote to the
# collection: `meal_types` had two read functions and no way in.
#
# Same shape as the catalogue editors above — a list that is also the
# create form, a rename, a reorder — with one difference. There is no
# archive here. A meal type is deleted outright, and deletion is refused
# while a dish still references it, so `/menu` can never resolve to a
# label a dish points at and nothing renders under. `meal_type_admin`
# carries the reasoning.


def _meal_type_or_404(meal_type_id: str):
    meal_type = meal_type_admin.get_meal_type(meal_type_id)
    if meal_type is None:
        abort(404)
    return meal_type


@bp.get("/meal-types")
@chef_required
def meal_types() -> str:
    """Every meal type, and the form that creates one."""
    return render_template(
        "chef/meal_types.html", listing=meal_type_admin.listing()
    )


@bp.get("/meal-types/<meal_type_id>/edit")
@chef_required
def edit_meal_type(meal_type_id: str) -> str:
    """Rename one meal type, on its own page.

    Its own page rather than an inline field on the list: a rename is the
    one action here that can break a saved `/menu` link, and the page is
    where that is explained beside the field that does it.
    """
    return render_template(
        "chef/meal_type_form.html", meal_type=_meal_type_or_404(meal_type_id)
    )


@bp.post("/meal-types/save")
@bp.post("/meal-types/<meal_type_id>/save")
@chef_required
def save_meal_type(meal_type_id: str | None = None):
    """Create or rename. A refusal re-renders, so nothing typed is lost."""
    existing = _meal_type_or_404(meal_type_id) if meal_type_id else None

    try:
        meal_type = meal_type_admin.save(meal_type_id, request.form)
    except meal_type_admin.MealTypeFormError as error:
        flash(str(error), "error")
        if existing is None:
            return (
                render_template(
                    "chef/meal_types.html",
                    listing=meal_type_admin.listing(),
                    submitted=request.form,
                ),
                400,
            )
        return (
            render_template(
                "chef/meal_type_form.html",
                meal_type=existing,
                submitted=request.form,
            ),
            400,
        )

    flash(f"{meal_type.name} is saved.", "success")
    return redirect(url_for("chef.meal_types"))


@bp.post("/meal-types/<meal_type_id>/delete")
@chef_required
def delete_meal_type(meal_type_id: str):
    """Remove one meal type, unless a dish still points at it."""
    meal_type = _meal_type_or_404(meal_type_id)

    try:
        meal_type_admin.delete(meal_type)
    except meal_type_admin.MealTypeFormError as error:
        flash(str(error), "error")
        return redirect(url_for("chef.meal_types"))

    flash(f"{meal_type.name} is deleted.", "success")
    return redirect(url_for("chef.meal_types"))


@bp.post("/meal-types/<meal_type_id>/move")
@chef_required
def move_meal_type(meal_type_id: str):
    """Reorder by swapping with a neighbour. The order is the menu's."""
    meal_type = _meal_type_or_404(meal_type_id)

    try:
        meal_type_admin.move(meal_type, request.form.get("direction", ""))
    except meal_type_admin.MealTypeFormError as error:
        flash(str(error), "error")

    return redirect(url_for("chef.meal_types"))


# --- the allergen editor ----------------------------------------------------
#
# A deliberately separate step, not a section of the catalogue form
# (04-WORKFLOWS.md). It is reached from the item editor, it is the only
# code path that writes an `AllergenBlock`, and saving it *is* the
# review — there is no save that does not stamp `reviewed_at` and
# `reviewed_by`.


@bp.get("/<plural>/<item_id>/allergens")
@chef_required
def allergens(plural: str, item_id: str) -> str:
    """The declaration, and the ingredients it is a declaration about."""
    kind = _kind_or_404(plural)
    item = _item_or_404(kind, item_id)
    return render_template(
        "chef/allergens.html", **allergen_editor.form_context(kind, item)
    )


@bp.post("/<plural>/<item_id>/allergens")
@chef_required
def save_allergens(plural: str, item_id: str):
    """Record the reviewed declaration.

    A refusal re-renders rather than redirects. The chef has just read an
    ingredients list against a set of checkboxes, and throwing that away
    over a missing confirmation is how a compliance surface teaches
    somebody to tick everything and try again.
    """
    kind = _kind_or_404(plural)
    item = _item_or_404(kind, item_id)

    try:
        allergen_editor.save_review(kind, item, current_user, request.form)
    except allergen_editor.AllergenReviewError as error:
        flash(str(error), "error")
        context = allergen_editor.form_context(kind, item)
        context["submitted"] = request.form
        return render_template("chef/allergens.html", **context), 400

    flash(
        f"The allergen declaration for {item.name} is reviewed and saved.",
        "success",
    )
    return redirect(
        url_for("chef.allergens", plural=plural, item_id=item_id)
    )


# --- one customer's ledger --------------------------------------------------
#
# Append-only (04-WORKFLOWS.md), so there is no edit route and no delete
# route here. Not a disabled control, not a route that refuses — no such
# route exists. A correction is a new offsetting entry.


def _customer_or_404(user_id: str):
    customer = chef_ledger.get_customer(user_id)
    if customer is None:
        abort(404)
    return customer


def _ledger_context(customer, submitted=None, issued_password=None) -> dict:
    """What the ledger page renders from, with or without a rejected form.

    `issued_password` is the one-shot plaintext from a password reset. It
    reaches the template through the context of the response that set it
    and by no other route — deliberately not a flash, which outlives its
    redirect and has already once in this codebase been rendered to
    whoever signed in next on the same browser.
    """
    context = {
        "ledger": chef_ledger.customer_ledger(customer),
        "entry_types": [
            (entry_type.value, chef_ledger.ENTRY_TYPE_LABELS[entry_type])
            for entry_type in chef_ledger.MANUAL_ENTRY_TYPES
        ],
        "directions": chef_ledger.DIRECTION_LABELS,
        "max_description": chef_ledger.MAX_DESCRIPTION,
    }
    if submitted is not None:
        context["submitted"] = submitted
    if issued_password is not None:
        context["issued_password"] = issued_password
    return context


@bp.get("/customers")
@chef_required
def customers() -> str:
    """Every customer, and the way in to each one's ledger.

    04-WORKFLOWS.md gives the ledger a URL and nothing to reach it from.
    The order queue links to it, but only for a customer with an order
    outstanding — and a customer who settled last month still has a
    ledger to read and a credit that may need writing.
    """
    return render_template(
        "chef/customers.html",
        directory=chef_ledger.customer_directory(
            chef_ledger.parse_page(request.args.get("page"))
        ),
    )


@bp.get("/customers/<user_id>/ledger")
@chef_required
def customer_ledger(user_id: str) -> str:
    """One customer's entries and their running balance."""
    customer = _customer_or_404(user_id)
    return render_template("chef/ledger.html", **_ledger_context(customer))


@bp.post("/customers/<user_id>/password")
@chef_required
def set_customer_password(user_id: str):
    """Set a new password on one customer and show it once.

    04-WORKFLOWS.md keeps notifications out of v1, so there is no channel
    to send a reset token over — the chef reads the new password out over
    the phone, which is the channel the kitchen has. `chef_credentials`
    carries the reasoning.

    This answers **200 with the page**, not a redirect. The password has
    to survive exactly one response and no longer, and every way of
    carrying a value across a redirect — a flash, the session, a query
    string — keeps it somewhere it can be read again.
    """
    customer = _customer_or_404(user_id)

    try:
        password = chef_credentials.set_password(current_user, customer)
    except chef_credentials.PasswordResetError as error:
        flash(str(error), "error")
        return (
            render_template("chef/ledger.html", **_ledger_context(customer)),
            400,
        )

    return render_template(
        "chef/ledger.html",
        **_ledger_context(customer, issued_password=password),
    )


@bp.post("/customers/<user_id>/ledger")
@chef_required
def add_ledger_entry(user_id: str):
    """Append one hand-entered credit or adjustment."""
    customer = _customer_or_404(user_id)

    try:
        entry = chef_ledger.append_manual_entry(
            current_user,
            customer,
            entry_type=request.form.get("entry_type"),
            amount=request.form.get("amount"),
            direction=request.form.get("direction"),
            description=request.form.get("description"),
        )
    except chef_ledger.LedgerEntryError as error:
        # Re-rendered rather than redirected, so the amount and the
        # description survive a refusal.
        flash(str(error), "error")
        return (
            render_template(
                "chef/ledger.html", **_ledger_context(customer, request.form)
            ),
            400,
        )

    flash(
        f"{chef_ledger.ENTRY_TYPE_LABELS[entry.entry_type]} recorded against "
        f"{customer.display_name or customer.email}.",
        "success",
    )
    return redirect(url_for("chef.customer_ledger", user_id=user_id))
