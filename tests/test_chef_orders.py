"""The chef's order queue.

04-WORKFLOWS.md specifies the queue as non-terminal orders sorted by
`requested_for` ascending, filterable by status and date, showing each
order's customer, dietary notes, lines and a consolidated allergen
summary, with one-click transitions along the allowed map.

These tests hold it to that, and to the three rules that surround it: the
area is the chef's alone and a customer must not be able to tell it
exists, a transition is decided by `order_state.py` and written under the
status it was decided against, and a cancellation credits the charge back
exactly once.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.repositories import orders as orders_repo
from app.models.catalogue import Component
from app.models.orders import LedgerEntryType, OrderStatus, PaymentStatus
from app.models.users import Role, User
from app.security.passwords import hash_password
from app.services import accounts, chef_orders

PASSWORD = "a-long-enough-passphrase"

REVIEWED_MILK = {
    "contains": ["milk"],
    "may_contain": [],
    "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef",
}

REVIEWED_NUTS = {
    "contains": ["tree_nuts"],
    "tree_nut_species": ["cashew", "almond"],
    "may_contain": ["sesame"],
    "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef",
}


def _component(db, *, name, slug, allergens, price=850) -> str:
    item = Component.model_validate(
        {
            "name": name,
            "slug": slug,
            "category": "sauce",
            "price_cents": price,
            "unit": "250ml",
            "is_available": True,
            "allergens": dict(allergens),
        }
    )
    return str(db["components"].insert_one(item.to_mongo()).inserted_id)


@pytest.fixture()
def labneh(db) -> str:
    return _component(db, name="Labneh", slug="labneh", allergens=REVIEWED_MILK)


@pytest.fixture()
def dukkah(db) -> str:
    return _component(db, name="Dukkah", slug="dukkah", allergens=REVIEWED_NUTS)


@pytest.fixture()
def chef(app, db):
    """The single `chef_admin`, provisioned the way the script does it."""
    user = User(
        email="chef@example.com",
        password_hash=hash_password(PASSWORD),
        display_name="Chef",
        role=Role.CHEF_ADMIN,
    )
    db["users"].insert_one(user.to_mongo())
    return user


def _register(app, email: str, **extra) -> None:
    with app.app_context():
        accounts.register_customer(
            email=email,
            password=PASSWORD,
            password_confirmation=PASSWORD,
            **extra,
        )


def _sign_in(client, email: str):
    return client.post(
        "/login", data={"email": email, "password": PASSWORD}, follow_redirects=False
    )


def _place_order(client, item_id: str, *, requested_for: date | None = None) -> str:
    client.post(
        "/cart/add",
        data={"item_type": "component", "item_id": item_id, "quantity": "2"},
    )
    page = client.get("/checkout").get_data(as_text=True)

    def hidden(name: str) -> str:
        match = re.search(rf'name="{re.escape(name)}" value="([^"]*)"', page)
        return match.group(1) if match else ""

    when = requested_for or (date.today() + timedelta(days=1))
    response = client.post(
        "/checkout",
        data={
            "requested_for": when.isoformat(),
            "fulfilment": "collection",
            "checkout_token": hidden("checkout_token"),
            "review_digest": hidden("review_digest"),
        },
    )
    return response.headers["Location"].rsplit("/", 1)[-1]


def _order_id(db, reference: str) -> str:
    return str(db["orders"].find_one({"reference": reference})["_id"])


@pytest.fixture()
def placed(client, app, db, labneh):
    """One customer, one placed order, and the chef signed in afterwards."""
    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    db["users"].update_one(
        {"email": "ada@example.com"},
        {"$set": {"dietary_notes": "Coeliac — no gluten at all."}},
    )
    reference = _place_order(client, labneh)
    client.post("/logout")
    return reference


# --- the area belongs to the chef, and nobody may learn otherwise -----------


@pytest.mark.parametrize("path", ["/chef/", "/chef/orders"])
def test_the_chef_area_requires_a_session(client, path):
    response = client.get(path)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.parametrize("path", ["/chef/", "/chef/orders"])
def test_a_customer_gets_404_not_403(client, app, path):
    """404, never 403: a 403 would confirm the chef area exists."""
    _register(app, "ada@example.com")
    _sign_in(client, "ada@example.com")

    assert client.get(path).status_code == 404


def test_a_customer_cannot_move_an_order_through_the_chef_routes(
    client, app, db, placed
):
    order_id = _order_id(db, placed)
    _register(app, "bob@example.com")
    _sign_in(client, "bob@example.com")

    assert (
        client.post(
            f"/chef/orders/{order_id}/transition", data={"target": "confirmed"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/chef/orders/{order_id}/payment", data={"payment_status": "settled"}
        ).status_code
        == 404
    )
    assert db["orders"].find_one({"reference": placed})["status"] == "placed"


def test_the_chef_lands_on_the_queue_after_signing_in(client, chef):
    response = _sign_in(client, "chef@example.com")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/chef/orders")


def test_a_customer_still_lands_on_the_cart(client, app):
    _register(app, "ada@example.com")
    response = _sign_in(client, "ada@example.com")

    assert response.headers["Location"].endswith("/cart")


# --- what the queue shows ---------------------------------------------------


def test_the_queue_shows_the_order_its_customer_and_their_dietary_notes(
    client, chef, placed
):
    _sign_in(client, "chef@example.com")
    page = client.get("/chef/orders").get_data(as_text=True)

    assert placed in page
    assert "Ada" in page
    assert "Coeliac — no gluten at all." in page
    assert "Labneh" in page


def test_the_queue_renders_a_consolidated_allergen_summary(
    client, app, db, chef, labneh, dukkah
):
    """One declaration per order, rolled up across every line."""
    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    client.post(
        "/cart/add",
        data={"item_type": "component", "item_id": labneh, "quantity": "1"},
    )
    reference = _place_order(client, dukkah)
    client.post("/logout")

    _sign_in(client, "chef@example.com")
    page = client.get("/chef/orders").get_data(as_text=True)

    assert reference in page
    assert "Allergens across this order" in page
    # Both lines' declarations, in one list, with the species named inline
    # and never abbreviated (03-FRONTEND.md).
    assert "Milk" in page
    assert "Tree nuts (almond, cashew)" in page
    assert "May contain — cross-contact risk" in page


def test_the_summary_reads_from_the_snapshot_not_the_catalogue(
    app, db, chef, labneh, client
):
    """A catalogue edit after the order must not change what the chef sees.

    The whole point of the snapshot: the kitchen cooks around what the
    customer was told, and an allergen block edited since then is a
    different statement about a different item.
    """
    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    reference = _place_order(client, labneh)
    client.post("/logout")

    from bson import ObjectId

    db["components"].update_one(
        {"_id": ObjectId(labneh)},
        {"$set": {"allergens.contains": ["peanut"]}},
    )

    with app.app_context():
        order = orders_repo.chef_get_order(_order_id(db, reference))
        summary = chef_orders.summarise_allergens(order)

    assert summary.contains_labels == ["Milk"]
    assert "Peanut" not in summary.contains_labels


def test_a_declared_allergen_is_not_also_listed_as_a_cross_contact_risk(
    app, db, chef, client, labneh, dukkah
):
    """Dukkah flags sesame as possible; a sesame line would make it certain."""
    sesame = _component(
        db,
        name="Tahini",
        slug="tahini",
        allergens={
            "contains": ["sesame"],
            "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
            "reviewed_by": "chef",
        },
    )
    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    client.post(
        "/cart/add",
        data={"item_type": "component", "item_id": sesame, "quantity": "1"},
    )
    reference = _place_order(client, dukkah)
    client.post("/logout")

    with app.app_context():
        order = orders_repo.chef_get_order(_order_id(db, reference))
        summary = chef_orders.summarise_allergens(order)

    assert "Sesame" in summary.contains_labels
    assert "Sesame" not in summary.may_contain_labels


def test_the_queue_defaults_to_outstanding_orders_only(client, app, db, chef, labneh):
    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    open_reference = _place_order(client, labneh)
    done_reference = _place_order(client, labneh)
    client.post("/logout")
    db["orders"].update_one(
        {"reference": done_reference}, {"$set": {"status": "collected"}}
    )

    _sign_in(client, "chef@example.com")
    page = client.get("/chef/orders").get_data(as_text=True)

    assert open_reference in page
    assert done_reference not in page


def test_the_queue_sorts_by_the_requested_date(client, app, db, chef, labneh):
    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    later = _place_order(client, labneh, requested_for=date.today() + timedelta(days=9))
    sooner = _place_order(client, labneh, requested_for=date.today() + timedelta(days=2))
    client.post("/logout")

    _sign_in(client, "chef@example.com")
    page = client.get("/chef/orders").get_data(as_text=True)

    assert page.index(sooner) < page.index(later)


def test_the_queue_filters_by_status_including_a_finished_one(
    client, app, db, chef, labneh
):
    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    open_reference = _place_order(client, labneh)
    done_reference = _place_order(client, labneh)
    client.post("/logout")
    db["orders"].update_one(
        {"reference": done_reference}, {"$set": {"status": "collected"}}
    )

    _sign_in(client, "chef@example.com")
    page = client.get("/chef/orders?status=collected").get_data(as_text=True)

    assert done_reference in page
    assert open_reference not in page


def test_the_queue_filters_by_the_requested_date(client, app, db, chef, labneh):
    wanted = date.today() + timedelta(days=3)
    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    on_the_day = _place_order(client, labneh, requested_for=wanted)
    another_day = _place_order(
        client, labneh, requested_for=date.today() + timedelta(days=4)
    )
    client.post("/logout")

    _sign_in(client, "chef@example.com")
    page = client.get(
        f"/chef/orders?requested_for={wanted.isoformat()}"
    ).get_data(as_text=True)

    assert on_the_day in page
    assert another_day not in page


def test_an_unreadable_filter_shows_the_queue_rather_than_failing(
    client, chef, placed
):
    """A stale bookmark is not worth a 400 on the kitchen's working list."""
    _sign_in(client, "chef@example.com")
    response = client.get("/chef/orders?status=banana&requested_for=not-a-date")

    assert response.status_code == 200
    assert placed in response.get_data(as_text=True)


