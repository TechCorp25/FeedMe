"""`POST /api/cart` — the same intents as the forms, for `cart.js`.

The JSON surface is an enhancement over the form routes, never a
replacement: nothing on the cart is reachable only through it. It applies
the same rules, and it does the money arithmetic server-side so a client
never computes a price.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import create_app
from app.config import TestingConfig
from app.models.catalogue import Component

REVIEWED = {
    "contains": [],
    "may_contain": [],
    "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef",
}


@pytest.fixture()
def harissa(db) -> str:
    item = Component.model_validate(
        {
            "name": "Harissa",
            "slug": "harissa",
            "category": "sauce",
            "price_cents": 850,
            "unit": "250ml",
            "is_available": True,
            "is_archived": False,
            "allergens": dict(REVIEWED),
        }
    )
    return str(db["components"].insert_one(item.to_mongo()).inserted_id)


@pytest.fixture()
def draft(db) -> str:
    item = Component.model_validate(
        {
            "name": "Draft",
            "slug": "draft",
            "category": "sauce",
            "price_cents": 500,
            "is_available": False,
            "is_archived": False,
            "allergens": {"contains": [], "may_contain": []},
        }
    )
    return str(db["components"].insert_one(item.to_mongo()).inserted_id)


def post(client, **payload):
    return client.post("/api/cart", json=payload)


def test_adding_returns_the_whole_cart_priced_by_the_server(client, harissa):
    response = post(
        client, action="add", item_type="component", item_id=harissa, quantity=2
    )
    assert response.status_code == 200

    body = response.get_json()
    assert body["item_count"] == 2
    assert body["subtotal_cents"] == 1700
    assert body["blocked"] is False
    assert body["lines"] == [
        {
            "item_type": "component",
            "item_id": harissa,
            "name": "Harissa",
            "quantity": 2,
            "unit_price_cents": 850,
            "line_total_cents": 1700,
            "is_available": True,
        }
    ]


def test_the_api_and_the_forms_share_one_cart(client, harissa):
    post(client, action="add", item_type="component", item_id=harissa)
    assert "Harissa" in client.get("/cart").get_data(as_text=True)

    client.post(
        "/cart/update",
        data={"item_type": "component", "item_id": harissa, "quantity": "4"},
    )
    body = post(
        client, action="set", item_type="component", item_id=harissa, quantity=5
    ).get_json()
    assert body["item_count"] == 5


def test_setting_a_quantity_to_zero_removes_the_line(client, harissa):
    post(client, action="add", item_type="component", item_id=harissa)
    body = post(
        client, action="set", item_type="component", item_id=harissa, quantity=0
    ).get_json()

    assert body["lines"] == []
    assert body["item_count"] == 0


def test_removing_a_line_needs_no_quantity(client, harissa):
    post(client, action="add", item_type="component", item_id=harissa, quantity=3)
    body = post(
        client, action="remove", item_type="component", item_id=harissa
    ).get_json()

    assert body["lines"] == []


def test_an_unpublished_item_is_refused(client, draft):
    response = post(client, action="add", item_type="component", item_id=draft)

    assert response.status_code == 404
    assert response.get_json() == {"error": "unavailable"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"action": "add"},
        {"action": "add", "item_type": "component"},
        {"action": "add", "item_type": "meal", "item_id": "x"},
        {"action": "destroy", "item_type": "component", "item_id": "x"},
    ],
)
def test_a_request_that_names_no_valid_intent_is_refused(client, payload):
    """Refused, not ignored: a silent no-op misleads the caller."""
    response = client.post("/api/cart", json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


@pytest.mark.parametrize("quantity", ["two", -1, None])
def test_a_quantity_that_is_not_a_whole_number_is_refused(
    client, harissa, quantity
):
    response = post(
        client,
        action="set",
        item_type="component",
        item_id=harissa,
        quantity=quantity,
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_quantity"


def test_a_full_cart_is_reported_rather_than_silently_truncated(client, db):
    from app.services.cart import MAX_LINES

    ids = []
    for index in range(MAX_LINES + 1):
        item = Component.model_validate(
            {
                "name": f"Sauce {index}",
                "slug": f"sauce-{index}",
                "category": "sauce",
                "price_cents": 100,
                "is_available": True,
                "is_archived": False,
                "allergens": dict(REVIEWED),
            }
        )
        ids.append(str(db["components"].insert_one(item.to_mongo()).inserted_id))

    for item_id in ids[:MAX_LINES]:
        assert post(
            client, action="add", item_type="component", item_id=item_id
        ).status_code == 200

    response = post(client, action="add", item_type="component", item_id=ids[-1])
    assert response.status_code == 409
    assert response.get_json() == {"error": "cart_full", "max_lines": MAX_LINES}


def test_a_withdrawn_line_is_reported_as_blocking(client, db, harissa):
    post(client, action="add", item_type="component", item_id=harissa)
    db["components"].update_one(
        {"slug": "harissa"}, {"$set": {"is_available": False}}
    )

    body = post(
        client, action="set", item_type="component", item_id=harissa, quantity=2
    ).get_json()

    assert body["blocked"] is True
    assert body["lines"][0]["is_available"] is False
    assert body["lines"][0]["name"] == "Harissa"
    assert body["lines"][0]["line_total_cents"] == 0
    assert body["subtotal_cents"] == 0


# --- CSRF -------------------------------------------------------------------


@pytest.fixture()
def csrf_client(mongo_client):
    """The same app with CSRF left on, as it is everywhere but the tests."""
    config = TestingConfig()
    config.WTF_CSRF_ENABLED = True
    application = create_app(config, mongo_client=mongo_client)
    return application.test_client()


def test_the_cart_api_is_not_csrf_exempt(csrf_client):
    """A session-cookie surface: the token is required (02-ARCHITECTURE.md).

    `/api/auth/token` is exempt because it authenticates with credentials
    in the body. This one rides the session cookie, so it is not.
    """
    response = csrf_client.post(
        "/api/cart",
        json={"action": "add", "item_type": "component", "item_id": "x"},
    )
    assert response.status_code == 400

    form = csrf_client.post(
        "/cart/add", data={"item_type": "component", "item_id": "x"}
    )
    assert form.status_code == 400
