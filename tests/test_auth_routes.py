"""Registration, sign-in and sign-out over the real HTTP surface.

Sessions, not tokens (02-ARCHITECTURE.md), and every state-changing step
is a form POST that works without JavaScript.
"""

from __future__ import annotations

import pytest

from app.db.repositories import users as users_repo
from app.models.users import Role
from app.services import accounts

PASSWORD = "a-long-enough-passphrase"


@pytest.fixture()
def customer(app, db):
    with app.app_context():
        return accounts.register_customer(
            email="Ada@Example.com",
            password=PASSWORD,
            password_confirmation=PASSWORD,
            display_name="Ada",
        )


def sign_in(client, email=" ada@example.com ", password=PASSWORD, **extra):
    return client.post(
        "/login",
        data={"email": email, "password": password, **extra},
        follow_redirects=False,
    )


def test_registration_creates_a_customer_and_signs_them_in(client, db):
    response = client.post(
        "/register",
        data={
            "email": "New@Example.com",
            "display_name": "New Person",
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
    )

    assert response.status_code == 302
    stored = db["users"].find_one({"email": "new@example.com"})
    assert stored is not None
    assert stored["role"] == Role.CUSTOMER.value
    # Never the password itself, and never a hash a GPU chews through.
    assert PASSWORD not in str(stored)
    assert stored["password_hash"].startswith("$argon2")

    # The session is live: the login-only route no longer bounces to sign in.
    assert not client.get("/checkout").headers["Location"].startswith("/login")


def test_registration_refuses_an_address_that_already_has_an_account(
    client, customer
):
    response = client.post(
        "/register",
        data={
            "email": "ada@example.com",
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
    )

    assert response.status_code == 400
    assert b"already has an account" in response.data


@pytest.mark.parametrize(
    ("password", "confirmation", "expected"),
    [
        ("short", "short", b"at least"),
        (PASSWORD, PASSWORD + "!", b"do not match"),
    ],
)
def test_registration_refuses_a_password_it_cannot_accept(
    client, db, password, confirmation, expected
):
    response = client.post(
        "/register",
        data={
            "email": "someone@example.com",
            "password": password,
            "password_confirmation": confirmation,
        },
    )

    assert response.status_code == 400
    assert expected in response.data
    assert db["users"].count_documents({}) == 0


def test_sign_in_accepts_the_address_however_it_was_typed(client, customer):
    response = sign_in(client)

    assert response.status_code == 302
    assert not client.get("/checkout").headers["Location"].startswith("/login")


def test_sign_in_says_the_same_thing_for_every_failure(client, customer):
    wrong_password = sign_in(client, password="not-the-password")
    no_such_account = sign_in(client, email="nobody@example.com")

    assert wrong_password.status_code == 401
    assert no_such_account.status_code == 401
    # Byte-identical: the form cannot be used to find out which addresses
    # are registered.
    assert b"do not match an account" in wrong_password.data
    assert b"do not match an account" in no_such_account.data


def test_a_deactivated_account_cannot_sign_in(client, db, customer):
    db["users"].update_one(
        {"email": "ada@example.com"}, {"$set": {"is_active": False}}
    )

    assert sign_in(client).status_code == 401


def test_signing_in_stamps_last_login(client, db, customer):
    assert db["users"].find_one({"email": "ada@example.com"})["last_login_at"] is None

    sign_in(client)

    assert (
        db["users"].find_one({"email": "ada@example.com"})["last_login_at"]
        is not None
    )


def test_sign_in_returns_to_a_same_site_path(client, customer):
    response = sign_in(client, next="/dishes")

    assert response.headers["Location"] == "/dishes"


@pytest.mark.parametrize(
    "hostile", ["https://example.net/", "//example.net/", "/\\example.net"]
)
def test_sign_in_refuses_to_be_an_open_redirect(client, customer, hostile):
    response = sign_in(client, next=hostile)

    assert response.headers["Location"] == "/cart"


def test_sign_out_is_a_post_and_ends_the_session(client, customer):
    sign_in(client)

    assert client.get("/logout").status_code == 405

    response = client.post("/logout")
    assert response.status_code == 302
    # Back to being a guest: the login-only route redirects to sign in.
    assert client.get("/checkout").headers["Location"].startswith("/login")


def test_checkout_sends_a_signed_out_customer_to_sign_in_and_back(client):
    response = client.get("/checkout")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")
    assert "next=%2Fcheckout" in response.headers["Location"]


def test_the_header_offers_sign_in_to_a_guest_and_sign_out_to_a_customer(
    client, customer
):
    assert b"Sign in" in client.get("/").data

    sign_in(client)
    signed_in = client.get("/").data
    assert b"Sign out" in signed_in
    # A sign-out that a fetch can trigger is not a sign-out control.
    assert b'action="/logout"' in signed_in


def test_registration_never_creates_a_chef(client, db):
    client.post(
        "/register",
        data={
            "email": "aspiring@example.com",
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
            "role": "chef_admin",
        },
    )

    stored = db["users"].find_one({"email": "aspiring@example.com"})
    assert stored["role"] == Role.CUSTOMER.value


def test_a_rehash_is_written_when_argon2_asks_for_one(app, db, customer, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(accounts, "needs_rehash", lambda _hash: True)
        original = db["users"].find_one({"email": "ada@example.com"})["password_hash"]

        user = accounts.authenticate("ada@example.com", PASSWORD)

        assert user is not None
        stored = db["users"].find_one({"email": "ada@example.com"})["password_hash"]
        assert stored != original
        assert users_repo.get_user_by_email("ada@example.com") is not None


def test_deactivating_an_account_ends_its_existing_session(client, db, customer):
    """A session outlives the credentials that created it.

    The active check at sign-in only covers sign-in. Without one on the
    session itself, a customer deactivated after signing in keeps every
    page they already had — including placing orders — until their cookie
    expires.
    """
    sign_in(client)
    assert not client.get("/checkout").headers["Location"].startswith("/login")

    db["users"].update_one(
        {"email": "ada@example.com"}, {"$set": {"is_active": False}}
    )

    assert client.get("/checkout").headers["Location"].startswith("/login")