# --- transitions ------------------------------------------------------------


def test_the_queue_offers_only_the_transitions_the_map_allows(app, db, chef, placed):
    with app.app_context():
        order = orders_repo.chef_get_order(_order_id(db, placed))

    targets = [choice.target for choice in chef_orders.transition_choices(order)]

    assert targets == [OrderStatus.CONFIRMED, OrderStatus.CANCELLED]


def test_one_click_moves_the_order_along_the_map(client, db, chef, placed):
    _sign_in(client, "chef@example.com")
    order_id = _order_id(db, placed)

    response = client.post(
        f"/chef/orders/{order_id}/transition", data={"target": "confirmed"}
    )

    assert response.status_code == 302
    document = db["orders"].find_one({"reference": placed})
    assert document["status"] == "confirmed"
    assert document["status_history"][-1]["status"] == "confirmed"


def test_a_transition_outside_the_map_is_refused(client, db, chef, placed):
    _sign_in(client, "chef@example.com")
    order_id = _order_id(db, placed)

    client.post(f"/chef/orders/{order_id}/transition", data={"target": "delivered"})

    assert db["orders"].find_one({"reference": placed})["status"] == "placed"


def test_prepared_at_is_stamped_on_entry_to_ready(client, db, chef, placed):
    _sign_in(client, "chef@example.com")
    order_id = _order_id(db, placed)

    for target in ("confirmed", "prepping", "ready"):
        client.post(f"/chef/orders/{order_id}/transition", data={"target": target})

    document = db["orders"].find_one({"reference": placed})
    assert document["status"] == "ready"
    assert document["prepared_at"] is not None


