"""JWT issuance for a future mobile client. The web app does not use it."""

from __future__ import annotations

import pytest

from app.db.repositories.users import create_user
from app.models.users import Role, User
from app.security.passwords import hash_password, verify_password
from app.security.tokens import TokenError, issue_refresh_token, verify_access_token


@pytest.fixture()
def customer(app):
    with app.app_context():
        return create_user(
            User(
                email="Customer@Example.com",
                password_hash=hash_password("correct horse battery staple"),
                display_name="A Customer",
            )
        )


def test_email_is_stored_lowercased(customer):
    assert customer.email == "customer@example.com"


def test_password_hash_is_argon2_and_verifies():
    stored = hash_password("s3cret")
    assert stored.startswith("$argon2")
    assert verify_password(stored, "s3cret") is True
    assert verify_password(stored, "wrong") is False


def test_token_is_issued_for_valid_credentials(app, client, customer):
    response = client.post(
        "/api/auth/token",
        json={"email": "customer@example.com", "password": "correct horse battery staple"},
    )
    assert response.status_code == 200

    body = response.get_json()
    assert body["token_type"] == "Bearer"

    with app.app_context():
        claims = verify_access_token(body["access_token"])
    assert claims["sub"] == customer.id
    assert claims["role"] == Role.CUSTOMER.value
    assert claims["exp"] > claims["iat"]


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "customer@example.com", "password": "wrong"},
        {"email": "nobody@example.com", "password": "correct horse battery staple"},
        {},
    ],
)
def test_failures_are_indistinguishable(client, customer, payload):
    """One message for every failure mode: never reveal which part failed."""
    response = client.post("/api/auth/token", json=payload)
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid_credentials"}


def test_an_inactive_user_gets_no_token(app, client):
    with app.app_context():
        create_user(
            User(
                email="dormant@example.com",
                password_hash=hash_password("pw"),
                is_active=False,
            )
        )
    response = client.post(
        "/api/auth/token", json={"email": "dormant@example.com", "password": "pw"}
    )
    assert response.status_code == 401


def test_a_tampered_token_is_rejected(app, client, customer):
    response = client.post(
        "/api/auth/token",
        json={"email": "customer@example.com", "password": "correct horse battery staple"},
    )
    token = response.get_json()["access_token"]

    with app.app_context():
        with pytest.raises(TokenError):
            verify_access_token(token[:-2] + ("aa" if not token.endswith("aa") else "bb"))


def test_refresh_rotation_is_not_designed_yet():
    with pytest.raises(NotImplementedError):
        issue_refresh_token("u1")
