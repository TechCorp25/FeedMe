"""The chef's out-of-band password set.

`blueprints/auth/__init__.py` decided there is no password reset flow,
because a reset is delivered by email and 04-WORKFLOWS.md keeps
notifications out of v1. That decision stands. What did not exist was the
out-of-band mechanism the same comment promised: no chef route set a
customer's password and no script did, so "out of band" meant editing an
Argon2 hash in MongoDB by hand.

`test_a_customer_who_lost_their_password_can_be_let_back_in` is the test
that fails before the fix: it locks a customer out, has the chef set a new
password, and signs the customer back in with it — through the served
routes, with no direct write to `users`.
"""

from __future__ import annotations

import re

import pytest

from app.models.users import Role, User
from app.security.passwords import hash_password, verify_password
from app.services import chef_credentials
from app.services.accounts import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

CHEF_PASSWORD = "a-long-enough-passphrase"
OLD_PASSWORD = "the-old-one-they-forgot"

#: The block the page renders the one-shot password into.
ISSUED = re.compile(r'<code class="issued-password">([^<]+)</code>')


@pytest.fixture()
def chef(db):
    user = User(
        email="chef@example.com",
        password_hash=hash_password(CHEF_PASSWORD),
        display_name="Chef",
        role=Role.CHEF_ADMIN,
    )
    db["users"].insert_one(user.to_mongo())
    return user


@pytest.fixture()
def customer(db):
    user = User(
        email="ada@example.com",
        password_hash=hash_password(OLD_PASSWORD),
        display_name="Ada Customer",
        role=Role.CUSTOMER,
    )
    return str(db["users"].insert_one(user.to_mongo()).inserted_id)


@pytest.fixture()
def signed_in(client, chef):
    client.post("/login", data={"email": "chef@example.com", "password": CHEF_PASSWORD})
    return client


def _issued(response) -> str:
    match = ISSUED.search(response.get_data(as_text=True))
    assert match, "the page did not render a one-shot password"
    return match.group(1).strip()


def _hash_of(db, user_id: str) -> str:
    from bson import ObjectId

    return db["users"].find_one({"_id": ObjectId(user_id)})["password_hash"]


# --- the gap this closes ----------------------------------------------------


def test_a_customer_who_lost_their_password_can_be_let_back_in(
    client, db, signed_in, customer
):
    """End to end, through the served routes, with no hand-edited hash."""
    issued = _issued(signed_in.post(f"/chef/customers/{customer}/password"))
    signed_in.post("/logout")

    # The old one no longer works...
    refused = client.post(
        "/login", data={"email": "ada@example.com", "password": OLD_PASSWORD}
    )
    assert refused.status_code == 401

    # ...and the one the chef read out does.
    accepted = client.post(
        "/login",
        data={"email": "ada@example.com", "password": issued},
        follow_redirects=True,
    )
    assert accepted.status_code == 200
    assert "Signed in as ada@example.com" in accepted.get_data(as_text=True)


# --- shown once, and only once ----------------------------------------------


def test_the_password_is_not_shown_again_on_a_reload(signed_in, customer):
    issued = _issued(signed_in.post(f"/chef/customers/{customer}/password"))
    reloaded = signed_in.get(f"/chef/customers/{customer}/ledger")
    assert issued not in reloaded.get_data(as_text=True)
    assert "issued-password" not in reloaded.get_data(as_text=True)


def test_the_password_is_answered_directly_rather_than_redirected_to(
    signed_in, customer
):
    """A redirect would need somewhere to carry the plaintext."""
    response = signed_in.post(f"/chef/customers/{customer}/password")
    assert response.status_code == 200


def test_the_password_never_reaches_the_session(signed_in, customer):
    """Not a flash: a flash outlives its redirect and its reader."""
    issued = _issued(signed_in.post(f"/chef/customers/{customer}/password"))
    with signed_in.session_transaction() as session:
        assert issued not in str(dict(session))
        assert "_flashes" not in session or issued not in str(session["_flashes"])


def test_the_plaintext_is_never_stored(signed_in, customer, db):
    issued = _issued(signed_in.post(f"/chef/customers/{customer}/password"))
    stored = _hash_of(db, customer)
    assert issued not in stored
    assert stored.startswith("$argon2")
    assert verify_password(stored, issued)


def test_the_old_password_is_never_displayed_or_recoverable(signed_in, customer):
    body = signed_in.post(
        f"/chef/customers/{customer}/password"
    ).get_data(as_text=True)
    assert OLD_PASSWORD not in body
    # The page says why, rather than leaving the chef to wonder.
    assert "only the hash is stored" in body