def test_cancelling_from_prepping_requires_a_chef_note(client, db, chef, placed):
    _sign_in(client, "chef@example.com")
    order_id = _order_id(db, placed)
    for target in ("confirmed", "prepping"):
        client.post(f"/chef/orders/{order_id}/transition", data={"target": target})

    client.post(
        f"/chef/orders/{order_id}/transition",
        data={"target": "cancelled", "chef_note": "   "},
    )
    assert db["orders"].find_one({"reference": placed})["status"] == "prepping"

    client.post(
        f"/chef/orders/{order_id}/transition",
        data={"target": "cancelled", "chef_note": "Supplier failed on the milk."},
    )
    document = db["orders"].find_one({"reference": placed})
    assert document["status"] == "cancelled"
    assert document["chef_note"] == "Supplier failed on the milk."


def test_a_chef_cancellation_credits_the_charge_back_once(client, db, chef, placed):
    _sign_in(client, "chef@example.com")
    order_id = _order_id(db, placed)

    client.post(
        f"/chef/orders/{order_id}/transition",
        data={"target": "cancelled", "chef_note": "Kitchen closed."},
    )
    # The same form submitted again: the order is already cancelled, the
    # compare-and-set matches nothing, and no second credit is written.
    client.post(
        f"/chef/orders/{order_id}/transition",
        data={"target": "cancelled", "chef_note": "Kitchen closed."},
    )

    entries = list(db["account_ledger"].find({"order_id": order_id}))
    credits = [e for e in entries if e["entry_type"] == LedgerEntryType.CREDIT.value]
    assert len(credits) == 1
    assert sum(entry["amount_cents"] for entry in entries) == 0


