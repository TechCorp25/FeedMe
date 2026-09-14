"""The chef's meal-type editor.

01-DOMAIN.md names `meal_types` as a chef-owned collection, "ordered,
renameable", and 04-WORKFLOWS.md makes `/menu/<meal_type_slug>` one of the
three ordering entry points. Before this editor there was no chef route,
no script and no seed: `meal_types` had two read functions and nothing
that wrote to it, so in a real deployment the whole entry point was dead.

`test_menu_entry_point_is_reachable_end_to_end` is the test that fails
before the fix — it builds a meal type and a dish through the served HTTP
routes only, with no direct database write, and then asks for the menu as
a signed-out visitor.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.repositories import meal_types as meal_types_repo
from app.models.catalogue import Dish, MealType
from app.models.users import Role, User
from app.security.passwords import hash_password
from app.services import meal_type_admin

PASSWORD = "a-long-enough-passphrase"
REVIEWED = {
    "contains": ["milk"],
    "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef@example.com",
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


def _meal_type(db, *, name, slug, sort_order=0) -> str:
    document = MealType(name=name, slug=slug, sort_order=sort_order).to_mongo()
    return str(db["meal_types"].insert_one(document).inserted_id)


def _dish(db, *, name, slug, meal_type_ids=(), archived=False) -> str:
    dish = Dish.model_validate(
        {
            "name": name,
            "slug": slug,
            "category": "main",
            "price_cents": 2400,
            "unit": "portion",
            "meal_type_ids": list(meal_type_ids),
            "is_archived": archived,
            "allergens": dict(REVIEWED),
        }
    )
    return str(db["dishes"].insert_one(dish.to_mongo()).inserted_id)


# Repository reads need an application context of their own. The `db`
# fixture deliberately does not hold one open — see `tests/conftest.py` —
# so each read pushes and pops its own, exactly as a request does.


def _ordered(app) -> list[str]:
    with app.app_context():
        return [row.slug for row in meal_types_repo.list_meal_types()]


def _by_slug(app, slug):
    with app.app_context():
        return meal_types_repo.get_meal_type_by_slug(slug)


def _by_id(app, meal_type_id):
    with app.app_context():
        return meal_types_repo.chef_get_meal_type(meal_type_id)


# --- the gap this closes ----------------------------------------------------


def test_menu_entry_point_is_reachable_end_to_end(client, db, signed_in, app):
    """A meal type created through the UI makes `/menu/<slug>` real.

    Nothing here writes to MongoDB directly. Before the editor existed
    there was no route that could have created the meal type, so this
    fails at the first POST.
    """
    created = signed_in.post(
        "/chef/meal-types/save",
        data={"name": "Weekend brunch", "slug": ""},
        follow_redirects=True,
    )
    assert created.status_code == 200

    stored = _by_slug(app, "weekend-brunch")
    assert stored is not None
    assert stored.name == "Weekend brunch"

    # A dish put under it through the dish editor's own checkbox.
    dish_id = _dish(db, name="Baked eggs", slug="baked-eggs")
    signed_in.post(
        f"/chef/dishes/{dish_id}/save",
        data={
            "name": "Baked eggs",
            "slug": "baked-eggs",
            "category": "main",
            "price": "24.00",
            "unit": "portion",
            "serves": "2",
            "meal_type": stored.id,
        },
        follow_redirects=True,
    )
    signed_in.post(
        f"/chef/dishes/{dish_id}/availability",
        data={"available": "1"},
        follow_redirects=True,
    )

    # A signed-out visitor, which is who the entry point is for.
    client.post("/logout")
    menu = client.get("/menu/weekend-brunch")
    assert menu.status_code == 200
    assert "Baked eggs" in menu.get_data(as_text=True)


def test_dish_editor_offers_a_meal_type_that_was_created(signed_in):
    """The checkbox control is no longer permanently empty."""
    signed_in.post(
        "/chef/meal-types/save", data={"name": "Dinner", "slug": "dinner"}
    )
    page = signed_in.get("/chef/dishes/new").get_data(as_text=True)
    assert 'name="meal_type"' in page
    assert "Dinner" in page


def test_dish_editor_with_no_meal_types_points_at_the_editor(signed_in):
    page = signed_in.get("/chef/dishes/new").get_data(as_text=True)
    assert "No meal types exist yet" in page
    assert "/chef/meal-types" in page


# --- creating ---------------------------------------------------------------


def test_slug_is_derived_from_the_name_when_left_blank(signed_in, app):
    signed_in.post(
        "/chef/meal-types/save", data={"name": "Late supper", "slug": ""}
    )
    assert _by_slug(app, "late-supper") is not None


def test_a_typed_slug_is_never_overwritten(signed_in, app):
    signed_in.post(
        "/chef/meal-types/save", data={"name": "Late supper", "slug": "supper"}
    )
    assert _by_slug(app, "supper") is not None
    assert _by_slug(app, "late-supper") is None


def test_a_name_is_required(signed_in, app):
    response = signed_in.post("/chef/meal-types/save", data={"name": "  "})
    assert response.status_code == 400
    assert "Give the meal type a name." in response.get_data(as_text=True)
    assert _ordered(app) == []


def test_a_malformed_slug_is_refused(signed_in, app):
    response = signed_in.post(
        "/chef/meal-types/save", data={"name": "Brunch", "slug": "Weekend Brunch!"}
    )
    assert response.status_code == 400
    assert "lowercase letters" in response.get_data(as_text=True)
    assert _ordered(app) == []


def test_a_name_with_no_slug_in_it_is_refused_rather_than_stored(signed_in):
    """'—' slugifies to nothing, and an empty slug is not a URL."""
    response = signed_in.post("/chef/meal-types/save", data={"name": "———"})
    assert response.status_code == 400
    assert "gives no slug" in response.get_data(as_text=True)


def test_a_duplicate_slug_is_refused(signed_in, db):
    _meal_type(db, name="Dinner", slug="dinner")
    response = signed_in.post(
        "/chef/meal-types/save", data={"name": "Evening", "slug": "dinner"}
    )
    assert response.status_code == 400
    assert "already uses the slug" in response.get_data(as_text=True)


def test_a_new_meal_type_lands_at_the_end_of_the_order(signed_in, db, app):
    _meal_type(db, name="Breakfast", slug="breakfast", sort_order=0)
    _meal_type(db, name="Lunch", slug="lunch", sort_order=1)
    signed_in.post("/chef/meal-types/save", data={"name": "Dinner", "slug": "dinner"})
    assert _ordered(app) == [
        "breakfast",
        "lunch",
        "dinner",
    ]


# --- renaming ---------------------------------------------------------------


def test_renaming_never_re_derives_the_slug(signed_in, db, app):
    """A corrected label must not move a URL customers already have."""
    meal_type_id = _meal_type(db, name="Diner", slug="diner")
    signed_in.post(
        f"/chef/meal-types/{meal_type_id}/save",
        data={"name": "Dinner", "slug": "diner"},
    )
    stored = _by_id(app, meal_type_id)
    assert stored.name == "Dinner"
    assert stored.slug == "diner"


def test_the_rename_page_prefills_the_stored_slug(signed_in, db):
    meal_type_id = _meal_type(db, name="Diner", slug="diner")
    page = signed_in.get(f"/chef/meal-types/{meal_type_id}/edit").get_data(as_text=True)
    assert 'value="diner"' in page
    assert "saved link to this menu" in page


def test_a_slug_left_blank_on_an_edit_keeps_the_stored_one(signed_in, db, app):
    meal_type_id = _meal_type(db, name="Dinner", slug="dinner")
    signed_in.post(
        f"/chef/meal-types/{meal_type_id}/save",
        data={"name": "Evening meal", "slug": ""},
    )
    stored = _by_id(app, meal_type_id)
    assert stored.slug == "dinner"
    assert stored.name == "Evening meal"


def test_a_slug_change_is_accepted_when_it_is_actually_typed(signed_in, db, app):
    meal_type_id = _meal_type(db, name="Dinner", slug="diner")
    signed_in.post(
        f"/chef/meal-types/{meal_type_id}/save",
        data={"name": "Dinner", "slug": "dinner"},
    )
    assert _by_id(app, meal_type_id).slug == "dinner"


def test_renaming_does_not_move_the_meal_type_in_the_order(signed_in, db, app):
    _meal_type(db, name="Breakfast", slug="breakfast", sort_order=0)
    lunch = _meal_type(db, name="Lunch", slug="lunch", sort_order=1)
    _meal_type(db, name="Dinner", slug="dinner", sort_order=2)
    signed_in.post(
        f"/chef/meal-types/{lunch}/save", data={"name": "Midday", "slug": "lunch"}
    )
    assert _ordered(app) == [
        "breakfast",
        "lunch",
        "dinner",
    ]


# --- deleting ---------------------------------------------------------------


def test_a_meal_type_no_dish_uses_is_deleted(signed_in, db, app):
    meal_type_id = _meal_type(db, name="Snack", slug="snack")
    signed_in.post(f"/chef/meal-types/{meal_type_id}/delete", follow_redirects=True)
    assert _by_id(app, meal_type_id) is None


def test_deletion_is_refused_while_a_dish_references_the_type(signed_in, db, app):
    meal_type_id = _meal_type(db, name="Dinner", slug="dinner")
    _dish(db, name="Ragu", slug="ragu", meal_type_ids=[meal_type_id])

    response = signed_in.post(
        f"/chef/meal-types/{meal_type_id}/delete", follow_redirects=True
    )
    body = response.get_data(as_text=True)
    assert _by_id(app, meal_type_id) is not None
    assert "cannot be deleted" in body
    # The refusal names the dish, so the chef knows where to go.
    assert "Ragu" in body


def test_an_archived_dish_still_blocks_deletion(signed_in, db, app):
    """Archived is withdrawn, not deleted — restoring it must still work."""
    meal_type_id = _meal_type(db, name="Dinner", slug="dinner")
    _dish(db, name="Ragu", slug="ragu", meal_type_ids=[meal_type_id], archived=True)

    signed_in.post(f"/chef/meal-types/{meal_type_id}/delete", follow_redirects=True)
    assert _by_id(app, meal_type_id) is not None


def test_the_delete_control_is_not_offered_where_it_would_be_refused(signed_in, db):
    meal_type_id = _meal_type(db, name="Dinner", slug="dinner")
    _dish(db, name="Ragu", slug="ragu", meal_type_ids=[meal_type_id])

    page = signed_in.get("/chef/meal-types").get_data(as_text=True)
    assert "Delete Dinner" not in page
    assert "1 dish uses this meal type" in page
    assert "broken menu link" in page


def test_the_refusal_counts_the_dishes_it_does_not_name(signed_in, db):
    meal_type_id = _meal_type(db, name="Dinner", slug="dinner")
    for index in range(7):
        _dish(db, name=f"Dish {index}", slug=f"dish-{index}",
              meal_type_ids=[meal_type_id])

    body = signed_in.post(
        f"/chef/meal-types/{meal_type_id}/delete", follow_redirects=True
    ).get_data(as_text=True)
    assert "and 2 others" in body


def test_deleting_a_meal_type_that_is_gone_is_a_404(signed_in):
    assert signed_in.post("/chef/meal-types/nope/delete").status_code == 404


# --- reordering -------------------------------------------------------------


def test_moving_a_meal_type_up_swaps_it_with_its_neighbour(signed_in, db, app):
    _meal_type(db, name="Breakfast", slug="breakfast", sort_order=0)
    lunch = _meal_type(db, name="Lunch", slug="lunch", sort_order=1)
    signed_in.post(f"/chef/meal-types/{lunch}/move", data={"direction": "up"})
    assert _ordered(app) == [
        "lunch",
        "breakfast",
    ]


def test_moving_the_first_one_up_does_nothing_and_does_not_complain(signed_in, db, app):
    breakfast = _meal_type(db, name="Breakfast", slug="breakfast", sort_order=0)
    _meal_type(db, name="Lunch", slug="lunch", sort_order=1)
    response = signed_in.post(
        f"/chef/meal-types/{breakfast}/move",
        data={"direction": "up"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert _ordered(app) == [
        "breakfast",
        "lunch",
    ]


def test_two_meal_types_sharing_a_sort_order_still_swap(signed_in, db, app):
    """A blind swap of equal numbers would be a no-op that looks broken."""
    _meal_type(db, name="Breakfast", slug="breakfast", sort_order=0)
    lunch = _meal_type(db, name="Lunch", slug="lunch", sort_order=0)
    signed_in.post(f"/chef/meal-types/{lunch}/move", data={"direction": "up"})
    assert _ordered(app) == [
        "lunch",
        "breakfast",
    ]


def test_a_direction_that_is_not_one_is_refused(db, app, chef):
    meal_type = MealType(name="Dinner", slug="dinner")
    with app.app_context():
        stored = meal_types_repo.chef_create_meal_type(meal_type)
        with pytest.raises(meal_type_admin.MealTypeFormError):
            meal_type_admin.move(stored, "sideways")


# --- access -----------------------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/chef/meal-types"),
        ("post", "/chef/meal-types/save"),
    ],
)
def test_the_editor_is_chef_only(client, method, path):
    """A signed-out visitor is redirected to sign in, never served it."""
    response = getattr(client, method)(path)
    assert response.status_code in {302, 401, 404}
    assert "/chef/meal-types" not in response.get_data(as_text=True)


def test_a_customer_gets_404_rather_than_403(client, db):
    """404, not 403: a 403 would confirm the route exists."""
    user = User(
        email="customer@example.com",
        password_hash=hash_password(PASSWORD),
        role=Role.CUSTOMER,
    )
    db["users"].insert_one(user.to_mongo())
    client.post(
        "/login", data={"email": "customer@example.com", "password": PASSWORD}
    )
    assert client.get("/chef/meal-types").status_code == 404