def test_two_resets_issue_two_different_passwords(signed_in, customer):
    first = _issued(signed_in.post(f"/chef/customers/{customer}/password"))
    second = _issued(signed_in.post(f"/chef/customers/{customer}/password"))
    assert first != second


# --- what it writes, and what it leaves alone -------------------------------


def test_only_the_hash_changes(signed_in, customer, db):
    from bson import ObjectId

    before = db["users"].find_one({"_id": ObjectId(customer)})
    signed_in.post(f"/chef/customers/{customer}/password")
    after = db["users"].find_one({"_id": ObjectId(customer)})

    assert after["password_hash"] != before["password_hash"]
    for field in ("email", "role", "is_active", "display_name", "dietary_notes"):
        assert after[field] == before[field]


def test_no_ledger_entry_is_written(signed_in, customer, db):
    """A password change carries no amount and does not belong in a balance."""
    signed_in.post(f"/chef/customers/{customer}/password")
    assert db["account_ledger"].count_documents({}) == 0


def test_a_deactivated_account_is_not_reactivated_by_a_reset(signed_in, db, customer):
    from bson import ObjectId

    db["users"].update_one(
        {"_id": ObjectId(customer)}, {"$set": {"is_active": False}}
    )
    signed_in.post(f"/chef/customers/{customer}/password")
    assert db["users"].find_one({"_id": ObjectId(customer)})["is_active"] is False


# --- the generated password -------------------------------------------------


def test_the_generated_password_satisfies_the_registration_rule():
    """A password the customer could not have chosen is one they cannot re-set."""
    for _ in range(50):
        password = chef_credentials.generate_password()
        assert MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH


def test_the_generated_password_avoids_characters_that_are_ambiguous_aloud():
    """It is going to be dictated down a telephone line."""
    allowed = set(chef_credentials.ALPHABET) | {"-"}
    for _ in range(50):
        assert set(chef_credentials.generate_password()) <= allowed
    for ambiguous in "01lIoOuv":
        assert ambiguous not in chef_credentials.ALPHABET


def test_the_generated_password_is_not_predictable():
    issued = {chef_credentials.generate_password() for _ in range(200)}
    assert len(issued) == 200


# --- refusals ---------------------------------------------------------------


def test_the_chef_admin_account_cannot_be_reset_through_this_route(
    signed_in, db, chef
):
    """Pointing the route at the administrative account is refused."""
    chef_id = str(db["users"].find_one({"email": "chef@example.com"})["_id"])
    response = signed_in.post(f"/chef/customers/{chef_id}/password")
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "issued-password" not in body
    assert "create_chef_admin.py" in body
    # And the chef's own credentials are untouched.
    assert verify_password(_hash_of(db, chef_id), CHEF_PASSWORD)


def test_an_unknown_customer_is_a_404(signed_in):
    assert signed_in.post("/chef/customers/nope/password").status_code == 404


def test_a_get_is_not_a_reset(signed_in, customer):
    """Nothing sets a password by being fetched."""
    assert signed_in.get(f"/chef/customers/{customer}/password").status_code == 405


# --- access -----------------------------------------------------------------


def test_a_signed_out_visitor_cannot_reset_a_password(client, customer, db):
    response = client.post(f"/chef/customers/{customer}/password")
    assert response.status_code in {302, 401, 404}
    assert verify_password(_hash_of(db, customer), OLD_PASSWORD)


def test_a_customer_cannot_reset_another_customers_password(client, db, customer):
    """404, not 403: a 403 would confirm the route exists."""
    other = User(
        email="mal@example.com",
        password_hash=hash_password(CHEF_PASSWORD),
        role=Role.CUSTOMER,
    )
    db["users"].insert_one(other.to_mongo())
    client.post("/login", data={"email": "mal@example.com", "password": CHEF_PASSWORD})

    assert client.post(f"/chef/customers/{customer}/password").status_code == 404
    assert verify_password(_hash_of(db, customer), OLD_PASSWORD)


def test_the_control_is_on_the_customers_page(signed_in, customer):
    page = signed_in.get(f"/chef/customers/{customer}/ledger").get_data(as_text=True)
    assert f"/chef/customers/{customer}/password" in page
    assert "Set a new password" in page
    # It says why there is no reset link, where somebody would look for one.
    assert "no reset link" in page