def test_a_transition_lost_to_a_race_changes_nothing(client, db, chef, placed):
    """The compare-and-set is the whole protection against a double move."""
    _sign_in(client, "chef@example.com")
    order_id = _order_id(db, placed)

    # The customer cancels between the chef's read and the chef's write.
    db["orders"].update_one({"_id": db["orders"].find_one(
        {"reference": placed})["_id"]}, {"$set": {"status": "cancelled"}})

    client.post(f"/chef/orders/{order_id}/transition", data={"target": "confirmed"})

    assert db["orders"].find_one({"reference": placed})["status"] == "cancelled"


def test_a_transition_returns_to_the_filtered_queue(client, db, chef, placed):
    _sign_in(client, "chef@example.com")
    order_id = _order_id(db, placed)

    response = client.post(
        f"/chef/orders/{order_id}/transition",
        data={"target": "confirmed", "filter_status": "placed"},
    )

    assert "status=placed" in response.headers["Location"]


# --- payment is tracked, never captured -------------------------------------


def test_the_chef_settles_payment_by_hand(client, db, chef, placed):
    _sign_in(client, "chef@example.com")
    order_id = _order_id(db, placed)

    client.post(f"/chef/orders/{order_id}/payment", data={"payment_status": "settled"})

    document = db["orders"].find_one({"reference": placed})
    assert document["payment_status"] == PaymentStatus.SETTLED.value
    # Independent of `status` (04-WORKFLOWS.md): settling moved nothing else.
    assert document["status"] == "placed"


def test_payment_status_survives_a_later_transition(client, db, chef, placed):
    """A transition writes only the fields it touches."""
    _sign_in(client, "chef@example.com")
    order_id = _order_id(db, placed)

    client.post(f"/chef/orders/{order_id}/payment", data={"payment_status": "waived"})
    client.post(f"/chef/orders/{order_id}/transition", data={"target": "confirmed"})

    document = db["orders"].find_one({"reference": placed})
    assert document["payment_status"] == PaymentStatus.WAIVED.value
    assert document["status"] == "confirmed"


def test_an_unknown_payment_status_is_refused(client, db, chef, placed):
    _sign_in(client, "chef@example.com")
    order_id = _order_id(db, placed)

    client.post(f"/chef/orders/{order_id}/payment", data={"payment_status": "paid"})

    assert (
        db["orders"].find_one({"reference": placed})["payment_status"]
        == PaymentStatus.UNPAID.value
    )


def test_an_unknown_order_is_a_404(client, chef):
    _sign_in(client, "chef@example.com")

    assert (
        client.post(
            "/chef/orders/not-an-object-id/transition", data={"target": "confirmed"}
        ).status_code
        == 404
    )


# --- the page works without JavaScript --------------------------------------


def test_every_queue_control_is_a_form_post(client, chef, placed):
    """No script gates any action here (00-SYSTEM.md, 03-FRONTEND.md)."""
    _sign_in(client, "chef@example.com")
    page = client.get("/chef/orders").get_data(as_text=True)

    assert 'method="post"' in page
    assert page.count('name="csrf_token"') >= 2
    assert "onclick" not in page


# --- findings from review, each with the test that failed before the fix ----


def test_only_one_chef_admin_can_exist(app, db, chef):
    """Enforced by the database, not by a read-then-write in a script.

    Two provisioning runs with different addresses would both see no chef
    and both insert; the unique index on `email` does not stop that,
    because the addresses differ.
    """
    from pymongo.errors import DuplicateKeyError

    second = User(
        email="second-chef@example.com",
        password_hash=hash_password(PASSWORD),
        role=Role.CHEF_ADMIN,
    )

    with pytest.raises(DuplicateKeyError):
        db["users"].insert_one(second.to_mongo())


def test_customers_still_share_a_role_freely(app, db, chef):
    """The constraint is partial: it binds `chef_admin` and nothing else."""
    _register(app, "ada@example.com")
    _register(app, "bob@example.com")

    assert db["users"].count_documents({"role": Role.CUSTOMER.value}) == 2


