"""The customer's own account area.

04-WORKFLOWS.md gives the customer four pages over their own record and
one mutation on an order — cancellation from `placed` or `confirmed`.
These tests hold that surface to the rules the specification states about
it: every read scoped by `user_id` with a miss answering 404 rather than
403, a cancellation that is a state transition rather than a status
write, a ledger that nets to nothing when an order is cancelled, and
saved preference filters that narrow a browse page and say that they did.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.catalogue import Component
from app.models.orders import LedgerEntryType, OrderStatus
from app.services import accounts

PASSWORD = "a-long-enough-passphrase"

REVIEWED = {
    "contains": ["milk"],
    "may_contain": [],
    "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef",
}


def _component(db, *, name: str, slug: str, flags: list[str]) -> str:
    item = Component.model_validate(
        {
            "name": name,
            "slug": slug,
            "category": "sauce",
            "price_cents": 850,
            "unit": "250ml",
            "is_available": True,
            "preference_flags": flags,
            "allergens": dict(REVIEWED),
        }
    )
    return str(db["components"].insert_one(item.to_mongo()).inserted_id)


@pytest.fixture()
def harissa(db) -> str:
    return _component(db, name="Harissa", slug="harissa", flags=["chilli"])


@pytest.fixture()
def labneh(db) -> str:
    return _component(db, name="Labneh", slug="labneh", flags=["vegetarian"])


def _register(app, email: str, **extra) -> None:
    with app.app_context():
        accounts.register_customer(
            email=email,
            password=PASSWORD,
            password_confirmation=PASSWORD,
            **extra,
        )


def _sign_in(client, email: str) -> None:
    client.post("/login", data={"email": email, "password": PASSWORD})


@pytest.fixture()
def signed_in(client, app):
    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    return client


def _place_order(client, item_id: str, *, item_type: str = "component") -> str:
    """Put one item in the cart and check it out, as a customer would."""
    import re

    client.post(
        "/cart/add",
        data={"item_type": item_type, "item_id": item_id, "quantity": "1"},
    )
    page = client.get("/checkout").get_data(as_text=True)

    def hidden(name: str) -> str:
        match = re.search(rf'name="{re.escape(name)}" value="([^"]*)"', page)
        return match.group(1) if match else ""

    from datetime import date, timedelta

    response = client.post(
        "/checkout",
        data={
            "requested_for": (date.today() + timedelta(days=1)).isoformat(),
            "fulfilment": "collection",
            "checkout_token": hidden("checkout_token"),
            "review_digest": hidden("review_digest"),
        },
    )
    return response.headers["Location"].rsplit("/", 1)[-1]


# --- the pages are the customer's, and nobody else's ------------------------


@pytest.mark.parametrize(
    "path",
    ["/account/", "/account/orders", "/account/balance"],
)
def test_the_account_area_requires_a_session(client, path):
    response = client.get(path)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_another_customers_order_is_a_404_not_a_403(client, app, db, harissa):
    _register(app, "ada@example.com")
    _sign_in(client, "ada@example.com")
    reference = _place_order(client, harissa)
    client.post("/logout")

    _register(app, "bob@example.com")
    _sign_in(client, "bob@example.com")

    # 404, never 403: a 403 would confirm somebody else's order exists.
    assert client.get(f"/account/orders/{reference}").status_code == 404
    assert client.get(f"/api/orders/{reference}/status").status_code == 404
    assert (
        client.post(f"/account/orders/{reference}/cancel").status_code == 404
    )


def test_another_customers_order_is_not_in_the_history(client, app, db, harissa):
    _register(app, "ada@example.com")
    _sign_in(client, "ada@example.com")
    reference = _place_order(client, harissa)
    client.post("/logout")

    _register(app, "bob@example.com")
    _sign_in(client, "bob@example.com")

    html = client.get("/account/orders").get_data(as_text=True)
    assert f'href="/account/orders/{reference}"' not in html


def test_a_reference_that_names_nothing_is_a_404(signed_in):
    assert signed_in.get("/account/orders/MP-9901-0001").status_code == 404


def test_the_old_order_url_still_reaches_the_order(signed_in, harissa):
    """One order, one page. The path checkout used to send customers to
    is kept so a link a customer already has still works."""
    reference = _place_order(signed_in, harissa)

    response = signed_in.get(f"/orders/{reference}")

    assert response.status_code == 301
    assert response.headers["Location"] == f"/account/orders/{reference}"


# --- the history and the order ----------------------------------------------


def test_the_history_lists_the_order_that_was_placed(signed_in, harissa):
    reference = _place_order(signed_in, harissa)

    html = signed_in.get("/account/orders").get_data(as_text=True)

    assert reference in html
    assert "Placed" in html


def test_an_order_shows_the_snapshot_not_the_catalogue(
    signed_in, db, harissa
):
    """The declaration on the page is the one frozen at checkout."""
    reference = _place_order(signed_in, harissa)
    db["components"].update_one(
        {"slug": "harissa"},
        {"$set": {"name": "Harissa (new recipe)", "price_cents": 1, "allergens.contains": []}},
    )

    html = signed_in.get(f"/account/orders/{reference}").get_data(as_text=True)

    assert "Harissa" in html
    assert "new recipe" not in html
    assert "$8.50" in html
    assert "Milk" in html


# --- cancellation -----------------------------------------------------------


def test_a_placed_order_can_be_cancelled_and_is_credited_back(
    signed_in, db, harissa
):
    reference = _place_order(signed_in, harissa)

    response = signed_in.post(f"/account/orders/{reference}/cancel")

    assert response.status_code == 302
    stored = db["orders"].find_one({"reference": reference})
    assert stored["status"] == OrderStatus.CANCELLED.value
    # Every transition is recorded, never a bare status write.
    assert [entry["status"] for entry in stored["status_history"]] == [
        OrderStatus.PLACED.value,
        OrderStatus.CANCELLED.value,
    ]

    entries = list(db["account_ledger"].find({}))
    assert [entry["entry_type"] for entry in entries] == [
        LedgerEntryType.CHARGE.value,
        LedgerEntryType.CREDIT.value,
    ]
    # The credit offsets the charge exactly: a cancelled order nets to zero.
    assert sum(entry["amount_cents"] for entry in entries) == 0


def test_cancelling_twice_writes_one_credit(signed_in, db, harissa):
    """The write is conditional on the status it was decided against, so
    a form submitted twice cancels once and credits once."""
    reference = _place_order(signed_in, harissa)

    signed_in.post(f"/account/orders/{reference}/cancel")
    signed_in.post(f"/account/orders/{reference}/cancel")

    assert (
        db["account_ledger"].count_documents(
            {"entry_type": LedgerEntryType.CREDIT.value}
        )
        == 1
    )
    stored = db["orders"].find_one({"reference": reference})
    assert len(stored["status_history"]) == 2


def test_a_prepping_order_cannot_be_cancelled_by_the_customer(
    signed_in, db, harissa
):
    """Beyond `confirmed` it is the chef's call (04-WORKFLOWS.md)."""
    reference = _place_order(signed_in, harissa)
    db["orders"].update_one(
        {"reference": reference}, {"$set": {"status": OrderStatus.PREPPING.value}}
    )

    signed_in.post(f"/account/orders/{reference}/cancel")

    stored = db["orders"].find_one({"reference": reference})
    assert stored["status"] == OrderStatus.PREPPING.value
    assert (
        db["account_ledger"].count_documents(
            {"entry_type": LedgerEntryType.CREDIT.value}
        )
        == 0
    )


