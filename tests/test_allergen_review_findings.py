"""Regressions found in review of the allergen editor slice.

Each test here failed before the fix beside it.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.db.repositories._common import parse_many, parse_one
from app.models.allergens import AllergenBlock, AllergenCode, GlutenCereal
from app.models.catalogue import Component
from app.models.orders import Order
from app.models.users import Role, User
from app.security.passwords import hash_password

PASSWORD = "a-long-enough-passphrase"
REVIEWED_AT = datetime(2026, 3, 1, tzinfo=timezone.utc)
REVIEWED = {"reviewed_at": REVIEWED_AT, "reviewed_by": "chef"}


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
def signed_in(client, chef):
    client.post("/login", data={"email": "chef@example.com", "password": PASSWORD})
    return client


def _component(db, **overrides) -> str:
    document = {
        "name": "Harissa",
        "slug": "harissa",
        "category": "sauce",
        "price_cents": 850,
        "unit": "250ml",
        "ingredients": [{"name": "Red peppers", "quantity": "6"}],
    }
    document.update(overrides)
    return str(
        db["components"].insert_one(Component.model_validate(document).to_mongo())
        .inserted_id
    )


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# --- a stored declaration stays readable ------------------------------------
#
# The sulphites biconditional is a rule for the *write* path. A block
# stored under the previous schema could legally carry `sulphites` in
# `contains` with the flag never set, and refusing to parse it is the
# same failure the retired-vocabulary slice exists to prevent: a record
# a customer was given, unreadable because the vocabulary moved.


def test_a_stored_block_missing_the_threshold_flag_still_parses():
    document = {"contains": ["sulphites", "milk"], **REVIEWED}

    block = parse_one(AllergenBlock, document)

    # Authored order, as stored — nothing here re-sorts a declaration.
    assert block.contains == [AllergenCode.SULPHITES, AllergenCode.MILK]
    assert block.sulphites_declared is False
    # And the page still does not invent a threshold it was never told.
    assert block.sulphites_threshold_note is None


def test_a_stored_block_with_the_flag_and_no_entry_still_parses():
    block = parse_one(AllergenBlock, {"sulphites_declared": True, **REVIEWED})

    assert block.contains == []
    assert block.sulphites_threshold_note is None


def test_one_incoherent_snapshot_does_not_take_out_the_whole_list():
    """`parse_many` isolates nothing, so a refusal here is a 500 on a
    customer's whole order history, not one bad row."""
    documents = [
        {"contains": ["milk"], **REVIEWED},
        {"contains": ["sulphites"], **REVIEWED},
        {"contains": ["egg"], **REVIEWED},
    ]

    blocks = parse_many(AllergenBlock, documents)

    assert len(blocks) == 3


def test_a_stored_order_snapshot_in_that_state_still_reads(app, db):
    from app.db.repositories import orders as orders_repo

    db["orders"].insert_one(
        {
            "user_id": "u1",
            "reference": "MP-2609-0001",
            "status": "placed",
            "lines": [
                {
                    "item_type": "component",
                    "item_id": "c1",
                    "name_snapshot": "Pickled onions",
                    "unit_price_cents": 500,
                    "quantity": 1,
                    "line_total_cents": 500,
                    "allergen_snapshot": {"contains": ["sulphites"], **REVIEWED},
                }
            ],
            "subtotal_cents": 500,
            "total_cents": 500,
            "created_at": REVIEWED_AT,
            "updated_at": REVIEWED_AT,
        }
    )

    with app.app_context():
        orders = orders_repo.list_orders("u1")

    assert len(orders) == 1
    assert orders[0].lines[0].allergen_snapshot.contains == [AllergenCode.SULPHITES]


def test_the_write_path_still_refuses_both_directions():
    """Unchanged: only a *stored* document is read leniently."""
    with pytest.raises(ValidationError, match="sulphites"):
        AllergenBlock(contains=[AllergenCode.SULPHITES], **REVIEWED)
    with pytest.raises(ValidationError, match="sulphites"):
        AllergenBlock(sulphites_declared=True, **REVIEWED)


