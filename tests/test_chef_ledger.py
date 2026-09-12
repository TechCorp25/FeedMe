"""The chef's customer ledger.

04-WORKFLOWS.md: "View entries, add manual `adjustment` or `credit`
entries with a description. Entries are append-only; corrections are new
offsetting entries, never edits or deletes."

Append-onlyness is tested as the absence of a route, not the absence of a
button: a page that hides an edit control while the route still answers
is a ledger that can be rewritten.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.repositories import ledger as ledger_repo
from app.models.catalogue import Component
from app.models.orders import LedgerEntry, LedgerEntryType
from app.models.users import Role, User
from app.security.passwords import hash_password
from app.services import accounts, chef_ledger

PASSWORD = "a-long-enough-passphrase"
REVIEWED = {
    "contains": ["milk"],
    "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef",
}


@pytest.fixture()
def chef(db):
    user = User(
        email="chef@example.com",
        password_hash=hash_password(PASSWORD),
        display_name="Chef",
        role=Role.CHEF_ADMIN,
    )
    db["users"].insert_one(user.to_mongo())
    return user


@pytest.fixture()
def customer(app, db) -> str:
    with app.app_context():
        accounts.register_customer(
            email="ada@example.com",
            password=PASSWORD,
            password_confirmation=PASSWORD,
            display_name="Ada Chesterfield",
        )
    return str(db["users"].find_one({"email": "ada@example.com"})["_id"])


@pytest.fixture()
def signed_in(client, chef):
    client.post("/login", data={"email": "chef@example.com", "password": PASSWORD})
    return client


def _entry(db, user_id, *, entry_type, cents, description="An entry"):
    entry = LedgerEntry(
        user_id=user_id,
        entry_type=entry_type,
        amount_cents=cents,
        description=description,
        created_by="system",
    )
    db["account_ledger"].insert_one(entry.to_mongo())


# --- the page belongs to the chef -------------------------------------------


def test_the_ledger_requires_the_chef(client, app, db, customer):
    assert client.get(f"/chef/customers/{customer}/ledger").status_code == 302

    client.post("/login", data={"email": "ada@example.com", "password": PASSWORD})
    # Even the customer whose ledger it is: this is the chef's page, and
    # /account/balance is theirs.
    assert client.get(f"/chef/customers/{customer}/ledger").status_code == 404
    assert client.post(
        f"/chef/customers/{customer}/ledger",
        data={"entry_type": "credit", "amount": "5.00", "description": "x"},
    ).status_code == 404


def test_an_unknown_customer_is_a_404(signed_in):
    assert signed_in.get("/chef/customers/not-an-id/ledger").status_code == 404


# --- append-only ------------------------------------------------------------


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_there_is_no_route_that_edits_or_deletes_an_entry(
    signed_in, db, customer, method
):
    """Tested as the absence of a route, not a hidden button."""
    _entry(db, customer, entry_type=LedgerEntryType.CHARGE, cents=2500)
    entry_id = str(db["account_ledger"].find_one({})["_id"])

    response = getattr(signed_in, method)(
        f"/chef/customers/{customer}/ledger/{entry_id}"
    )

    assert response.status_code in (404, 405)
    assert db["account_ledger"].count_documents({}) == 1


def test_the_url_map_offers_no_ledger_mutation_beyond_appending(app):
    ledger_rules = [
        (str(rule), sorted(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
        if "ledger" in str(rule)
    ]

    # One path, and only the two methods that read and append. Flask
    # registers the GET and the POST as separate rules.
    assert sorted(ledger_rules) == [
        ("/chef/customers/<user_id>/ledger", ["GET"]),
        ("/chef/customers/<user_id>/ledger", ["POST"]),
    ]


def test_a_correction_is_a_new_offsetting_entry(signed_in, db, customer):
    _entry(
        db, customer, entry_type=LedgerEntryType.CHARGE, cents=2500,
        description="Order MP-2609-0001",
    )

    signed_in.post(
        f"/chef/customers/{customer}/ledger",
        data={
            "entry_type": "adjustment",
            "amount": "25.00",
            "direction": "owes_less",
            "description": "Charged twice for MP-2609-0001.",
        },
    )

    entries = list(db["account_ledger"].find({}).sort("created_at", 1))
    # Both rows stand: what happened, and what was put right.
    assert len(entries) == 2
    assert entries[0]["amount_cents"] == 2500
    assert entries[1]["amount_cents"] == -2500
    assert sum(e["amount_cents"] for e in entries) == 0


# --- what may be entered by hand --------------------------------------------


def test_a_charge_cannot_be_entered_by_hand(signed_in, db, customer):
    """A charge is written against an order. Typed here it owes nothing."""
    response = signed_in.post(
        f"/chef/customers/{customer}/ledger",
        data={
            "entry_type": "charge",
            "amount": "25.00",
            "direction": "owes_more",
            "description": "A charge",
        },
    )

    assert response.status_code == 400
    assert "Only a credit or an adjustment" in response.get_data(as_text=True)
    assert db["account_ledger"].count_documents({}) == 0


def test_the_form_does_not_offer_a_charge(signed_in, customer):
    page = signed_in.get(f"/chef/customers/{customer}/ledger").get_data(as_text=True)

    assert 'value="credit"' in page
    assert 'value="adjustment"' in page
    assert 'value="charge"' not in page


def test_a_credit_always_reduces_what_is_owed(signed_in, db, customer):
    """Even when the chef picks the other direction by mistake."""
    signed_in.post(
        f"/chef/customers/{customer}/ledger",
        data={
            "entry_type": "credit",
            "amount": "12.00",
            "direction": "owes_more",
            "description": "Goodwill on a late delivery.",
        },
    )

    entry = db["account_ledger"].find_one({})
    assert entry["amount_cents"] == -1200
    assert entry["entry_type"] == "credit"


def test_an_adjustment_goes_the_way_the_chef_said(signed_in, db, customer):
    signed_in.post(
        f"/chef/customers/{customer}/ledger",
        data={
            "entry_type": "adjustment", "amount": "8.00",
            "direction": "owes_more", "description": "Extra portion added.",
        },
    )
    signed_in.post(
        f"/chef/customers/{customer}/ledger",
        data={
            "entry_type": "adjustment", "amount": "3.00",
            "direction": "owes_less", "description": "Overcharged on delivery.",
        },
    )

    amounts = [e["amount_cents"] for e in db["account_ledger"].find({})]
    assert sorted(amounts) == [-300, 800]


def test_an_adjustment_with_no_direction_is_refused(signed_in, db, customer):
    response = signed_in.post(
        f"/chef/customers/{customer}/ledger",
        data={"entry_type": "adjustment", "amount": "8.00", "description": "x"},
    )

    assert response.status_code == 400
    assert "adds to or reduces" in response.get_data(as_text=True)
    assert db["account_ledger"].count_documents({}) == 0


def test_an_entry_must_say_what_it_is_for(signed_in, db, customer):
    """The only record of why the balance moved."""
    response = signed_in.post(
        f"/chef/customers/{customer}/ledger",
        data={"entry_type": "credit", "amount": "5.00", "description": "   "},
    )

    assert response.status_code == 400
    assert db["account_ledger"].count_documents({}) == 0


def test_the_entry_records_who_wrote_it(signed_in, db, customer, chef):
    signed_in.post(
        f"/chef/customers/{customer}/ledger",
        data={"entry_type": "credit", "amount": "5.00", "description": "Goodwill."},
    )

    entry = db["account_ledger"].find_one({})
    chef_id = str(db["users"].find_one({"email": "chef@example.com"})["_id"])
    assert entry["created_by"] == chef_id
    assert entry["user_id"] == customer


def test_a_refusal_keeps_what_was_typed(signed_in, customer):
    response = signed_in.post(
        f"/chef/customers/{customer}/ledger",
        data={
            "entry_type": "credit", "amount": "not a number",
            "description": "A long explanation nobody wants to retype.",
        },
    )

    page = response.get_data(as_text=True)
    assert "not a number" in page
    assert "A long explanation nobody wants to retype." in page


# --- amounts are integer cents ----------------------------------------------


@pytest.mark.parametrize(
    "typed,cents", [("14.50", 1450), ("14.5", 1450), ("7", 700), ("$1,200", 120000)]
)
def test_an_amount_becomes_integer_cents(typed, cents):
    assert chef_ledger.parse_amount_cents(typed) == cents


@pytest.mark.parametrize("typed", ["", "0", "0.00", "abc", "14.505", "-5"])
def test_an_amount_that_is_not_one_is_refused(typed):
    with pytest.raises(chef_ledger.LedgerEntryError):
        chef_ledger.parse_amount_cents(typed)


def test_a_negative_amount_says_to_use_the_direction_instead():
    with pytest.raises(chef_ledger.LedgerEntryError) as caught:
        chef_ledger.parse_amount_cents("-5.00")

    assert "positive number" in str(caught.value)


# --- the running balance ----------------------------------------------------


def test_the_balance_is_the_sum_over_every_entry(signed_in, db, customer, app):
    _entry(db, customer, entry_type=LedgerEntryType.CHARGE, cents=2500)
    _entry(db, customer, entry_type=LedgerEntryType.CREDIT, cents=-1000)

    with app.app_context():
        assert ledger_repo.balance_cents(customer) == 1500

    page = signed_in.get(f"/chef/customers/{customer}/ledger").get_data(as_text=True)
    assert "$15.00" in page


def test_the_window_is_the_newest_entries_not_the_oldest(
    signed_in, db, customer, app, monkeypatch
):
    """Sorting ascending then limiting pins a long account to its first page.

    The chef looking a customer up to correct this morning's charge would
    otherwise be shown entries from the year they signed up.
    """
    monkeypatch.setattr(chef_ledger, "LEDGER_LIMIT", 3)
    for index in range(6):
        _entry(
            db, customer, entry_type=LedgerEntryType.CHARGE, cents=100,
            description=f"Entry {index}",
        )

    with app.app_context():
        from app.models.users import User as UserModel

        person = UserModel.model_validate(
            db["users"].find_one({"_id": __import__("bson").ObjectId(customer)})
        )
        view = chef_ledger.customer_ledger(person)

    assert [row.entry.description for row in view.rows] == [
        "Entry 3", "Entry 4", "Entry 5",
    ]
    # The closing balance still counts every entry, and the last running
    # total equals it.
    assert view.balance_cents == 600
    assert view.rows[-1].balance_cents == 600
    assert view.opening_balance_cents == 300


def test_the_direction_of_every_row_is_stated_in_words(signed_in, db, customer):
    """A minus sign is one character; the words survive a glance."""
    _entry(db, customer, entry_type=LedgerEntryType.CHARGE, cents=2500)
    _entry(db, customer, entry_type=LedgerEntryType.CREDIT, cents=-1000)

    page = signed_in.get(f"/chef/customers/{customer}/ledger").get_data(as_text=True)

    assert "Added to what they owe" in page
    assert "Reduced what they owe" in page


# --- reachable, and JavaScript-free -----------------------------------------


def test_the_order_queue_links_to_the_customers_ledger(
    signed_in, client, app, db, customer, chef
):
    item = Component.model_validate(
        {
            "name": "Labneh", "slug": "labneh", "category": "sauce",
            "price_cents": 850, "unit": "250ml", "is_available": True,
            "allergens": dict(REVIEWED),
        }
    )
    item_id = str(db["components"].insert_one(item.to_mongo()).inserted_id)

    signed_in.post("/logout")
    client.post("/login", data={"email": "ada@example.com", "password": PASSWORD})
    client.post(
        "/cart/add",
        data={"item_type": "component", "item_id": item_id, "quantity": "1"},
    )
    page = client.get("/checkout").get_data(as_text=True)
    hidden = lambda name: (  # noqa: E731
        re.search(rf'name="{name}" value="([^"]*)"', page) or [None, ""]
    )[1]
    client.post(
        "/checkout",
        data={
            "requested_for": (date.today() + timedelta(days=1)).isoformat(),
            "fulfilment": "collection",
            "checkout_token": hidden("checkout_token"),
            "review_digest": hidden("review_digest"),
        },
    )
    client.post("/logout")
    client.post("/login", data={"email": "chef@example.com", "password": PASSWORD})

    queue = client.get("/chef/orders").get_data(as_text=True)
    assert f"/chef/customers/{customer}/ledger" in queue


def test_the_ledger_page_needs_no_javascript(signed_in, customer):
    page = signed_in.get(f"/chef/customers/{customer}/ledger").get_data(as_text=True)

    assert "onclick" not in page
    assert 'name="csrf_token"' in page
    # Said on the page, not only enforced in the routes.
    assert "append-only" in page
