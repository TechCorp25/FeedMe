"""Registration, sign-in and sign-out.

Sessions, not tokens: the web application authenticates with a
Flask-Login session cookie, and `api/auth/token` serves a future mobile
client against the same collection (02-ARCHITECTURE.md).

Every form here posts and redirects. Nothing on this surface depends on
JavaScript, and the CSRF token is on every one of them.
"""

from __future__ import annotations

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user

from app.blueprints.auth import bp
from app.models.users import User
from app.security.decorators import login_required, public_route
from app.security.redirects import safe_path
from app.services import accounts
from app.services import cart as cart_service
from app.services import checkout as checkout_service


def _requested_next() -> str | None:
    """The destination actually asked for, or None.

    Deliberately distinct from `_next_destination`. The forms carry this
    one in a hidden field, and a field prefilled with the *default*
    destination would answer the question before the account is known:
    the chef would post `/cart` back to the server and land there, every
    time, because a stated `next` wins over the role.
    """
    return safe_path(request.values.get("next"))


def _next_destination(user: User | None = None) -> str:
    """Where to land after signing in.

    The value arrives from a query string or a hidden field, so it is
    filtered to a same-site path; anything else falls back to where the
    person signing in was heading anyway — the cart for a customer, the
    order queue for the chef, who does not have one.

    `user` is passed on the POST path because `current_user` is only the
    signed-in user *after* `login_user`, and the redirect is built from
    the account that just authenticated rather than the one that did.
    """
    candidate = _requested_next()
    if candidate:
        return candidate
    subject = current_user if user is None else user
    if getattr(subject, "is_chef_admin", False):
        return url_for("chef.orders")
    return url_for("order.cart")


def _adopt_guest_cart(user: User, guest_cart: cart_service.Cart) -> None:
    """Carry the cart built before sign-in into the customer's own.

    Called with the cart read *before* `login_user`: once the request is
    authenticated the guest cart is no longer the current owner's, so it
    cannot be read back (04-WORKFLOWS.md).
    """
    overflowed = cart_service.merge_into_user_cart(user.get_id(), guest_cart)
    if overflowed:
        # Never dropped without saying so, here as anywhere else.
        flash(
            f"{len(overflowed)} item"
            f"{'' if len(overflowed) == 1 else 's'} from your cart did not "
            f"fit: a cart holds {cart_service.MAX_LINES} different items.",
            "error",
        )


@bp.route("/login", methods=["GET", "POST"])
@public_route
def login():
    """Sign in, then continue to wherever the customer was going."""
    if current_user.is_authenticated:
        return redirect(_next_destination())

    if request.method == "GET":
        return render_template("auth/login.html", next_path=_requested_next())

    guest_cart = cart_service.load_cart()
    user = accounts.authenticate(
        request.form.get("email"), request.form.get("password")
    )
    if user is None:
        # One message for every failure — no account, wrong password, a
        # deactivated account — so the form cannot be used to find out
        # which addresses are registered.
        flash("Those details do not match an account.", "error")
        return (
            render_template(
                "auth/login.html",
                next_path=_requested_next(),
                email=request.form.get("email", ""),
            ),
            401,
        )

    login_user(user)
    _adopt_guest_cart(user, guest_cart)
    flash(f"Signed in as {user.email}.", "success")
    return redirect(_next_destination(user))


@bp.route("/register", methods=["GET", "POST"])
@public_route
def register():
    """Create a customer account and sign straight in with it."""
    if current_user.is_authenticated:
        return redirect(_next_destination())

    if request.method == "GET":
        return render_template(
            "auth/register.html",
            next_path=_requested_next(),
            min_password_length=accounts.MIN_PASSWORD_LENGTH,
        )

    guest_cart = cart_service.load_cart()
    try:
        user = accounts.register_customer(
            email=request.form.get("email"),
            password=request.form.get("password"),
            password_confirmation=request.form.get("password_confirmation"),
            display_name=request.form.get("display_name"),
        )
    except accounts.RegistrationError as error:
        flash(str(error), "error")
        return (
            render_template(
                "auth/register.html",
                next_path=_requested_next(),
                min_password_length=accounts.MIN_PASSWORD_LENGTH,
                email=request.form.get("email", ""),
                display_name=request.form.get("display_name", ""),
            ),
            400,
        )

    login_user(user)
    _adopt_guest_cart(user, guest_cart)
    flash("Your account is ready.", "success")
    return redirect(_next_destination())


@bp.post("/logout")
@login_required
def logout():
    """Sign out, and take the cart with it.

    A cart belongs to its owner, so it leaves when they do: on a shared
    machine the next person meets an empty cart rather than somebody
    else's order (04-WORKFLOWS.md).

    A POST, not a GET: a link that signs a customer out can be triggered
    by anything that fetches it.

    Anything else this customer left in the session goes with them.
    `logout_user` ends the login, not the session, so a message flashed
    but never rendered — the confirmation after a redirect the customer
    never followed, which carries their order reference — would be shown
    to whoever signs in next on this browser, and the open checkout would
    send that person to a reference that is not theirs.
    """
    cart_service.clear_cart()
    checkout_service.clear_checkout_session(session)
    session.pop("_flashes", None)
    logout_user()
    flash("You are signed out.", "success")
    return redirect(url_for("public.index"))