def test_the_editor_flags_a_stored_block_whose_mirror_disagrees(signed_in, db):
    # Written straight to the collection: the model refuses to build one,
    # which is the point — this state can only predate the rule.
    from bson import ObjectId

    item_id = _component(db)
    db["components"].update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"allergens": {"contains": ["sulphites"], **REVIEWED}}},
    )

    text = _text(
        signed_in.get(f"/chef/components/{item_id}/allergens").get_data(as_text=True)
    )

    assert "threshold" in text.lower()
    assert "does not match" in text.lower()


# --- wheat is declarable in its own right ------------------------------------
#
# Schedule 9 item 3: `wheat`, and `gluten` as well where gluten is
# present. Declaring only the gluten renders "Gluten (wheat)" and no
# wheat declaration, which is the exact under-declaration the split was
# made to prevent.


def test_gluten_from_wheat_requires_the_wheat_declaration():
    with pytest.raises(ValidationError, match="wheat"):
        AllergenBlock(
            contains=[AllergenCode.GLUTEN],
            gluten_cereals=[GlutenCereal.WHEAT],
            **REVIEWED,
        )


def test_wheat_and_gluten_together_are_accepted():
    block = AllergenBlock(
        contains=[AllergenCode.WHEAT, AllergenCode.GLUTEN],
        gluten_cereals=[GlutenCereal.WHEAT],
        **REVIEWED,
    )

    assert block.contains_labels == ["Wheat", "Gluten (wheat)"]


def test_gluten_from_other_cereals_does_not_require_wheat():
    block = AllergenBlock(
        contains=[AllergenCode.GLUTEN],
        gluten_cereals=[GlutenCereal.BARLEY, GlutenCereal.RYE],
        **REVIEWED,
    )

    assert AllergenCode.WHEAT not in block.contains


def test_a_retired_block_naming_wheat_is_not_held_to_the_new_rule():
    """It was written under the vocabulary that could not express it."""
    block = parse_one(
        AllergenBlock,
        {"contains": ["cereals_gluten"], "gluten_cereals": ["wheat"], **REVIEWED},
    )

    assert block.contains_labels == ["Cereals containing gluten (wheat)"]


def test_the_editor_refuses_gluten_from_wheat_without_wheat(signed_in, db):
    item_id = _component(db)

    response = signed_in.post(
        f"/chef/components/{item_id}/allergens",
        data={"confirm": "1", "contains": ["gluten"], "gluten_cereal": ["wheat"]},
    )

    assert response.status_code == 400
    from bson import ObjectId

    stored = db["components"].find_one({"_id": ObjectId(item_id)})["allergens"]
    assert stored["reviewed_at"] is None


# --- nothing from a retired block is pre-ticked ------------------------------


def test_a_retired_blocks_cereals_are_not_pre_ticked(signed_in, db):
    """The contains boxes are already left clear; the cereal boxes were
    not, so the chef could tick Gluten and save a half-carried-over
    translation."""
    item_id = _component(
        db,
        allergens={
            "contains": ["cereals_gluten"],
            "gluten_cereals": ["wheat"],
            **REVIEWED,
        },
    )

    html = signed_in.get(f"/chef/components/{item_id}/allergens").get_data(
        as_text=True
    )

    assert "checked" not in html.split('id="cereal-wheat"')[1].split(">")[0]


def test_a_live_blocks_cereals_are_still_pre_ticked(signed_in, db):
    item_id = _component(
        db,
        allergens={
            "contains": ["wheat", "gluten"],
            "gluten_cereals": ["wheat"],
            **REVIEWED,
        },
    )

    html = signed_in.get(f"/chef/components/{item_id}/allergens").get_data(
        as_text=True
    )

    assert "checked" in html.split('id="cereal-wheat"')[1].split(">")[0]


# --- a review is a review of the ingredients that were on screen -------------


