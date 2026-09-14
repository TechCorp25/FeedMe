"""The chef allergen editor.

04-WORKFLOWS.md makes it a deliberately separate step, not a section of
the catalogue form: opened explicitly from the item editor, requiring the
chef to confirm the declaration before saving, setting `reviewed_at` and
`reviewed_by`, showing the rollup warning as advice and never applying
it, and clearing a stale review by re-reviewing.

01-DOMAIN.md makes it the *only* code path that writes an
`AllergenBlock`. Most of what is asserted here is what this form is not
allowed to do: infer a declaration, apply a linked component's, write one
without a confirmation, or accept a word the standard no longer uses.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.models.catalogue import Component, Dish
from app.models.users import Role, User
from app.security.passwords import hash_password

PASSWORD = "a-long-enough-passphrase"
REVIEWED_AT = datetime(2026, 3, 1, tzinfo=timezone.utc)


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
    item = Component.model_validate(document)
    return str(db["components"].insert_one(item.to_mongo()).inserted_id)


def _dish(db, **overrides) -> str:
    document = {
        "name": "Satay bowl",
        "slug": "satay-bowl",
        "category": "bowl",
        "price_cents": 1850,
        "serves": 1,
    }
    document.update(overrides)
    item = Dish.model_validate(document)
    return str(db["dishes"].insert_one(item.to_mongo()).inserted_id)


def _declare(client, item_id, *, plural="components", **fields):
    """Declare the way the chef does: read the page, then post it back.

    The form pins a review to the ingredient list it was made against, so
    a post that skips the page skips the stamp — and is refused, exactly
    as a save from a tab left open while the ingredients changed is.
    """
    page = client.get(f"/chef/{plural}/{item_id}/allergens").get_data(as_text=True)
    stamp = re.search(r'name="ingredients_stamp" value="([^"]*)"', page)
    data = {"confirm": "1", "ingredients_stamp": stamp.group(1) if stamp else ""}
    data.update(fields)
    return client.post(f"/chef/{plural}/{item_id}/allergens", data=data)


def _block(db, item_id, collection="components") -> dict:
    from bson import ObjectId

    return db[collection].find_one({"_id": ObjectId(item_id)})["allergens"]


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# --- the page belongs to the chef -------------------------------------------


def test_the_editor_requires_the_chef(client, db):
    item_id = _component(db)
    assert client.get(f"/chef/components/{item_id}/allergens").status_code == 302


def test_a_customer_gets_404_not_403(client, app, db):
    from app.services import accounts

    item_id = _component(db)
    with app.app_context():
        accounts.register_customer(
            email="ada@example.com",
            password=PASSWORD,
            password_confirmation=PASSWORD,
        )
    client.post("/login", data={"email": "ada@example.com", "password": PASSWORD})

    assert client.get(f"/chef/components/{item_id}/allergens").status_code == 404


def test_an_item_that_does_not_exist_is_a_404(signed_in):
    from bson import ObjectId

    assert (
        signed_in.get(f"/chef/components/{ObjectId()}/allergens").status_code == 404
    )


# --- it is reached from the item editor -------------------------------------


def test_the_item_editor_links_to_it(signed_in, db):
    item_id = _component(db)

    html = signed_in.get(f"/chef/components/{item_id}/edit").get_data(as_text=True)

    assert f"/chef/components/{item_id}/allergens" in html
    # And still says it does not touch allergens itself.
    assert "Allergens are edited separately" in html


def test_the_catalogue_list_links_to_it(signed_in, db):
    item_id = _component(db)

    html = signed_in.get("/chef/components").get_data(as_text=True)

    assert f"/chef/components/{item_id}/allergens" in html


# --- saving is reviewing ----------------------------------------------------


def test_a_save_stamps_who_reviewed_it_and_when(signed_in, db):
    item_id = _component(db)
    before = datetime.now(timezone.utc)

    _declare(signed_in, item_id, contains=["milk"])

    block = _block(db, item_id)
    assert block["contains"] == ["milk"]
    assert block["reviewed_by"] == "chef@example.com"
    assert block["reviewed_at"] is not None
    assert block["reviewed_at"] >= before


def test_a_save_without_the_confirmation_writes_nothing(signed_in, db):
    item_id = _component(db)

    response = signed_in.post(
        f"/chef/components/{item_id}/allergens", data={"contains": ["milk"]}
    )

    assert response.status_code == 400
    assert _block(db, item_id)["reviewed_at"] is None
    assert _block(db, item_id)["contains"] == []
    assert "Confirm that the declaration is accurate" in _text(
        response.get_data(as_text=True)
    )


def test_a_refusal_keeps_what_was_ticked(signed_in, db):
    """The chef has just read an ingredients list against a set of boxes."""
    item_id = _component(db)

    response = signed_in.post(
        f"/chef/components/{item_id}/allergens",
        data={"contains": ["milk", "egg"]},
    )
    html = response.get_data(as_text=True)

    assert 'id="contains-milk"' in html
    milk = html.split('id="contains-milk"')[1].split(">")[0]
    egg = html.split('id="contains-egg"')[1].split(">")[0]
    assert "checked" in milk and "checked" in egg
    # The confirmation itself is never carried back: a box that arrives
    # already ticked confirms nothing.
    confirm = html.split('id="confirm"')[1].split(">")[0]
    assert "checked" not in confirm


def test_an_empty_declaration_is_a_declaration_once_reviewed(signed_in, db):
    item_id = _component(db)

    _declare(signed_in, item_id)

    block = _block(db, item_id)
    assert block["contains"] == []
    assert block["reviewed_at"] is not None


# --- the rules the model holds, surfaced as sentences -----------------------


def test_gluten_without_a_cereal_is_refused(signed_in, db):
    item_id = _component(db)

    response = _declare(signed_in, item_id, contains=["gluten"])

    assert response.status_code == 400
    assert "gluten_cereals" in response.get_data(as_text=True)
    assert _block(db, item_id)["reviewed_at"] is None


def test_gluten_with_a_cereal_is_accepted(signed_in, db):
    item_id = _component(db)

    _declare(signed_in, item_id, contains=["gluten"], gluten_cereal=["barley"])

    block = _block(db, item_id)
    assert block["contains"] == ["gluten"]
    assert block["gluten_cereals"] == ["barley"]


def test_tree_nuts_without_a_species_is_refused(signed_in, db):
    item_id = _component(db)

    response = _declare(signed_in, item_id, contains=["tree_nuts"])

    assert response.status_code == 400
    assert _block(db, item_id)["reviewed_at"] is None


def test_an_allergen_cannot_be_declared_and_a_risk_at_once(signed_in, db):
    item_id = _component(db)

    response = _declare(
        signed_in, item_id, contains=["peanut"], may_contain=["peanut"]
    )

    assert response.status_code == 400
    assert _block(db, item_id)["reviewed_at"] is None


# --- sulphites are one control ----------------------------------------------


def test_ticking_sulphites_writes_both_halves(signed_in, db):
    """The biconditional is unreachable through the UI, not caught by it."""
    item_id = _component(db)

    _declare(signed_in, item_id, contains=["sulphites"])

    block = _block(db, item_id)
    assert block["contains"] == ["sulphites"]
    assert block["sulphites_declared"] is True


def test_leaving_sulphites_clear_leaves_the_flag_false(signed_in, db):
    item_id = _component(db)

    _declare(signed_in, item_id, contains=["milk"])

    assert _block(db, item_id)["sulphites_declared"] is False


def test_the_form_offers_no_separate_threshold_control(signed_in, db):
    item_id = _component(db)

    html = signed_in.get(f"/chef/components/{item_id}/allergens").get_data(
        as_text=True
    )

    assert 'name="sulphites_declared"' not in html
    assert 'id="contains-sulphites"' in html


def test_a_posted_threshold_flag_is_ignored(signed_in, db):
    """The form does not offer it, so nothing reads it."""
    item_id = _component(db)

    _declare(signed_in, item_id, contains=["milk"], sulphites_declared="1")

    assert _block(db, item_id)["sulphites_declared"] is False


# --- the live vocabulary, and only it ---------------------------------------


def test_the_retired_codes_are_not_offered(signed_in, db):
    item_id = _component(db)

    html = signed_in.get(f"/chef/components/{item_id}/allergens").get_data(
        as_text=True
    )

    assert 'value="cereals_gluten"' not in html
    assert 'value="spelt"' not in html
    assert 'value="wheat"' in html
    assert 'value="gluten"' in html


def test_a_retired_code_is_refused_rather_than_dropped(signed_in, db):
    """Dropping it would save a declaration the chef did not see and say
    it succeeded."""
    item_id = _component(db)

    response = _declare(signed_in, item_id, contains=["cereals_gluten"])

    assert response.status_code == 400
    assert _block(db, item_id)["reviewed_at"] is None


def test_a_block_written_before_the_split_is_reported_not_translated(
    signed_in, db
):
    item_id = _component(
        db,
        allergens={
            "contains": ["cereals_gluten"],
            "gluten_cereals": ["spelt"],
            "reviewed_at": REVIEWED_AT,
            "reviewed_by": "chef",
        },
    )

    text = _text(
        signed_in.get(f"/chef/components/{item_id}/allergens").get_data(as_text=True)
    )

    assert "wording the standard no longer uses" in text
    # Nothing is pre-ticked on its behalf.
    html = signed_in.get(f"/chef/components/{item_id}/allergens").get_data(
        as_text=True
    )
    for marker in ('id="contains-wheat"', 'id="contains-gluten"'):
        assert "checked" not in html.split(marker)[1].split(">")[0]


# --- the rollup warning is advice -------------------------------------------


def test_a_linked_components_declaration_is_warned_about(signed_in, db):
    component_id = _component(
        db,
        name="Peanut sauce",
        slug="peanut-sauce",
        allergens={
            "contains": ["peanut"],
            "reviewed_at": REVIEWED_AT,
            "reviewed_by": "chef",
        },
    )
    dish_id = _dish(db, component_refs=[component_id])

    text = _text(
        signed_in.get(f"/chef/dishes/{dish_id}/allergens").get_data(as_text=True)
    )

    assert "Peanut sauce declares Peanut" in text
    assert "This is a prompt, not a change." in text


def test_the_warning_never_applies_itself(signed_in, db):
    """01-DOMAIN.md: the chef resolves it manually, always."""
    component_id = _component(
        db,
        name="Peanut sauce",
        slug="peanut-sauce",
        allergens={
            "contains": ["peanut"],
            "reviewed_at": REVIEWED_AT,
            "reviewed_by": "chef",
        },
    )
    dish_id = _dish(db, component_refs=[component_id])

    _declare(signed_in, dish_id, plural="dishes", contains=["milk"])

    block = _block(db, dish_id, "dishes")
    assert block["contains"] == ["milk"]
    assert "peanut" not in block["contains"]


def test_a_component_has_no_rollup(signed_in, db):
    item_id = _component(db)

    text = _text(
        signed_in.get(f"/chef/components/{item_id}/allergens").get_data(as_text=True)
    )

    assert "Linked components declare more" not in text


# --- staleness --------------------------------------------------------------


def test_a_stale_review_is_prompted_and_the_item_stays_published(signed_in, db):
    item_id = _component(
        db,
        is_available=True,
        allergens={
            "contains": ["milk"],
            "reviewed_at": REVIEWED_AT,
            "reviewed_by": "chef",
        },
        ingredients_updated_at=REVIEWED_AT + timedelta(days=1),
    )

    text = _text(
        signed_in.get(f"/chef/components/{item_id}/allergens").get_data(as_text=True)
    )

    assert "The ingredients changed after that review." in text
    assert "still published" in text
    from bson import ObjectId

    assert db["components"].find_one({"_id": ObjectId(item_id)})["is_available"] is True


def test_re_reviewing_clears_the_staleness(signed_in, db):
    item_id = _component(
        db,
        allergens={
            "contains": ["milk"],
            "reviewed_at": REVIEWED_AT,
            "reviewed_by": "chef",
        },
        ingredients_updated_at=REVIEWED_AT + timedelta(days=1),
    )

    _declare(signed_in, item_id, contains=["milk"])

    from app.services import catalogue_admin

    with signed_in.application.app_context():
        item = catalogue_admin.get_item("component", item_id)
    assert item.allergen_review_is_stale is False


def test_a_review_does_not_move_the_ingredients_stamp(signed_in, db):
    """This editor writes `allergens` and nothing else."""
    from bson import ObjectId

    stamp = REVIEWED_AT + timedelta(days=1)
    item_id = _component(db, ingredients_updated_at=stamp)

    _declare(signed_in, item_id, contains=["milk"])

    document = db["components"].find_one({"_id": ObjectId(item_id)})
    assert document["ingredients_updated_at"] == stamp
    assert document["name"] == "Harissa"
    assert document["price_cents"] == 850


# --- publication ------------------------------------------------------------


def test_a_review_is_what_unblocks_publication(signed_in, db):
    from bson import ObjectId

    item_id = _component(db)
    listing = signed_in.get("/chef/components").get_data(as_text=True)
    assert "Review the allergen declaration before making this available." in listing

    _declare(signed_in, item_id, contains=["milk"])
    signed_in.post(
        f"/chef/components/{item_id}/availability", data={"available": "1"}
    )

    assert db["components"].find_one({"_id": ObjectId(item_id)})["is_available"] is True


# --- the page itself --------------------------------------------------------


def test_the_page_shows_the_ingredients_it_is_a_declaration_about(signed_in, db):
    item_id = _component(db)

    text = _text(
        signed_in.get(f"/chef/components/{item_id}/allergens").get_data(as_text=True)
    )

    assert "Red peppers" in text
    assert "Ingredients as they stand" in text


def test_the_page_has_one_h1_and_labels_every_control(signed_in, db):
    item_id = _component(db)

    html = signed_in.get(f"/chef/components/{item_id}/allergens").get_data(
        as_text=True
    )

    assert html.count("<h1") == 1
    for identifier in re.findall(r'<input[^>]*id="([^"]+)"[^>]*type="checkbox"', html):
        assert f'for="{identifier}"' in html
    for identifier in re.findall(r'type="checkbox"[^>]*id="([^"]+)"', html):
        assert f'for="{identifier}"' in html


def test_no_preference_flag_or_spice_level_reaches_this_surface(signed_in, db):
    item_id = _component(db, preference_flags=["chilli", "vegan"], spice_level=4)

    text = _text(
        signed_in.get(f"/chef/components/{item_id}/allergens").get_data(as_text=True)
    )

    assert "Chilli" not in text
    assert "Vegan" not in text
    assert "Very hot" not in text


def test_the_form_works_without_javascript(signed_in, db):
    """Every control is a plain checkbox inside one form POST.

    The only script the page carries is the theme pre-paint in the shared
    head; nothing in the form depends on it.
    """
    item_id = _component(db)

    html = signed_in.get(f"/chef/components/{item_id}/allergens").get_data(
        as_text=True
    )
    # The declaration's own form, not the header's.
    form = html.split('<form\n    class="form"', 1)[1].split("</form>", 1)[0]

    assert f"/chef/components/{item_id}/allergens" in form
    assert "onclick" not in form
    assert "<script" not in form
    assert "data-" not in form
