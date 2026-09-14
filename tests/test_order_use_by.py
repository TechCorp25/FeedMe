"""The storage snapshot on `OrderLine`, and the use-by it supports.

04-WORKFLOWS.md computes the customer's use-by as
`prepared_at + shelf_life_days`, shortest across lines. Before this,
`OrderLine` snapshotted the name, the price and the allergen block but
not the storage block, so the shelf life was only readable from the
catalogue as it stands now — which the chef may have edited since. The
order page pointed at the item's page rather than naming a date it could
not stand behind.

The whole `StorageBlock` travels with the line, and the date is computed
only when every line carries one.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.catalogue import Component
from app.models.orders import OrderStatus
from app.services import accounts

PASSWORD = "a-long-enough-passphrase"

REVIEWED = {
    "contains": ["milk"],
    "may_contain": [],
    "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef",
}

FRIDGE = {
    "method": "refrigerate",
    "temperature_c": "0-4",
    "shelf_life_days": 10,
    "shelf_life_note": "3 days once opened",
    "freezable": True,
    "freezer_life_days": 90,
}


def _component(db, *, name: str, slug: str, storage: dict | None) -> str:
    document = {
        "name": name,
        "slug": slug,
        "category": "sauce",
        "price_cents": 850,
        "unit": "250ml",
        "is_available": True,
        "allergens": dict(REVIEWED),
    }
    if storage is not None:
        document["storage"] = storage
    item = Component.model_validate(document)
    return str(db["components"].insert_one(item.to_mongo()).inserted_id)


@pytest.fixture()
def harissa(db) -> str:
    return _component(db, name="Harissa", slug="harissa", storage=dict(FRIDGE))


@pytest.fixture()
def labneh(db) -> str:
    """A shorter shelf life, so it is the one the order takes."""
    return _component(
        db,
        name="Labneh",
        slug="labneh",
        storage=dict(FRIDGE, shelf_life_days=4, shelf_life_note=None),
    )


@pytest.fixture()
def salt(db) -> str:
    """No storage block at all, so no guidance to snapshot."""
    return _component(db, name="Sea salt", slug="sea-salt", storage=None)


@pytest.fixture()
def signed_in(client, app):
    with app.app_context():
        accounts.register_customer(
            email="cook@example.com",
            password=PASSWORD,
            password_confirmation=PASSWORD,
        )
    client.post("/login", data={"email": "cook@example.com", "password": PASSWORD})
    return client


def _place_order(client, *item_ids: str) -> str:
    for item_id in item_ids:
        client.post(
            "/cart/add",
            data={"item_type": "component", "item_id": item_id, "quantity": "1"},
        )
    page = client.get("/checkout").get_data(as_text=True)

    def hidden(name: str) -> str:
        match = re.search(rf'name="{re.escape(name)}" value="([^"]*)"', page)
        return match.group(1) if match else ""

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


def _flat(html: str) -> str:
    """The page with its Jinja line breaks collapsed.

    The template wraps a sentence across several lines; a reader sees one
    run of text and the assertions are written the way the reader reads.
    """
    return re.sub(r"\s+", " ", html)


def _prepare(db, reference: str, prepared_at: datetime) -> None:
    """Stamp the order as prepared, the way entry to `ready` does."""
    db["orders"].update_one(
        {"reference": reference},
        {
            "$set": {
                "status": OrderStatus.READY.value,
                "prepared_at": prepared_at,
            }
        },
    )


# --- what is frozen onto the line -------------------------------------------


def test_the_whole_storage_block_is_snapshotted(signed_in, db, harissa):
    reference = _place_order(signed_in, harissa)

    line = db["orders"].find_one({"reference": reference})["lines"][0]

    assert line["storage_snapshot"]["method"] == "refrigerate"
    assert line["storage_snapshot"]["temperature_c"] == "0-4"
    assert line["storage_snapshot"]["shelf_life_days"] == 10
    assert line["storage_snapshot"]["shelf_life_note"] == "3 days once opened"
    assert line["storage_snapshot"]["freezable"] is True
    assert line["storage_snapshot"]["freezer_life_days"] == 90


def test_an_item_without_storage_snapshots_nothing(signed_in, db, salt):
    reference = _place_order(signed_in, salt)

    line = db["orders"].find_one({"reference": reference})["lines"][0]

    assert line["storage_snapshot"] is None


def test_a_later_catalogue_edit_does_not_move_the_snapshot(
    signed_in, db, harissa
):
    """The failure this exists to prevent.

    A shelf life read live from the catalogue would lengthen a use-by
    underneath a customer who has already been given one.
    """
    reference = _place_order(signed_in, harissa)
    db["components"].update_one(
        {"slug": "harissa"},
        {"$set": {"storage.shelf_life_days": 30, "storage.method": "freeze"}},
    )

    line = db["orders"].find_one({"reference": reference})["lines"][0]

    assert line["storage_snapshot"]["shelf_life_days"] == 10
    assert line["storage_snapshot"]["method"] == "refrigerate"


# --- the date on the order page ---------------------------------------------


def test_the_use_by_is_the_shortest_shelf_life_from_the_prepared_day(
    signed_in, db, harissa, labneh
):
    reference = _place_order(signed_in, harissa, labneh)
    _prepare(db, reference, datetime(2026, 5, 1, 2, 0, tzinfo=timezone.utc))

    html = _flat(signed_in.get(f"/account/orders/{reference}").get_data(as_text=True))

    # Prepared 1 May in Melbourne, shortest shelf life 4 days.
    assert "Use by 5 May 2026." in html
    assert "4 days" in html


def test_an_unprepared_order_names_the_shelf_life_but_no_date(
    signed_in, harissa, labneh
):
    reference = _place_order(signed_in, harissa, labneh)

    html = _flat(signed_in.get(f"/account/orders/{reference}").get_data(as_text=True))

    assert "Use by" not in html
    assert "shortest shelf life on this order is 4 days" in html


def test_a_line_without_guidance_withholds_the_date_and_says_which(
    signed_in, db, harissa, salt
):
    """The shortest of the remaining lines would not cover the order."""
    reference = _place_order(signed_in, harissa, salt)
    _prepare(db, reference, datetime(2026, 5, 1, 2, 0, tzinfo=timezone.utc))

    html = _flat(signed_in.get(f"/account/orders/{reference}").get_data(as_text=True))

    assert "Use by" not in html
    assert "No storage guidance was recorded with Sea salt" in html
    # The pointer the page carried before the snapshot existed.
    assert "each item's own page" in html


def test_a_line_written_before_the_snapshot_existed_withholds_the_date(
    signed_in, db, harissa
):
    """Nothing is backfilled. An order placed before this field existed
    has `storage_snapshot: None` on every line, and inventing one from
    today's catalogue is the retroactive edit the snapshot prevents."""
    reference = _place_order(signed_in, harissa)
    db["orders"].update_one(
        {"reference": reference}, {"$unset": {"lines.0.storage_snapshot": ""}}
    )
    _prepare(db, reference, datetime(2026, 5, 1, 2, 0, tzinfo=timezone.utc))

    html = _flat(signed_in.get(f"/account/orders/{reference}").get_data(as_text=True))

    assert "Use by" not in html
    assert "each item's own page" in html


def test_the_page_renders_the_snapshotted_guidance_not_the_catalogue(
    signed_in, db, harissa
):
    reference = _place_order(signed_in, harissa)
    db["components"].update_one(
        {"slug": "harissa"},
        {"$set": {"storage.method": "freeze", "storage.temperature_c": "-18 or below"}},
    )

    html = _flat(signed_in.get(f"/account/orders/{reference}").get_data(as_text=True))

    assert "Refrigerate" in html
    assert "-18 or below" not in html
    assert "3 days once opened" in html