def test_a_review_against_ingredients_that_have_since_changed_is_refused(
    signed_in, db
):
    """Otherwise the new `reviewed_at` is later than
    `ingredients_updated_at` and the declaration reads as current, when
    the chef confirmed it against a list that no longer exists."""
    from bson import ObjectId

    item_id = _component(db, ingredients_updated_at=REVIEWED_AT)
    page = signed_in.get(f"/chef/components/{item_id}/allergens").get_data(
        as_text=True
    )
    stamp = re.search(r'name="ingredients_stamp" value="([^"]*)"', page).group(1)

    # Another tab edits the ingredients while this page sits open.
    db["components"].update_one(
        {"_id": ObjectId(item_id)},
        {
            "$set": {
                "ingredients": [{"name": "Almonds", "is_optional": False}],
                "ingredients_updated_at": REVIEWED_AT + timedelta(days=1),
            }
        },
    )

    response = signed_in.post(
        f"/chef/components/{item_id}/allergens",
        data={"confirm": "1", "contains": ["milk"], "ingredients_stamp": stamp},
    )

    assert response.status_code == 400
    assert db["components"].find_one({"_id": ObjectId(item_id)})["allergens"][
        "reviewed_at"
    ] is None
    assert "ingredients changed" in _text(response.get_data(as_text=True)).lower()
    # The re-render shows what they now have to review against.
    assert "Almonds" in response.get_data(as_text=True)


def test_a_review_of_unchanged_ingredients_is_accepted(signed_in, db):
    from bson import ObjectId

    item_id = _component(db, ingredients_updated_at=REVIEWED_AT)
    page = signed_in.get(f"/chef/components/{item_id}/allergens").get_data(
        as_text=True
    )
    stamp = re.search(r'name="ingredients_stamp" value="([^"]*)"', page).group(1)

    signed_in.post(
        f"/chef/components/{item_id}/allergens",
        data={"confirm": "1", "contains": ["milk"], "ingredients_stamp": stamp},
    )

    stored = db["components"].find_one({"_id": ObjectId(item_id)})["allergens"]
    assert stored["reviewed_at"] is not None


def test_an_item_never_edited_carries_an_empty_stamp_and_saves(signed_in, db):
    from bson import ObjectId

    item_id = _component(db)
    page = signed_in.get(f"/chef/components/{item_id}/allergens").get_data(
        as_text=True
    )
    stamp = re.search(r'name="ingredients_stamp" value="([^"]*)"', page).group(1)
    assert stamp == ""

    signed_in.post(
        f"/chef/components/{item_id}/allergens",
        data={"confirm": "1", "contains": ["milk"], "ingredients_stamp": stamp},
    )

    stored = db["components"].find_one({"_id": ObjectId(item_id)})["allergens"]
    assert stored["reviewed_at"] is not None


# --- every customer is reachable --------------------------------------------


def test_the_customer_index_pages_rather_than_dropping_the_rest(
    signed_in, db, monkeypatch
):
    """A bound with no way past it leaves later customers with no route
    to their ledger at all, which is the page's whole purpose."""
    from app.services import chef_ledger

    monkeypatch.setattr(chef_ledger, "CUSTOMER_LIMIT", 2)
    for index in range(5):
        db["users"].insert_one(
            User(
                email=f"c{index}@example.com",
                password_hash=hash_password(PASSWORD),
                display_name=f"Customer {index}",
                role=Role.CUSTOMER,
            ).to_mongo()
        )

    first = signed_in.get("/chef/customers").get_data(as_text=True)
    assert "Customer 0" in first and "Customer 1" in first
    assert "Customer 2" not in first
    assert "page=2" in first

    second = signed_in.get("/chef/customers?page=2").get_data(as_text=True)
    assert "Customer 2" in second and "Customer 3" in second
    assert "page=1" in second

    third = signed_in.get("/chef/customers?page=3").get_data(as_text=True)
    assert "Customer 4" in third
    assert "page=4" not in third


def test_a_page_beyond_the_end_is_empty_not_an_error(signed_in, db):
    response = signed_in.get("/chef/customers?page=99")

    assert response.status_code == 200


def test_a_nonsense_page_reads_as_the_first(signed_in, db):
    assert signed_in.get("/chef/customers?page=banana").status_code == 200
    assert signed_in.get("/chef/customers?page=-3").status_code == 200