def test_the_queue_bounds_its_read_in_the_query(client, app, db, chef, labneh, monkeypatch):
    """The limit is the database's, not a slice of what was already sent.

    A status filter naming a terminal state can match years of history,
    and every document would otherwise be sorted, transferred and parsed
    into a model — nested lines and allergen snapshots included — only to
    be thrown away.
    """
    monkeypatch.setattr(chef_orders, "QUEUE_LIMIT", 2)
    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    for _ in range(5):
        _place_order(client, labneh)
    client.post("/logout")

    seen: list[int | None] = []
    original = orders_repo.chef_list_order_queue

    def spy(*args, **kwargs):
        seen.append(kwargs.get("limit"))
        return original(*args, **kwargs)

    monkeypatch.setattr(orders_repo, "chef_list_order_queue", spy)

    with app.app_context():
        view = chef_orders.queue_view(chef_orders.QueueFilters())

    # One more than the page shows, so truncation is known without
    # reading the whole match.
    assert seen == [3]
    assert len(view.entries) == 2
    assert view.is_truncated


def test_a_chef_cancellation_is_credited_in_the_chefs_name(client, db, chef, placed):
    """The audit record says who issued the credit, not who owns the order."""
    _sign_in(client, "chef@example.com")
    order_id = _order_id(db, placed)
    chef_id = str(db["users"].find_one({"email": "chef@example.com"})["_id"])

    client.post(
        f"/chef/orders/{order_id}/transition",
        data={"target": "cancelled", "chef_note": "Kitchen closed."},
    )

    credit = db["account_ledger"].find_one({"entry_type": "credit"})
    assert credit["created_by"] == chef_id
    # Still the customer's ledger; only the author differs.
    assert credit["user_id"] != chef_id


def test_a_customer_cancellation_is_still_credited_in_their_own_name(
    client, app, db, chef, labneh
):
    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    reference = _place_order(client, labneh)
    customer_id = str(db["users"].find_one({"email": "ada@example.com"})["_id"])

    client.post(f"/account/orders/{reference}/cancel")

    credit = db["account_ledger"].find_one({"entry_type": "credit"})
    assert credit["created_by"] == customer_id


def test_the_chef_never_inherits_a_guest_cart(client, db, chef, labneh):
    """A cart is a customer's.

    Otherwise the chef signing in on a browser that had been browsing the
    catalogue adopts whatever was in it — and `/checkout`, which asks
    only for a session, would place a real order and write a real ledger
    charge against the administrative account.
    """
    client.post(
        "/cart/add",
        data={"item_type": "component", "item_id": labneh, "quantity": "2"},
    )

    _sign_in(client, "chef@example.com")

    page = client.get("/chef/orders").get_data(as_text=True)
    assert re.search(r"data-cart-count[^>]*>0<", page)

    # And nothing can be ordered in the chef's name.
    client.post("/checkout", data={"requested_for": "2026-12-01", "fulfilment": "collection"})
    chef_id = str(db["users"].find_one({"email": "chef@example.com"})["_id"])
    assert db["orders"].count_documents({"user_id": chef_id}) == 0
    assert db["account_ledger"].count_documents({"user_id": chef_id}) == 0


def test_a_customer_still_keeps_their_guest_cart(client, app, db, labneh):
    """The fix is scoped to the chef and changes nothing for a customer."""
    _register(app, "ada@example.com", display_name="Ada")
    client.post(
        "/cart/add",
        data={"item_type": "component", "item_id": labneh, "quantity": "2"},
    )

    _sign_in(client, "ada@example.com")

    page = client.get("/components").get_data(as_text=True)
    assert re.search(r"data-cart-count[^>]*>2<", page)


def test_a_settlement_recorded_by_mistake_can_be_undone(client, db, chef, placed):
    """`payment_status` is a mutable tracking field, not a ledger.

    04-WORKFLOWS.md has the chef setting `settled` or `waived`, which is
    the ordinary case — but a mis-click has to be correctable, and there
    is no other way back.
    """
    _sign_in(client, "chef@example.com")
    order_id = _order_id(db, placed)

    client.post(f"/chef/orders/{order_id}/payment", data={"payment_status": "settled"})
    client.post(f"/chef/orders/{order_id}/payment", data={"payment_status": "unpaid"})

    assert db["orders"].find_one({"reference": placed})["payment_status"] == "unpaid"


def test_the_form_says_what_choosing_unpaid_means(client, chef, placed):
    _sign_in(client, "chef@example.com")
    page = client.get("/chef/orders").get_data(as_text=True)

    assert "it is a correction, not a refund" in page
