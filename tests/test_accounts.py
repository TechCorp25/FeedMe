"""The credential rules, tested where they live.

The web form and the JSON token route authenticate the same customers
against the same collection, so the rules are asserted against the
service rather than through one of its two surfaces.
"""

from __future__ import annotations

import pytest

from app.services import accounts

PASSWORD = "a-long-enough-passphrase"


def register(app, **overrides):
    form = {
        "email": "ada@example.com",
        "password": PASSWORD,
        "password_confirmation": PASSWORD,
    }
    form.update(overrides)
    with app.app_context():
        return accounts.register_customer(**form)


@pytest.mark.parametrize(
    "address",
    ["ada@example.com", "ada.lovelace+orders@mail.example.co.uk", "a@b.co"],
)
def test_an_address_shaped_like_an_address_is_accepted(app, db, address):
    assert register(app, email=address).email == address.lower()


@pytest.mark.parametrize(
    "address",
    ["", "   ", "ada", "ada@", "@example.com", "ada@example", "ada @example.com"],
)
def test_what_is_plainly_not_an_address_is_refused(app, db, address):
    with pytest.raises(accounts.RegistrationError):
        register(app, email=address)


def test_the_address_is_stored_lowercased_and_trimmed(app, db):
    user = register(app, email="  ADA@Example.COM ")

    assert user.email == "ada@example.com"
    assert db["users"].find_one({"email": "ada@example.com"}) is not None


def test_length_is_the_whole_password_rule(app, db):
    """No composition rules: a long passphrase of one alphabet is fine."""
    short = "x" * (accounts.MIN_PASSWORD_LENGTH - 1)
    with pytest.raises(accounts.RegistrationError):
        register(app, password=short, password_confirmation=short)

    plain = "a" * accounts.MIN_PASSWORD_LENGTH
    assert register(app, password=plain, password_confirmation=plain) is not None


def test_an_unreasonably_long_password_is_refused_before_hashing(app, db):
    huge = "x" * (accounts.MAX_PASSWORD_LENGTH + 1)
    with pytest.raises(accounts.RegistrationError):
        register(app, password=huge, password_confirmation=huge)


def test_the_password_is_never_stored_in_the_clear(app, db):
    register(app)

    stored = db["users"].find_one({"email": "ada@example.com"})
    assert PASSWORD not in str(stored)
    # Argon2id, never MD5 or the SHA family (02-ARCHITECTURE.md).
    assert stored["password_hash"].startswith("$argon2id$")


def test_authenticate_returns_none_rather_than_saying_why(app, db):
    register(app)

    with app.app_context():
        assert accounts.authenticate("ada@example.com", "wrong") is None
        assert accounts.authenticate("nobody@example.com", PASSWORD) is None
        assert accounts.authenticate("", "") is None
        assert accounts.authenticate("ada@example.com", PASSWORD) is not None
