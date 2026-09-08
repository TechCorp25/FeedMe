"""Checkout: the moment the catalogue stops being live.

04-WORKFLOWS.md fixes what confirming does — snapshot the name, price and
full allergen block onto every line, total in integer minor units,
generate the reference, write the ledger charge, clear the cart. These
tests hold it to each of those, and to the two refusals that protect it:
a blocked cart and somebody else's order.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.repositories import orders as orders_repo
from app.models.catalogue import Component, Dish
from app.models.orders import LedgerEntryType, OrderStatus
from app.services import accounts
from app.services import checkout as checkout_service

PASSWORD = "a-long-enough-passphrase"

REVIEWED = {
    "contains": ["milk"],
    "may_contain": ["peanut"],
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
            "allergens": dict(REVIEWED),
        }
    )
    return str(db["components"].insert_one(item.to_mongo()).inserted_id)


@pytest.fixture()
def ragu(db) -> str:
    item = Dish.model_validate(
        {
            "name": "Lamb ragu",
            "slug": "lamb-ragu",
            "category": "dinner",
            "price_cents": 2400,
            "unit": "portion",
            "is_available": True,
            "allergens": {
                "contains": [],
                "may_contain": [],
                "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
                "reviewed_by": "chef",
            },
            "serves": 2,
        }
    )
    return str(db["dishes"].insert_one(item.to_mongo()).inserted_id)


@pytest.fixture()
def signed_in(client, app, db):
    """A registered customer with a session, ready to check out."""
    with app.app_context():
        accounts.register_customer(
            email="ada@example.com",
            password=PASSWORD,
            password_confirmation=PASSWORD,
            display_name="Ada",
        )
    client.post("/login", data={"email": "ada@example.com", "password": PASSWORD})
    return client


def tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def place(client, **overrides):
    form = {
        "requested_for": tomorrow(),
        "fulfilment": "collection",
        "customer_note": "",
    }
    form.update(overrides)
    return client.post("/checkout", data=form)


def fill_cart(client, item_type: str, item_id: str, quantity: str = "1"):
    return client.post(
        "/cart/add",
        data={"item_type": item_type, "item_id": item_id, "quantity": quantity},
    )


# --- the happy path ---------------------------------------------------------


def test_placing_an_order_snapshots_price_and_the_whole_allergen_block(
    signed_in, db, harissa, ragu
):
    fill_cart(signed_in, "component", harissa, "2")
    fill_cart(signed_in, "dish", ragu)

    response = place(signed_in)

    assert response.status_code == 302
    stored = db["orders"].find_one({})
    assert stored is not None
    assert stored["status"] == OrderStatus.PLACED.value
    assert stored["payment_status"] == "unpaid"

    lines = {line["name_snapshot"]: line for line in stored["lines"]}
    assert lines["Harissa"]["unit_price_cents"] == 850
    assert lines["Harissa"]["quantity"] == 2
    assert lines["Harissa"]["line_total_cents"] == 1700
    # The entire block, not a summary of it: `may_contain` is part of what
    # the customer was shown and part of what is kept.
    assert lines["Harissa"]["allergen_snapshot"]["contains"] == ["milk"]
    assert lines["Harissa"]["allergen_snapshot"]["may_contain"] == ["peanut"]
    assert lines["Harissa"]["allergen_snapshot"]["reviewed_by"] == "chef"

    assert stored["subtotal_cents"] == 1700 + 2400
    assert stored["total_cents"] == 4100
    assert all(isinstance(line["line_total_cents"], int) for line in stored["lines"])
    assert isinstance(stored["total_cents"], int)


def test_a_later_catalogue_edit_does_not_reach_a_placed_order(
    signed_in, db, harissa
):
    fill_cart(signed_in, "component", harissa)
    place(signed_in)

    db["components"].update_one(
        {"slug": "harissa"},
        {"$set": {"name": "Harissa (new recipe)", "price_cents": 1200,
                  "allergens.contains": ["milk", "sesame"]}},
    )

    line = db["orders"].find_one({})["lines"][0]
    assert line["name_snapshot"] == "Harissa"
    assert line["unit_price_cents"] == 850
    assert line["allergen_snapshot"]["contains"] == ["milk"]


def test_placing_an_order_writes_the_ledger_charge_and_clears_the_cart(
    signed_in, db, harissa
):
    fill_cart(signed_in, "component", harissa, "3")
    place(signed_in)

    order = db["orders"].find_one({})
    entry = db["account_ledger"].find_one({})
    assert entry["entry_type"] == LedgerEntryType.CHARGE.value
    assert entry["amount_cents"] == order["total_cents"] == 2550
    assert entry["order_id"] == str(order["_id"])
    assert entry["user_id"] == order["user_id"]
    assert order["reference"] in entry["description"]

    assert b"Your cart is empty" in signed_in.get("/cart").data


def test_the_order_records_who_placed_it_and_when(signed_in, db, harissa):
    fill_cart(signed_in, "component", harissa)
    place(signed_in, customer_note="Ring the top bell.")

    order = db["orders"].find_one({})
    assert order["customer_note"] == "Ring the top bell."
    assert order["requested_for"].date().isoformat() == tomorrow()
    assert [entry["status"] for entry in order["status_history"]] == ["placed"]
    assert order["status_history"][0]["by"] == order["user_id"]


def test_the_confirmation_page_shows_the_snapshot_it_stored(
    signed_in, db, harissa
):
    fill_cart(signed_in, "component", harissa)
    response = place(signed_in)

    page = signed_in.get(response.headers["Location"]).get_data(as_text=True)
    assert db["orders"].find_one({})["reference"] in page
    assert "Harissa" in page
    assert "$8.50" in page
    # The declaration travels with the order, in the same words as the
    # catalogue page uses.
    assert "Contains" in page
    assert "Milk" in page
    assert "May contain" in page


# --- references -------------------------------------------------------------


def test_references_follow_the_documented_shape_and_increment(
    signed_in, db, harissa, ragu
):
    fill_cart(signed_in, "component", harissa)
    place(signed_in)
    fill_cart(signed_in, "dish", ragu)
    place(signed_in)

    references = sorted(order["reference"] for order in db["orders"].find({}))
    prefix = f"MP-{date.today():%y%m}-"
    assert references == [f"{prefix}0001", f"{prefix}0002"]


def test_the_first_reference_of_a_month_starts_at_one(app, db):
    with app.app_context():
        assert checkout_service.next_reference(date(2026, 9, 8)) == "MP-2609-0001"


def test_a_reference_the_index_refuses_is_drawn_again(signed_in, db, harissa):
    """Two orders in the same second must not be handed one number.

    The unique index on `reference` is what decides; the service redraws
    rather than handing the customer a failure.
    """
    fill_cart(signed_in, "component", harissa)
    prefix = f"MP-{date.today():%y%m}-"

    real_create = orders_repo.create_order
    calls = {"n": 0}

    def racing_create(user_id, order):
        calls["n"] += 1
        if calls["n"] == 1:
            # Somebody else took this number between the draw and the write.
            raise orders_repo.ReferenceTaken(order.reference)
        return real_create(user_id, order)

    from app.services import checkout as service

    service.orders_repo.create_order = racing_create
    try:
        response = place(signed_in)
    finally:
        service.orders_repo.create_order = real_create

    assert response.status_code == 302
    assert calls["n"] == 2
    assert db["orders"].find_one({})["reference"].startswith(prefix)


def test_the_month_restarts_the_counter(app, db):
    with app.app_context():
        assert checkout_service.reference_prefix_for(date(2026, 1, 5)) == "MP-2601-"
        assert checkout_service.reference_prefix_for(date(2026, 12, 5)) == "MP-2612-"


# --- refusals ---------------------------------------------------------------


def test_checkout_refuses_a_cart_holding_a_withdrawn_item(
    signed_in, db, harissa
):
    fill_cart(signed_in, "component", harissa)
    db["components"].update_one(
        {"slug": "harissa"}, {"$set": {"is_available": False}}
    )

    response = place(signed_in)

    assert response.status_code == 302
    assert response.headers["Location"] == "/cart"
    assert db["orders"].count_documents({}) == 0
    assert db["account_ledger"].count_documents({}) == 0


def test_the_cart_page_will_not_offer_checkout_while_a_line_is_blocked(
    signed_in, db, harissa
):
    fill_cart(signed_in, "component", harissa)
    db["components"].update_one(
        {"slug": "harissa"}, {"$set": {"is_available": False}}
    )

    page = signed_in.get("/cart").get_data(as_text=True)
    assert 'href="/checkout"' not in page
    assert "disabled" in page


def test_checkout_refuses_an_empty_cart(signed_in, db):
    assert signed_in.get("/checkout").headers["Location"] == "/cart"
    assert place(signed_in).headers["Location"] == "/cart"
    assert db["orders"].count_documents({}) == 0


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("requested_for", "", b"Choose the date"),
        ("requested_for", "not-a-date", b"Choose the date"),
        (
            "requested_for",
            (date.today() - timedelta(days=1)).isoformat(),
            b"already passed",
        ),
        (
            "requested_for",
            (date.today() + timedelta(days=400)).isoformat(),
            b"within the next",
        ),
        ("fulfilment", "teleport", b"collection or delivery"),
        ("fulfilment", "", b"collection or delivery"),
    ],
)
def test_checkout_refuses_what_it_cannot_read(
    signed_in, db, harissa, field, value, expected
):
    fill_cart(signed_in, "component", harissa)

    response = place(signed_in, **{field: value})

    assert response.status_code == 400
    assert expected in response.data
    assert db["orders"].count_documents({}) == 0


def test_delivery_needs_an_address_and_keeps_it_on_the_account(
    signed_in, db, harissa
):
    fill_cart(signed_in, "component", harissa)

    refused = place(signed_in, fulfilment="delivery")
    assert refused.status_code == 400
    assert b"delivered to" in refused.data
    assert db["orders"].count_documents({}) == 0

    accepted = place(
        signed_in, fulfilment="delivery", delivery_address="12 Smith St, Fitzroy"
    )
    assert accepted.status_code == 302
    assert db["orders"].find_one({})["fulfilment"] == "delivery"
    # 01-DOMAIN.md puts the address on the customer, not on the order.
    assert (
        db["users"].find_one({"email": "ada@example.com"})["delivery_address"]
        == "12 Smith St, Fitzroy"
    )
    assert "delivery_address" not in db["orders"].find_one({})


def test_a_note_longer_than_the_limit_is_refused(signed_in, db, harissa):
    fill_cart(signed_in, "component", harissa)

    response = place(
        signed_in,
        customer_note="x" * (checkout_service.MAX_NOTE_LENGTH + 1),
    )

    assert response.status_code == 400
    assert db["orders"].count_documents({}) == 0


def test_checkout_requires_an_account(client, db, harissa):
    fill_cart(client, "component", harissa)

    assert client.get("/checkout").headers["Location"].startswith("/login")
    assert client.post("/checkout", data={}).headers["Location"].startswith("/login")
    assert db["orders"].count_documents({}) == 0


def test_a_ledger_write_that_fails_leaves_the_order_standing(
    signed_in, db, harissa, caplog
):
    """The documented failure mode, held to what it claims.

    The order is one atomic write and the ledger entry is a second one.
    If the entry cannot be written the order still stands — a customer is
    never charged for an order that does not exist — and the miss is
    logged loudly enough to be repaired.
    """
    fill_cart(signed_in, "component", harissa)

    from app.services import checkout as service

    def failing_append(user_id, entry):
        raise RuntimeError("ledger unavailable")

    real_append = service.ledger_repo.append_entry
    service.ledger_repo.append_entry = failing_append
    try:
        response = place(signed_in)
    finally:
        service.ledger_repo.append_entry = real_append

    assert response.status_code == 302
    assert db["orders"].count_documents({}) == 1
    assert db["account_ledger"].count_documents({}) == 0
    assert "without its ledger charge" in caplog.text


# --- tenancy ----------------------------------------------------------------


def test_another_customers_order_is_a_404_not_a_403(signed_in, app, db, harissa):
    fill_cart(signed_in, "component", harissa)
    place(signed_in)
    reference = db["orders"].find_one({})["reference"]

    signed_in.post("/logout")
    with app.app_context():
        accounts.register_customer(
            email="bob@example.com",
            password=PASSWORD,
            password_confirmation=PASSWORD,
        )
    signed_in.post("/login", data={"email": "bob@example.com", "password": PASSWORD})

    response = signed_in.get(f"/orders/{reference}")

    # 404, never 403: a 403 would confirm the order exists.
    assert response.status_code == 404


def test_the_cart_of_a_signed_out_customer_is_not_inherited(
    signed_in, app, db, harissa
):
    """A cart belongs to its owner, not to the browser (04-WORKFLOWS.md)."""
    fill_cart(signed_in, "component", harissa, "2")
    signed_in.post("/logout")

    assert b"Your cart is empty" in signed_in.get("/cart").data

    with app.app_context():
        accounts.register_customer(
            email="bob@example.com",
            password=PASSWORD,
            password_confirmation=PASSWORD,
        )
    signed_in.post("/login", data={"email": "bob@example.com", "password": PASSWORD})

    assert b"Your cart is empty" in signed_in.get("/cart").data


def test_a_guest_cart_comes_with_the_customer_at_sign_in(client, app, db, harissa):
    fill_cart(client, "component", harissa, "2")
    with app.app_context():
        accounts.register_customer(
            email="ada@example.com",
            password=PASSWORD,
            password_confirmation=PASSWORD,
        )

    client.post("/login", data={"email": "ada@example.com", "password": PASSWORD})

    cart = client.get("/cart").get_data(as_text=True)
    assert "Harissa" in cart
    assert "$17.00" in cart


def test_an_order_cannot_be_written_for_another_user(app, db):
    """The repository refuses a mismatch rather than trusting the caller."""
    from app.models.orders import Order

    with app.app_context():
        with pytest.raises(ValueError):
            orders_repo.create_order(
                "a" * 24,
                Order(user_id="b" * 24, reference="MP-2609-9999"),
            )
