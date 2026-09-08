"""Registration and sign-in.

The rules a credential has to satisfy live here, not in a route and not
in a template: the JSON token route and the web form authenticate the
same customers against the same `users` collection (02-ARCHITECTURE.md),
and a rule written twice is a rule that will drift.

Nothing here decides *where* a customer goes next. That is the route's
business, and the destination is filtered by `security/redirects.py`.
"""

from __future__ import annotations

import re

from app.db.repositories import users as users_repo
from app.models.base import utcnow
from app.models.users import Role, User
from app.security.passwords import hash_password, needs_rehash, verify_password

#: Deliberately permissive. An address is proven by delivering to it, and
#: nothing is delivered in v1 (04-WORKFLOWS.md keeps notifications out of
#: scope), so this rejects what is obviously not an address and refuses to
#: pretend to more certainty than that.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")

#: Length is the whole rule. Composition rules ("one capital, one digit")
#: push people towards predictable substitutions of short passwords and
#: buy nothing here, where hashing is Argon2id and there is no throttle to
#: be gamed by a wider alphabet.
MIN_PASSWORD_LENGTH = 10

#: Argon2 hashes any length; the bound stops a multi-megabyte body being
#: turned into work by the hasher.
MAX_PASSWORD_LENGTH = 256

MAX_DISPLAY_NAME_LENGTH = 120


class RegistrationError(ValueError):
    """A registration the customer can fix, with the wording to show them."""


def normalise_email(raw: str | None) -> str:
    return (raw or "").strip().lower()


def register_customer(
    *,
    email: str | None,
    password: str | None,
    password_confirmation: str | None,
    display_name: str | None = None,
) -> User:
    """Create a customer account, or raise `RegistrationError`.

    The role is not a parameter: this creates customers. The single
    `chef_admin` account is provisioned deliberately, never through a
    public form (01-DOMAIN.md).
    """
    address = normalise_email(email)
    secret = password or ""
    confirmation = password_confirmation or ""

    if not EMAIL_PATTERN.match(address):
        raise RegistrationError("Enter an email address.")
    if len(secret) < MIN_PASSWORD_LENGTH:
        raise RegistrationError(
            f"Choose a password of at least {MIN_PASSWORD_LENGTH} characters."
        )
    if len(secret) > MAX_PASSWORD_LENGTH:
        raise RegistrationError(
            f"Choose a password of at most {MAX_PASSWORD_LENGTH} characters."
        )
    if secret != confirmation:
        raise RegistrationError("The two passwords do not match.")

    user = User(
        email=address,
        password_hash=hash_password(secret),
        display_name=(display_name or "").strip()[:MAX_DISPLAY_NAME_LENGTH],
        role=Role.CUSTOMER,
    )
    try:
        return users_repo.create_user(user)
    except users_repo.EmailAlreadyRegistered as exc:
        # Said plainly. A registration form cannot hide that an address is
        # taken — the outcome differs either way — and pretending otherwise
        # would leave the customer unable to work out why nothing happened.
        raise RegistrationError(
            "That email address already has an account. Sign in instead."
        ) from exc


def authenticate(email: str | None, password: str | None) -> User | None:
    """The user those credentials identify, or None.

    One return value for every failure — no account, a deactivated
    account, a wrong password — so a caller cannot phrase a message that
    tells the difference.
    """
    address = normalise_email(email)
    secret = password or ""
    if not address or not secret or len(secret) > MAX_PASSWORD_LENGTH:
        return None

    user = users_repo.get_user_by_email(address)
    if user is None or user.id is None or not user.is_active:
        return None
    if not verify_password(user.password_hash, secret):
        return None

    if needs_rehash(user.password_hash):
        # Argon2's parameters moved on since this hash was written. The
        # password is in hand exactly once — now — so it is re-derived
        # here or not at all.
        users_repo.update_password_hash(user.id, hash_password(secret))

    now = utcnow()
    users_repo.record_login(user.id, now)
    return user.model_copy(update={"last_login_at": now})
