"""The chef's catalogue editors.

04-WORKFLOWS.md gives `/chef/components` and `/chef/dishes` full CRUD —
create, edit, archive, reorder, toggle availability — with optional
component linking on the dish editor.

The rules that matter most here are the ones about what this form is *not*
allowed to do: it never writes an allergen field, it cannot publish an
unreviewed item, and editing ingredients flags the review stale without
invalidating or unpublishing anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.db.repositories import components as components_repo
from app.models.catalogue import Component, Dish, MealType
from app.models.users import Role, User
from app.security.passwords import hash_password
from app.services import accounts, catalogue_admin

PASSWORD = "a-long-enough-passphrase"
REVIEWED_AT = datetime(2026, 3, 1, tzinfo=timezone.utc)
REVIEWED = {
    "contains": ["milk"],
    "reviewed_at": REVIEWED_AT,
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
def signed_in(client, chef):
    client.post("/login", data={"email": "chef@example.com", "password": PASSWORD})
    return client


def _component(db, *, name, slug, reviewed=True, available=False, sort_order=0) -> str:
    item = Component.model_validate(
        {
            "name": name, "slug": slug, "category": "sauce", "price_cents": 850,
            "unit": "250ml", "is_available": available, "sort_order": sort_order,
            "allergens": dict(REVIEWED) if reviewed else {},
        }
    )
    return str(db["components"].insert_one(item.to_mongo()).inserted_id)


def _dish(db, *, name, slug, refs=()) -> str:
    item = Dish.model_validate(
        {
            "name": name, "slug": slug, "category": "main", "price_cents": 2400,
            "unit": "portion", "component_refs": list(refs),
            "allergens": dict(REVIEWED),
        }
    )
    return str(db["dishes"].insert_one(item.to_mongo()).inserted_id)


def _form(**overrides) -> dict:
    """A minimal valid component form, overridable per test."""
    base = {
        "name": "Harissa rosa",
        "slug": "harissa-rosa",
        "summary": "Rose harissa, slow cooked.",
        "description": "",
        "category": "sauce",
        "price": "14.50",
        "unit": "250ml",
        "spice_level": "3",
        "ingredient-0-name": "Red peppers",
        "ingredient-0-quantity": "600g",
    }
    base.update(overrides)
    return base


# --- the editors belong to the chef -----------------------------------------


@pytest.mark.parametrize(
    "path", ["/chef/components", "/chef/dishes", "/chef/components/new"]
)
def test_the_editors_require_the_chef(client, app, path):
    assert client.get(path).status_code == 302

    with app.app_context():
        accounts.register_customer(
            email="ada@example.com", password=PASSWORD,
            password_confirmation=PASSWORD,
        )
    client.post("/login", data={"email": "ada@example.com", "password": PASSWORD})
    assert client.get(path).status_code == 404


def test_a_customer_cannot_write_the_catalogue(client, app, db):
    item_id = _component(db, name="Harissa", slug="harissa")
    with app.app_context():
        accounts.register_customer(
            email="ada@example.com", password=PASSWORD,
            password_confirmation=PASSWORD,
        )
    client.post("/login", data={"email": "ada@example.com", "password": PASSWORD})

    assert client.post(
        f"/chef/components/{item_id}/save", data=_form()
    ).status_code == 404
    assert client.post(
        f"/chef/components/{item_id}/archive", data={"archive": "1"}
    ).status_code == 404
    assert db["components"].find_one({"_id": ObjectId(item_id)})["name"] == "Harissa"


def test_an_unknown_kind_is_a_404(signed_in):
    assert signed_in.get("/chef/widgets/new").status_code == 404


def test_the_dish_editor_is_reachable_under_its_real_plural(signed_in, db):
    """"dish" + "s" is "dishs". Every dish URL is spelled out, not derived."""
    dish_id = _dish(db, name="Lamb tagine", slug="lamb-tagine")

    assert signed_in.get("/chef/dishes/new").status_code == 200
    assert signed_in.get(f"/chef/dishes/{dish_id}/edit").status_code == 200
    assert signed_in.get("/chef/dishs/new").status_code == 404


def test_a_category_renders_as_words_not_an_enum(signed_in, db):
    """`ComponentCategory.SAUCE` is not a thing to show a person."""
    _component(db, name="Harissa", slug="harissa")

    page = signed_in.get("/chef/components").get_data(as_text=True)

    assert "Sauces" in page
    assert "ComponentCategory" not in page


# --- price arithmetic is integer cents, always ------------------------------


@pytest.mark.parametrize(
    "typed,cents",
    [
        ("14.50", 1450), ("14.5", 1450), ("7", 700), ("0", 0),
        ("$14.50", 1450), ("1,299.99", 129999), (".75", 75),
    ],
)
def test_a_typed_price_becomes_integer_cents(typed, cents):
    """No float touches a price. `float('14.50') * 100` is 1449.99…"""
    assert catalogue_admin.parse_price_cents(typed) == cents


@pytest.mark.parametrize("typed", ["", "abc", "14.505", "-3", "12.3.4"])
def test_a_price_that_is_not_one_is_refused(typed):
    with pytest.raises(catalogue_admin.ItemFormError):
        catalogue_admin.parse_price_cents(typed)


def test_the_price_round_trips_through_the_form_field():
    assert catalogue_admin.format_price_input(1450) == "14.50"
    assert catalogue_admin.format_price_input(700) == "7.00"
    assert catalogue_admin.format_price_input(5) == "0.05"


# --- creating and editing ---------------------------------------------------


def test_creating_a_component_stores_it_as_an_unpublished_draft(signed_in, db):
    response = signed_in.post("/chef/components/save", data=_form())

    assert response.status_code == 302
    document = db["components"].find_one({"slug": "harissa-rosa"})
    assert document["name"] == "Harissa rosa"
    assert document["price_cents"] == 1450
    # No review yet, so it cannot be visible to customers.
    assert document["is_available"] is False
    assert document["allergens"]["reviewed_at"] is None


def test_a_blank_slug_is_made_from_the_name(signed_in, db):
    signed_in.post("/chef/components/save", data=_form(slug=""))

    assert db["components"].find_one({"slug": "harissa-rosa"}) is not None


def test_a_duplicate_slug_is_refused_with_a_sentence(signed_in, db):
    _component(db, name="Harissa", slug="harissa-rosa")

    response = signed_in.post("/chef/components/save", data=_form())

    assert response.status_code == 400
    assert "already uses the slug" in response.get_data(as_text=True)
    assert db["components"].count_documents({"slug": "harissa-rosa"}) == 1


def test_a_refusal_re_renders_what_was_typed(signed_in, db):
    """This form is long enough that losing it once costs an afternoon."""
    response = signed_in.post(
        "/chef/components/save", data=_form(price="not a price", name="Zhoug")
    )

    page = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "Zhoug" in page
    assert "not a price" in page


def test_editing_writes_only_the_fields_the_form_owns(signed_in, db):
    """A whole-document replace would carry a stale allergen block back."""
    item_id = _component(db, name="Harissa", slug="harissa")

    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(name="Harissa rosa", slug="harissa"),
    )

    document = db["components"].find_one({"_id": ObjectId(item_id)})
    assert document["name"] == "Harissa rosa"
    # Untouched, exactly as 01-DOMAIN.md requires.
    assert document["allergens"]["contains"] == ["milk"]
    assert document["allergens"]["reviewed_by"] == "chef"


def test_a_review_made_in_another_tab_survives_a_save(signed_in, db):
    """The concrete failure the explicit `$set` exists to prevent."""
    item_id = _component(db, name="Harissa", slug="harissa", reviewed=False)
    page_form = _form(name="Harissa", slug="harissa")

    # The allergen editor reviews it while this form sits open.
    db["components"].update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"allergens": {
            "contains": ["sesame"], "may_contain": [], "gluten_cereals": [],
            "tree_nut_species": [], "sulphites_declared": False,
            "chef_note": None, "reviewed_at": REVIEWED_AT, "reviewed_by": "chef",
        }}},
    )

    signed_in.post(f"/chef/components/{item_id}/save", data=page_form)

    document = db["components"].find_one({"_id": ObjectId(item_id)})
    assert document["allergens"]["contains"] == ["sesame"]
    assert document["allergens"]["reviewed_at"] is not None


def test_the_four_tabs_are_stored(signed_in, db):
    signed_in.post(
        "/chef/components/save",
        data=_form(
            **{
                "ingredient-1-name": "Rose petals",
                "ingredient-1-note": "Dried",
                "ingredient-1-optional": "1",
                "storage-method": "refrigerate",
                "storage-temperature": "0-4",
                "storage-shelf-life-days": "14",
                "storage-shelf-life-note": "3 days once opened",
                "step-0-text": "Blitz the peppers.",
                "step-1-text": "Fold through the harissa.",
                "prep-reheat-method": "pan",
                "prep-reheat-minutes": "4",
            }
        ),
    )

    document = db["components"].find_one({"slug": "harissa-rosa"})
    assert [i["name"] for i in document["ingredients"]] == ["Red peppers", "Rose petals"]
    # Authored order, never alphabetised (01-DOMAIN.md).
    assert document["ingredients"][1]["is_optional"] is True
    assert document["storage"]["shelf_life_days"] == 14
    assert document["preparation"]["steps"] == [
        "Blitz the peppers.",
        "Fold through the harissa.",
    ]
    assert document["preparation"]["reheat_method"] == "pan"


def test_a_half_filled_storage_block_is_refused(signed_in, db):
    """A shelf life nobody typed is a use-by the kitchen stands behind."""
    response = signed_in.post(
        "/chef/components/save",
        data=_form(**{"storage-method": "refrigerate", "storage-temperature": "0-4"}),
    )

    assert response.status_code == 400
    assert "shelf life" in response.get_data(as_text=True)
    assert db["components"].count_documents({}) == 0


def test_an_ingredient_row_with_no_name_is_reported_not_dropped(signed_in):
    response = signed_in.post(
        "/chef/components/save",
        data=_form(**{"ingredient-1-quantity": "2 tbsp"}),
    )

    assert response.status_code == 400
    assert "no name" in response.get_data(as_text=True)


def test_a_new_preference_flag_is_accepted_and_stored(signed_in, db):
    """The vocabulary is chef-extensible (01-DOMAIN.md)."""
    signed_in.post(
        "/chef/components/save",
        data=_form(preference="vegan", **{"preference-new": "Smoked, Fermented"}),
    )

    document = db["components"].find_one({"slug": "harissa-rosa"})
    assert document["preference_flags"] == ["vegan", "smoked", "fermented"]


def test_a_preference_flag_nobody_was_offered_is_dropped(signed_in, db):
    form = _form()
    form["preference"] = "not_a_flag"
    signed_in.post("/chef/components/save", data=form)

    assert db["components"].find_one({"slug": "harissa-rosa"})["preference_flags"] == []


# --- the staleness prompt ---------------------------------------------------


def test_editing_ingredients_flags_the_review_stale(signed_in, db, app):
    """It prompts. It does not invalidate, and it does not unpublish."""
    item_id = _component(db, name="Harissa", slug="harissa", available=True)

    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(name="Harissa", slug="harissa", **{"ingredient-0-name": "Guajillo"}),
    )

    with app.app_context():
        item = components_repo.chef_get_component(item_id)

    assert item.allergen_review_is_stale
    # Still published, still declaring what it was reviewed with.
    assert item.is_available is True
    assert item.allergens.is_reviewed
    assert item.allergens.contains[0].value == "milk"


def test_a_save_that_leaves_ingredients_alone_does_not_flag_it(
    signed_in, db, app
):
    """A prompt that fires on every save is one the chef learns to ignore."""
    item_id = _component(db, name="Harissa", slug="harissa", available=True)
    first = _form(name="Harissa", slug="harissa")
    signed_in.post(f"/chef/components/{item_id}/save", data=first)

    # Re-review, then save again changing only the price.
    db["components"].update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"allergens.reviewed_at": datetime.now(timezone.utc)}},
    )
    signed_in.post(
        f"/chef/components/{item_id}/save", data=dict(first, price="16.00")
    )

    with app.app_context():
        item = components_repo.chef_get_component(item_id)

    assert item.price_cents == 1600
    assert not item.allergen_review_is_stale


def test_an_unreviewed_item_is_never_called_stale(db, app):
    """Unreviewed is a different state, with a harder rule."""
    item_id = _component(db, name="Harissa", slug="harissa", reviewed=False)
    db["components"].update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"ingredients_updated_at": datetime.now(timezone.utc)}},
    )

    with app.app_context():
        item = components_repo.chef_get_component(item_id)

    assert not item.allergen_review_is_stale
    assert not item.allergens.is_reviewed


# --- availability is gated on the allergen review ---------------------------


def test_an_unreviewed_item_cannot_be_made_available(signed_in, db):
    item_id = _component(db, name="Harissa", slug="harissa", reviewed=False)

    response = signed_in.post(
        f"/chef/components/{item_id}/availability",
        data={"available": "1"},
        follow_redirects=True,
    )

    assert "allergen declaration has been reviewed" in response.get_data(as_text=True)
    assert db["components"].find_one({"_id": ObjectId(item_id)})["is_available"] is False


def test_the_list_does_not_offer_a_control_that_can_only_fail(signed_in, db):
    _component(db, name="Harissa", slug="harissa", reviewed=False)

    page = signed_in.get("/chef/components").get_data(as_text=True)

    assert "Review the allergen declaration before making this available." in page
    assert "Make Harissa available" not in page


def test_a_reviewed_item_can_be_made_available_and_withdrawn(signed_in, db):
    item_id = _component(db, name="Harissa", slug="harissa")

    signed_in.post(
        f"/chef/components/{item_id}/availability", data={"available": "1"}
    )
    assert db["components"].find_one({"_id": ObjectId(item_id)})["is_available"] is True

    signed_in.post(f"/chef/components/{item_id}/availability", data={})
    assert db["components"].find_one({"_id": ObjectId(item_id)})["is_available"] is False


# --- archive ----------------------------------------------------------------


def test_archiving_also_clears_availability(signed_in, db):
    """Leaving both set stores a contradiction that surfaces on restore."""
    item_id = _component(db, name="Harissa", slug="harissa", available=True)

    signed_in.post(f"/chef/components/{item_id}/archive", data={"archive": "1"})

    document = db["components"].find_one({"_id": ObjectId(item_id)})
    assert document["is_archived"] is True
    assert document["is_available"] is False


def test_restoring_brings_it_back_as_a_draft(signed_in, db):
    item_id = _component(db, name="Harissa", slug="harissa", available=True)
    signed_in.post(f"/chef/components/{item_id}/archive", data={"archive": "1"})

    signed_in.post(f"/chef/components/{item_id}/archive", data={})

    document = db["components"].find_one({"_id": ObjectId(item_id)})
    assert document["is_archived"] is False
    # Not silently re-published: the chef says when it goes back on sale.
    assert document["is_available"] is False


def test_archived_items_are_hidden_from_the_list_unless_asked_for(signed_in, db):
    item_id = _component(db, name="Harissa", slug="harissa")
    signed_in.post(f"/chef/components/{item_id}/archive", data={"archive": "1"})

    # The edit link, not the bare name: the flash confirming the archive
    # also says "Harissa".
    link = f'href="/chef/components/{item_id}/edit"'
    assert link not in signed_in.get("/chef/components").get_data(as_text=True)
    assert link in signed_in.get("/chef/components?archived=1").get_data(as_text=True)


def test_an_archived_item_is_gone_from_the_customer_catalogue(signed_in, db, client):
    item_id = _component(db, name="Harissa", slug="harissa", available=True)
    assert client.get("/components/harissa").status_code == 200

    signed_in.post(f"/chef/components/{item_id}/archive", data={"archive": "1"})

    assert client.get("/components/harissa").status_code == 404


# --- reordering -------------------------------------------------------------


def test_moving_an_item_swaps_it_with_its_neighbour(signed_in, db, app):
    _component(db, name="Alpha", slug="alpha", sort_order=0)
    second = _component(db, name="Beta", slug="beta", sort_order=1)

    signed_in.post(f"/chef/components/{second}/move", data={"direction": "up"})

    with app.app_context():
        order = [row.item.name for row in catalogue_admin.admin_list("component").rows]
    assert order == ["Beta", "Alpha"]


def test_moving_past_the_end_does_nothing(signed_in, db, app):
    first = _component(db, name="Alpha", slug="alpha", sort_order=0)
    _component(db, name="Beta", slug="beta", sort_order=1)

    signed_in.post(f"/chef/components/{first}/move", data={"direction": "up"})

    with app.app_context():
        order = [row.item.name for row in catalogue_admin.admin_list("component").rows]
    assert order == ["Alpha", "Beta"]


def test_items_sharing_a_sort_order_still_reorder(signed_in, db, app):
    """A blind swap is a no-op when both sit at the default 0."""
    _component(db, name="Alpha", slug="alpha", sort_order=0)
    second = _component(db, name="Beta", slug="beta", sort_order=0)

    signed_in.post(f"/chef/components/{second}/move", data={"direction": "up"})

    with app.app_context():
        order = [row.item.name for row in catalogue_admin.admin_list("component").rows]
    assert order == ["Beta", "Alpha"]


# --- the dish editor --------------------------------------------------------


def _dish_form(**overrides) -> dict:
    base = {
        "name": "Lamb tagine",
        "slug": "lamb-tagine",
        "summary": "Slow shoulder, apricot, harissa.",
        "description": "",
        "category": "Mains",
        "price": "28.00",
        "unit": "portion",
        "serves": "4",
        "spice_level": "2",
        "ingredient-0-name": "Lamb shoulder",
    }
    base.update(overrides)
    return base


def test_a_dish_links_components_for_provenance(signed_in, db):
    harissa = _component(db, name="Harissa", slug="harissa")
    labneh = _component(db, name="Labneh", slug="labneh")

    signed_in.post(
        "/chef/dishes/save",
        data={**_dish_form(), "component_ref": [harissa, labneh]},
    )

    document = db["dishes"].find_one({"slug": "lamb-tagine"})
    assert document["component_refs"] == [harissa, labneh]
    assert document["serves"] == 4


def test_a_link_to_something_that_does_not_exist_is_dropped(signed_in, db):
    harissa = _component(db, name="Harissa", slug="harissa")

    signed_in.post(
        "/chef/dishes/save",
        data={
            **_dish_form(),
            "component_ref": [harissa, str(ObjectId()), "not-an-id"],
        },
    )

    assert db["dishes"].find_one({"slug": "lamb-tagine"})["component_refs"] == [harissa]


def test_linking_a_component_never_touches_the_dishs_own_declaration(
    signed_in, db, app
):
    """A dish's own tabs are authoritative (01-DOMAIN.md)."""
    peanutty = str(
        db["components"].insert_one(
            Component.model_validate(
                {
                    "name": "Satay", "slug": "satay", "category": "sauce",
                    "price_cents": 900, "unit": "250ml",
                    "allergens": {
                        "contains": ["peanut"],
                        "reviewed_at": REVIEWED_AT, "reviewed_by": "chef",
                    },
                }
            ).to_mongo()
        ).inserted_id
    )
    dish_id = _dish(db, name="Lamb tagine", slug="lamb-tagine")

    signed_in.post(
        f"/chef/dishes/{dish_id}/save",
        data={**_dish_form(slug="lamb-tagine"), "component_ref": [peanutty]},
    )

    document = db["dishes"].find_one({"_id": ObjectId(dish_id)})
    assert document["component_refs"] == [peanutty]
    # Nothing rolled up. The chef resolves a discrepancy by hand.
    assert document["allergens"]["contains"] == ["milk"]


def test_a_dish_only_takes_meal_types_that_exist(signed_in, db):
    breakfast = str(
        db["meal_types"].insert_one(
            MealType(name="Breakfast", slug="breakfast").to_mongo()
        ).inserted_id
    )

    signed_in.post(
        "/chef/dishes/save",
        data={**_dish_form(), "meal_type": [breakfast, str(ObjectId())]},
    )

    assert db["dishes"].find_one({"slug": "lamb-tagine"})["meal_type_ids"] == [breakfast]


# --- the pages work without JavaScript --------------------------------------


def test_every_editor_control_is_a_form_post(signed_in, db):
    _component(db, name="Harissa", slug="harissa")

    listing = signed_in.get("/chef/components").get_data(as_text=True)
    form = signed_in.get("/chef/components/new").get_data(as_text=True)

    assert 'method="post"' in listing
    assert "onclick" not in listing
    assert 'method="post"' in form
    assert "onclick" not in form
    # The form says, on every render, that it does not touch allergens.
    assert "never changed by this form" in form
