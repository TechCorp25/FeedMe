"""The chef's out-of-band password reset.

`blueprints/auth/__init__.py` records a deliberate decision: there is no
password reset flow, because a reset is *delivered* — by email — and
04-WORKFLOWS.md keeps notifications out of v1 entirely. A reset form with
no delivery channel would be a form that appears to work and does not.
That reasoning stands and this module does not revisit it.

What it closes is the other half of the same sentence. "The chef resets a
password out of band" named a mechanism that did not exist: no chef route
set a customer's password and no script did, so "out of band" meant
editing an Argon2 hash in MongoDB by hand. A customer who forgot their
password had no route back into their account at all.

**A set, not a token.** A token needs somewhere to send it, and that is
precisely the thing that does not exist. So the chef sets the password and
reads it to the customer over the phone — the channel the kitchen already
has, and the one 04-WORKFLOWS.md leaves them.

**The password is generated, never typed.** A chef inventing a password
for somebody else picks a memorable one, picks it from a pattern, and
picks it again next time. Generating it means the strength does not depend
on who is having a bad afternoon, and it means the chef never learns a
customer's habits. The alphabet drops every character that is ambiguous
out loud — no `O` against `0`, no `l` against `1` — because this is going
to be spoken down a telephone line.

**It is shown exactly once, and it is never stored.** Only the Argon2 hash
is written. The plaintext exists for the length of one response and is
deliberately *not* flashed: a flash outlives its redirect, and this
codebase has already been bitten once by a message surviving into somebody
else's session. It is returned to the caller, rendered into the page that
answered the POST, and never put in a log, a session, a query string or a
database field.

**It never displays or recovers the old password.** It cannot: the old
value was only ever an Argon2 hash. Nothing here reads `password_hash`.

**Where the record goes — and why not the ledger.** `account_ledger` is an
append-only record of *money*: every entry carries a signed
`amount_cents`, and 01-DOMAIN.md derives the customer's balance by summing
them. A password change has no amount. Writing it as a zero-valued
`adjustment` would file a security event in a financial record, put a row
the chef cannot act on in the middle of a page they settle accounts
against, and make "the balance is the sum of the entries" a sentence with
an exception in it. It is therefore **logged and not balanced** —
structured, at INFO, naming the customer and the acting chef, and never
the password (02-ARCHITECTURE.md). No new field on `users`, either: a
`password_set_at` would be a schema addition 01-DOMAIN.md does not carry,
and the log already answers the question it would.
"""

from __future__ import annotations

import logging
import secrets

from app.db.repositories import users as users_repo
from app.models.users import User
from app.security.passwords import hash_password
from app.services.accounts import MIN_PASSWORD_LENGTH

logger = logging.getLogger(__name__)

#: Every character that survives being read aloud. No `O`/`0`, no `l`/`1`,
#: no `u`/`v` ambiguity, and lowercase throughout so nobody has to say
#: "capital" thirteen times.
ALPHABET = "abcdefghjkmnpqrstwxyz3456789"

#: Four groups of four, hyphenated. Sixteen characters out of a 28-symbol
#: alphabet is a little over 76 bits, and the hyphens are what make it
#: dictatable — a customer writing it down gets four short things to hold
#: in their head rather than one long one.
GROUP_SIZE = 4
GROUPS = 4


class PasswordResetError(ValueError):
    """A refusal the chef can fix, with the wording to show them."""


def generate_password() -> str:
    """A new password, from `secrets` and readable down a phone line."""
    groups = [
        "".join(secrets.choice(ALPHABET) for _ in range(GROUP_SIZE))
        for _ in range(GROUPS)
    ]
    password = "-".join(groups)
    # The generator and the registration rule cannot be allowed to drift:
    # a password this issues that the customer could not have chosen would
    # be one they can never re-set for themselves.
    assert len(password) >= MIN_PASSWORD_LENGTH
    return password


def set_password(chef: User, customer: User) -> str:
    """Set a new password on one customer and return it, once.

    The return value is the only copy that will ever exist in plaintext.
    The caller renders it and drops it; nothing here keeps it.
    """
    if customer.is_chef_admin:
        # The administrative account is not reset through a customer's
        # page. It has no customer to read a password out to, and a route
        # that can re-credential the one account with full capability
        # should not be reachable by pointing this one at a different id.
        raise PasswordResetError(
            "This is the chef-admin account, not a customer. Its password is "
            "set with scripts/create_chef_admin.py."
        )
    if customer.id is None:
        raise PasswordResetError("That customer no longer exists.")

    password = generate_password()
    if not users_repo.chef_set_password_hash(customer.id, hash_password(password)):
        raise PasswordResetError("That customer no longer exists.")

    # Named, timed and attributed — and without the password, the hash, or
    # anything else that would let this line be replayed into an account.
    logger.info(
        "chef set a customer password",
        extra={
            "customer_id": customer.id,
            "customer_email": customer.email,
            "set_by": chef.email,
        },
    )
    return password
