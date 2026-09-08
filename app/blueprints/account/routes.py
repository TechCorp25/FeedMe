"""The customer's own account area.

Every route here is `@login_required` and every read is scoped by
`user_id` inside the repository. A reference that names another
customer's order is a 404, never a 403: a 403 would confirm the order
exists (02-ARCHITECTURE.md).

Cancellation is a form POST like every other mutation in this
application, so it works with JavaScript disabled, and it carries the
CSRF token every state-changing form here carries.
"""

from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.account import bp
from app.security.decorators import login_required
from app.services import account as account_service


#: Maximum lengths, handed to the template so the `maxlength` on each
#: control is the same bound the service enforces. A form that lets a
#: customer type past a limit it is going to refuse is a form that wastes
#: their time.
FIELD_LIMITS = {
    "display_name": account_service.MAX_DISPLAY_NAME_LENGTH,
    "phone": account_service.MAX_PHONE_LENGTH,
    "delivery_address": account_service.MAX_ADDRESS_LENGTH,
    "dietary_notes": account_service.MAX_DIETARY_NOTES_LENGTH,
}


def _profile_context(submitted=None) -> dict:
    """What the profile form renders from, prefilled or re-filled.

    `submitted` is the rejected form, so a refusal re-renders what the
    customer typed rather than what is stored.
    """
    offered = account_service.offered_preference_flags()
    stored = {
        "display_name": current_user.display_name,
        "phone": current_user.phone or "",
        "delivery_address": current_user.delivery_address or "",
        "dietary_notes": current_user.dietary_notes or "",
    }
    values = {
        field: (submitted.get(field, "") if submitted is not None else stored[field])
        for field in stored
    }
    choices = account_service.preference_choices(current_user, offered)
    if submitted is not None:
        chosen = set(submitted.getlist("preference"))
        choices = [
            account_service.PreferenceChoice(
                value=choice.value,
                label=choice.label,
                selected=choice.value in chosen,
            )
            for choice in choices
        ]
    return {
        "values": values,
        "preference_choices": choices,
        "limits": FIELD_LIMITS,
    }



@bp.get("/")
@login_required
def profile() -> str:
    """Name, phone, delivery address, dietary notes and default filters."""
    return render_template("account/profile.html", **_profile_context())


@bp.post("/")
@login_required
def save_profile():
    """Save the profile, or re-render the form with what was typed.

    The form is re-rendered rather than redirected on a refusal, so
    nothing the customer wrote is lost to a round trip.
    """
    offered = account_service.offered_preference_flags()
    try:
        update = account_service.parse_profile_form(request.form, offered=offered)
        account_service.save_profile(current_user, update)
    except account_service.ProfileError as error:
        flash(str(error), "error")
        return (
            render_template(
                "account/profile.html", **_profile_context(request.form)
            ),
            400,
        )

    flash("Your details are saved.", "success")
    return redirect(url_for("account.profile"))


@bp.get("/orders")
@login_required
def orders() -> str:
    """Order history, newest first."""
    return render_template(
        "account/orders.html",
        orders=account_service.list_orders(current_user.get_id()),
    )


@bp.get("/orders/<reference>")
@login_required
def order_detail(reference: str) -> str:
    """One order, as the customer was shown it when they placed it."""
    order = account_service.get_order(current_user.get_id(), reference)
    if order is None:
        abort(404)
    return render_template(
        "account/order_detail.html",
        order=order,
        can_cancel=account_service.can_customer_cancel(order),
    )


@bp.post("/orders/<reference>/cancel")
@login_required
def cancel_order(reference: str):
    """Cancel from `placed` or `confirmed`. Beyond that, the chef only.

    The order is read inside this request and the write is conditional on
    the status that read returned, so a second submission of the same
    form cancels nothing and credits nothing a second time.
    """
    order = account_service.get_order(current_user.get_id(), reference)
    if order is None:
        abort(404)

    try:
        account_service.cancel_order(current_user, order)
    except account_service.CancellationError as error:
        flash(str(error), "error")
        return redirect(url_for("account.order_detail", reference=reference))

    flash(f"Order {reference} is cancelled.", "success")
    return redirect(url_for("account.order_detail", reference=reference))


@bp.get("/balance")
@login_required
def balance() -> str:
    """Ledger entries and the running balance, summed by the database."""
    return render_template(
        "account/balance.html",
        view=account_service.balance_view(current_user.get_id()),
    )