def test_the_cancel_control_is_not_drawn_once_it_would_be_refused(
    signed_in, db, harissa
):
    reference = _place_order(signed_in, harissa)
    assert "Cancel order" in signed_in.get(
        f"/account/orders/{reference}"
    ).get_data(as_text=True)

    db["orders"].update_one(
        {"reference": reference}, {"$set": {"status": OrderStatus.READY.value}}
    )

    assert "Cancel order" not in signed_in.get(
        f"/account/orders/{reference}"
    ).get_data(as_text=True)


# --- the balance ------------------------------------------------------------


def test_the_balance_sums_the_ledger(signed_in, db, harissa):
    _place_order(signed_in, harissa)

    html = signed_in.get("/account/balance").get_data(as_text=True)

    assert "$8.50" in html
    assert "Charge" in html


def test_a_cancelled_order_leaves_the_balance_at_zero(signed_in, harissa):
    reference = _place_order(signed_in, harissa)
    signed_in.post(f"/account/orders/{reference}/cancel")

    html = signed_in.get("/account/balance").get_data(as_text=True)

    assert "Credit" in html
    assert "-$8.50" in html
    assert "$0.00" in html


# --- the profile ------------------------------------------------------------


def test_the_profile_saves_the_fields_the_customer_owns(signed_in, db, harissa):
    response = signed_in.post(
        "/account/",
        data={
            "display_name": "Ada L",
            "phone": "0400 000 000",
            "delivery_address": "1 Test Street",
            "dietary_notes": "No coriander, please.",
            "preference": ["chilli"],
        },
    )

    assert response.status_code == 302
    stored = db["users"].find_one({"email": "ada@example.com"})
    assert stored["display_name"] == "Ada L"
    assert stored["delivery_address"] == "1 Test Street"
    assert stored["dietary_notes"] == "No coriander, please."
    assert stored["default_preference_filters"] == ["chilli"]


def test_the_profile_form_cannot_reach_role_or_password(signed_in, db):
    """Only the five fields the customer owns are read from the form."""
    before = db["users"].find_one({"email": "ada@example.com"})

    signed_in.post(
        "/account/",
        data={
            "display_name": "Ada",
            "role": "chef_admin",
            "is_active": "false",
            "password_hash": "not-a-hash",
            "email": "someone@else.example",
        },
    )

    after = db["users"].find_one({"email": "ada@example.com"})
    assert after["role"] == "customer"
    assert after["is_active"] is True
    assert after["password_hash"] == before["password_hash"]
    assert after["email"] == "ada@example.com"


