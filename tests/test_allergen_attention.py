"""Finding the items whose allergen declaration needs re-reviewing.

The wheat/gluten split shipped with no migration, deliberately: a
catalogue item keeps its declaration until the chef re-reviews it, because
a compliance record rewritten by a script is a record nobody authored.
That decision rests entirely on the chef being able to *find* the affected
items — and they could not. `uses_retired_vocabulary` and
`sulphites_mirror_disagrees` were rendered only on an item's own allergen
editor, so you had to already be looking at the item to learn it needed
looking at.

`test_a_retired_declaration_is_reported_on_the_catalogue_list` is the test
that fails before the fix.

This is a **reporting surface only**. The last group of tests is the one
that matters most: nothing here offers a control that re-declares
anything, in bulk or at all.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.models.catalogue import Component, Dish
from app.models.users import Role, User
from app.security.passwords import hash_password
from app.services import catalogue_admin

PASSWORD = "a-long-enough-passphrase"
REVIEWED_AT = datetime(2026, 3, 1, tzinfo=timezone.utc)
LATER = REVIEWED_AT + timedelta(days=2)

CLEAN = {
    "contains": ["milk"],
    "reviewed_at": REVIEWED_AT,
    "reviewed_by": "chef@example.com",
}
#: Written before the split: `cereals_gluten` is parseable and never
#: written again (01-DOMAIN.md).
RETIRED = {
    "contains": ["cereals_gluten"],
    "gluten_cereals": ["wheat"],
    "reviewed_at": REVIEWED_AT,
    "reviewed_by": "chef@example.com",
}
#: The flag set without the declaration. Unwritable now; it can only
#: predate the rule, which is why it is inserted directly.
MIRROR_DISAGREES = {
    "contains": [],
    "sulphites_declared": True,
    "reviewed_at": REVIEWED_AT,
    "reviewed_by": "chef@example.com",
}


@pytest.fixture()
def chef(db):
    db["users"].insert_one(
        User(
            email="chef@example.com",
            password_hash=hash_password(PASSWORD),
            display_name="Chef",
            role=Role.CHEF_ADMIN,
        ).to_mongo()
    )


@pytest.fixture()
def signed_in(client, chef):
    client.post("/login", data={"email": "chef@example.com", "password": PASSWORD})
    return client


def _component(db, *, name, slug, allergens=None, ingredients_at=None, archived=False):
    """Inserted as a document, because two of these states are unwritable.

    A block whose sulphites flag disagrees with its declaration, and one
    carrying retired vocabulary, are both refused by every write path —
    the allergen editor offers the live vocabulary only. They can exist
    only by predating the rule, so a test for reporting them has to
    write one the way history did.
    """
    block = dict(allergens if allergens is not None else CLEAN)
    unwritable = block is not CLEAN and block.get("sulphites_declared") and not block.get(
        "contains"
    )
    document = Component.model_validate(
        {
            "name": name, "slug": slug, "category": "sauce", "price_cents": 850,
            "unit": "250ml", "is_archived": archived,
            # A block the model refuses on write is built from a clean one
            # and then overwritten in the raw document below, which is the
            # only way such a document can exist — by predating the rule.
            "allergens": dict(CLEAN) if unwritable else block,
        }
    ).to_mongo()
    if unwritable:
        document["allergens"] = {
            **document["allergens"],
            "contains": [],
            "sulphites_declared": True,
        }
    if ingredients_at is not None:
        document["ingredients_updated_at"] = ingredients_at
    return str(db["components"].insert_one(document).inserted_id)


def _attention_block(page: str) -> str:
    """The per-item attention notice, not the summary above the list.

    Anchored on the item-level heading: the page-level notice above the
    list uses similar wording, and slicing from the first match would
    test the wrong thing.
    """
    start = page.index("admin-item__attention-heading")
    return page[start : page.index("</div>", start)]


# --- the gap this closes ----------------------------------------------------


def test_a_retired_declaration_is_reported_on_the_catalogue_list(signed_in, db):
    """You no longer have to open the item to learn it needs attention."""
    _component(db, name="Old flatbread", slug="old-flatbread", allergens=RETIRED)
    page = signed_in.get("/chef/components").get_data(as_text=True)
    assert "needs looking at" in page
    assert "predates the wheat and gluten split" in page


def test_a_disagreeing_sulphites_mirror_is_reported_on_the_list(signed_in, db):
    _component(db, name="Old wine jus", slug="old-wine-jus", allergens=MIRROR_DISAGREES)
    page = signed_in.get("/chef/components").get_data(as_text=True)
    assert "sulphites threshold flag and the declaration disagree" in page


def test_the_same_is_reported_on_the_dish_catalogue(signed_in, db):
    """Both catalogues, not just the one that happened to be built first."""
    dish = Dish.model_validate(
        {
            "name": "Old pie", "slug": "old-pie", "category": "main",
            "price_cents": 2400, "unit": "portion", "allergens": dict(RETIRED),
        }
    )
    db["dishes"].insert_one(dish.to_mongo())
    page = signed_in.get("/chef/dishes").get_data(as_text=True)
    assert "predates the wheat and gluten split" in page


# --- the four states --------------------------------------------------------


def test_an_unreviewed_item_is_reported_as_unreviewed(signed_in, db):
    _component(db, name="New sauce", slug="new-sauce", allergens={})
    page = signed_in.get("/chef/components").get_data(as_text=True)
    assert "has never been reviewed" in page
    assert "cannot be made available to customers" in page


def test_a_stale_review_is_reported_as_stale(signed_in, db):
    _component(db, name="Edited sauce", slug="edited-sauce", ingredients_at=LATER)
    page = signed_in.get("/chef/components").get_data(as_text=True)
    assert "ingredients changed after the declaration was last reviewed" in page
    # Advisory: it prompts, it does not withdraw (04-WORKFLOWS.md).
    assert "is not" in page and "withdrawn" in page


def test_an_unreviewed_item_is_never_also_called_stale(signed_in, db):
    """Unreviewed is a different state with a harder rule (01-DOMAIN.md)."""
    _component(db, name="New sauce", slug="new-sauce", allergens={},
               ingredients_at=LATER)
    page = signed_in.get("/chef/components").get_data(as_text=True)
    assert "has never been reviewed" in page
    assert "ingredients changed after the declaration" not in page


def test_an_item_can_want_attention_for_more_than_one_reason(signed_in, db):
    _component(db, name="Old flatbread", slug="old-flatbread",
               allergens=RETIRED, ingredients_at=LATER)
    page = signed_in.get("/chef/components").get_data(as_text=True)
    assert "ingredients changed after the declaration was last reviewed" in page
    assert "predates the wheat and gluten split" in page


def test_a_clean_item_is_reported_as_nothing(signed_in, db):
    _component(db, name="Fine sauce", slug="fine-sauce")
    page = signed_in.get("/chef/components").get_data(as_text=True)
    assert "needs looking at" not in page
    assert "Needs allergen attention" not in page


# --- the filter -------------------------------------------------------------


def test_the_filter_narrows_to_what_needs_attention(signed_in, db):
    _component(db, name="Fine sauce", slug="fine-sauce")
    _component(db, name="Old flatbread", slug="old-flatbread", allergens=RETIRED)
    _component(db, name="New sauce", slug="new-sauce", allergens={})

    page = signed_in.get("/chef/components?attention=1").get_data(as_text=True)
    assert "Old flatbread" in page
    assert "New sauce" in page
    assert "Fine sauce" not in page


def test_the_filter_covers_all_four_states_in_one_place(signed_in, db):
    _component(db, name="Never reviewed", slug="a-never", allergens={})
    _component(db, name="Stale review", slug="b-stale", ingredients_at=LATER)
    _component(db, name="Retired wording", slug="c-retired", allergens=RETIRED)
    _component(db, name="Bad mirror", slug="d-mirror", allergens=MIRROR_DISAGREES)
    _component(db, name="Perfectly fine", slug="e-fine")

    page = signed_in.get("/chef/components?attention=1").get_data(as_text=True)
    for name in ("Never reviewed", "Stale review", "Retired wording", "Bad mirror"):
        assert name in page, name
    assert "Perfectly fine" not in page


def test_the_count_is_shown_before_the_filter_is_used(signed_in, db):
    _component(db, name="Old flatbread", slug="old-flatbread", allergens=RETIRED)
    _component(db, name="New sauce", slug="new-sauce", allergens={})
    _component(db, name="Fine sauce", slug="fine-sauce")

    page = signed_in.get("/chef/components").get_data(as_text=True)
    assert "Needs allergen attention (2)" in page
    # The sentence wraps in the template, so this checks a fragment that
    # sits on one line rather than one that straddles the break.
    assert "components in this" in page


def test_the_control_is_a_link_so_it_works_without_javascript(signed_in, db):
    _component(db, name="Old flatbread", slug="old-flatbread", allergens=RETIRED)
    page = signed_in.get("/chef/components").get_data(as_text=True)
    assert re.search(r'<a[^>]+href="[^"]*attention=1[^"]*"', page)


def test_the_filter_composes_with_show_archived(signed_in, db):
    """Each keeps the other rather than resetting it."""
    _component(db, name="Archived and retired", slug="arch-retired",
               allergens=RETIRED, archived=True)
    _component(db, name="Live and retired", slug="live-retired", allergens=RETIRED)

    plain = signed_in.get("/chef/components?attention=1").get_data(as_text=True)
    assert "Live and retired" in plain
    assert "Archived and retired" not in plain

    both = signed_in.get("/chef/components?attention=1&archived=1").get_data(
        as_text=True
    )
    assert "Live and retired" in both
    assert "Archived and retired" in both
    # And the link out of each keeps the other flag.
    assert "attention=1" in both and "archived=1" in both


def test_an_empty_filtered_list_says_so_rather_than_offering_to_create_one(
    signed_in, db
):
    _component(db, name="Fine sauce", slug="fine-sauce")
    page = signed_in.get("/chef/components?attention=1").get_data(as_text=True)
    assert "No component needs allergen attention" in page
    assert "Create the first one" not in page


def test_an_action_from_a_filtered_list_returns_to_that_list(signed_in, db):
    item_id = _component(db, name="Old flatbread", slug="old-flatbread",
                         allergens=RETIRED)
    response = signed_in.post(
        f"/chef/components/{item_id}/archive",
        data={"archive": "1", "attention_only": "1"},
    )
    assert response.status_code == 302
    assert "attention=1" in response.headers["Location"]


# --- it reports, and it changes nothing -------------------------------------


def test_the_list_offers_no_control_that_re_declares_anything(signed_in, db):
    """A reporting surface. Every declaration is authored one at a time."""
    _component(db, name="Old flatbread", slug="old-flatbread", allergens=RETIRED)
    _component(db, name="Bad mirror", slug="bad-mirror", allergens=MIRROR_DISAGREES)

    page = signed_in.get("/chef/components?attention=1").get_data(as_text=True)
    actions = re.findall(r'<form[^>]+action="([^"]+)"', page)
    assert actions, "no forms at all — the test would pass vacuously"
    # Not one form on this page posts to an allergen route.
    assert not [action for action in actions if "allergens" in action]
    # The way to a declaration is a link to its own editor, and nothing else.
    assert re.search(r'<a[^>]+href="[^"]*/allergens"', page)
    assert "changes a declaration" in page


def test_rendering_the_list_never_writes_an_allergen_field(signed_in, db):
    """Reporting a block must not repair it (01-DOMAIN.md)."""
    item_id = _component(db, name="Bad mirror", slug="bad-mirror",
                         allergens=MIRROR_DISAGREES)
    from bson import ObjectId

    before = db["components"].find_one({"_id": ObjectId(item_id)})["allergens"]
    signed_in.get("/chef/components")
    signed_in.get("/chef/components?attention=1")
    after = db["components"].find_one({"_id": ObjectId(item_id)})["allergens"]
    assert after == before


def test_the_reasons_are_words_and_not_a_colour(signed_in, db):
    """03-FRONTEND.md: no distinction is ever carried by colour alone."""
    _component(db, name="Old flatbread", slug="old-flatbread", allergens=RETIRED)
    page = signed_in.get("/chef/components").get_data(as_text=True)
    block = _attention_block(page)
    # The reason is a sentence, not a dot with a tooltip.
    assert "predates the wheat and gluten split" in block


def test_no_preference_flag_or_spice_level_appears_in_the_attention_block(
    signed_in, db
):
    """They never appear on an allergen surface (01-DOMAIN.md)."""
    document = Component.model_validate(
        {
            "name": "Hot old sauce", "slug": "hot-old-sauce", "category": "sauce",
            "price_cents": 850, "unit": "250ml", "spice_level": 4,
            "preference_flags": ["chilli"], "allergens": dict(RETIRED),
        }
    ).to_mongo()
    db["components"].insert_one(document)

    block = _attention_block(signed_in.get("/chef/components").get_data(as_text=True))
    assert "Chilli" not in block
    assert "Very hot" not in block


# --- the service ------------------------------------------------------------


def test_the_count_is_taken_before_the_filter_narrows_the_list(app, db):
    """A narrowed page still knows the whole figure."""
    _component(db, name="Fine sauce", slug="fine-sauce")
    _component(db, name="Old flatbread", slug="old-flatbread", allergens=RETIRED)

    with app.app_context():
        narrowed = catalogue_admin.admin_list("component", attention_only=True)
        assert len(narrowed.rows) == 1
        assert narrowed.attention_count == 1

        whole = catalogue_admin.admin_list("component")
        assert len(whole.rows) == 2
        assert whole.attention_count == 1