def test_an_over_long_field_is_refused_and_the_form_comes_back(signed_in, db):
    response = signed_in.post(
        "/account/",
        data={"display_name": "Ada", "dietary_notes": "x" * 1001},
    )

    assert response.status_code == 400
    # What was typed is still in the form, not lost to a round trip.
    assert "x" * 1001 in response.get_data(as_text=True)
    assert db["users"].find_one({"email": "ada@example.com"})["dietary_notes"] is None


def test_an_unknown_preference_flag_is_dropped_not_stored(signed_in, db, harissa):
    signed_in.post(
        "/account/",
        data={"display_name": "Ada", "preference": ["chilli", "not-a-flag"]},
    )

    stored = db["users"].find_one({"email": "ada@example.com"})
    assert stored["default_preference_filters"] == ["chilli"]


# --- saved filters pre-applied to browsing ----------------------------------


def test_saved_filters_narrow_an_unfiltered_browse_and_say_so(
    signed_in, db, harissa, labneh
):
    signed_in.post(
        "/account/", data={"display_name": "Ada", "preference": ["chilli"]}
    )

    html = signed_in.get("/components").get_data(as_text=True)

    assert "Harissa" in html
    assert "Labneh" not in html
    # A shortened catalogue that does not explain itself reads as the
    # whole catalogue.
    assert "Filtered by the preferences saved on" in html
    assert "Show everything" in html


def test_a_url_that_states_its_filters_is_taken_literally(
    signed_in, db, harissa, labneh
):
    """Clearing the filters clears them, rather than putting the saved
    ones back."""
    signed_in.post(
        "/account/", data={"display_name": "Ada", "preference": ["chilli"]}
    )

    html = signed_in.get("/components?filtered=1").get_data(as_text=True)

    assert "Harissa" in html
    assert "Labneh" in html
    assert "Filtered by the preferences saved on" not in html


def test_saved_filters_do_not_touch_a_signed_out_visitor(
    client, db, harissa, labneh
):
    html = client.get("/components").get_data(as_text=True)

    assert "Harissa" in html
    assert "Labneh" in html


# --- the status endpoint ----------------------------------------------------


def test_the_status_endpoint_reports_the_order_it_is_asked_about(
    signed_in, db, harissa
):
    reference = _place_order(signed_in, harissa)

    payload = signed_in.get(f"/api/orders/{reference}/status").get_json()

    assert payload["status"] == OrderStatus.PLACED.value
    assert payload["is_terminal"] is False
    assert payload["can_cancel"] is True


def test_the_status_endpoint_reports_a_terminal_order_as_terminal(
    signed_in, db, harissa
):
    reference = _place_order(signed_in, harissa)
    signed_in.post(f"/account/orders/{reference}/cancel")

    payload = signed_in.get(f"/api/orders/{reference}/status").get_json()

    assert payload["status"] == OrderStatus.CANCELLED.value
    assert payload["is_terminal"] is True
    assert payload["can_cancel"] is False


def test_signing_out_takes_the_pending_flashes_with_it(client, app, harissa):
    """A message flashed but never rendered belongs to whoever queued it.

    Confirming an order queues one carrying the order reference, and a
    customer who closes the tab on the redirect never sees it. Without
    this, the next person to use the browser does.
    """
    _register(app, "ada@example.com")
    _sign_in(client, "ada@example.com")
    reference = _place_order(client, harissa)
    client.post("/logout")

    _register(app, "bob@example.com")
    _sign_in(client, "bob@example.com")

    assert reference not in client.get("/account/orders").get_data(as_text=True)


def test_the_profile_page_renders_what_is_stored(signed_in, db, harissa):
    signed_in.post(
        "/account/",
        data={
            "display_name": "Ada L",
            "delivery_address": "1 Test Street",
            "preference": ["chilli"],
        },
    )

    html = signed_in.get("/account/").get_data(as_text=True)

    assert 'value="Ada L"' in html
    assert "1 Test Street" in html
    assert 'value="chilli"' in html
    # The checkbox for a saved flag comes back checked.
    assert "checked" in html


def test_a_customer_with_no_history_is_told_so_rather_than_shown_nothing(
    signed_in,
):
    assert "not placed an order yet" in signed_in.get("/account/orders").get_data(
        as_text=True
    )
    assert "Nothing has been charged" in signed_in.get(
        "/account/balance"
    ).get_data(as_text=True)


def test_a_saved_flag_the_catalogue_no_longer_offers_is_still_shown(
    signed_in, db, harissa
):
    """A stored default still narrows browsing, so the form still shows it."""
    db["users"].update_one(
        {"email": "ada@example.com"},
        {"$set": {"default_preference_filters": ["retired_flag"]}},
    )

    html = signed_in.get("/account/").get_data(as_text=True)

    assert 'value="retired_flag"' in html
