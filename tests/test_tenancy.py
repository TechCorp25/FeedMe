"""Tenancy isolation, enforced in the repository layer.

Two things are tested: the contract (customer-facing repository functions
take a required, non-defaulted `user_id` first) and the behaviour (user B
cannot read user A's documents by any of them).
"""

from __future__ import annotations

import inspect

import pytest

from app.db.repositories import ledger as ledger_repo
from app.db.repositories import orders as orders_repo
from app.models.orders import LedgerEntry, LedgerEntryType, Order, OrderStatus

USER_A = "aaaaaaaaaaaaaaaaaaaaaaaa"
USER_B = "bbbbbbbbbbbbbbbbbbbbbbbb"

#: Customer-facing repository functions. `chef_*` and `system_*` names are
#: the explicit, separately named scopes outside the customer contract and
#: are excluded by construction: `chef_*` reads a customer's data as
#: somebody who is not that customer, `system_*` reads across every
#: customer and returns no customer data at all. Both are visible as such
#: at the call site, which is the point of naming them.
CUSTOMER_SCOPED_MODULES = (orders_repo, ledger_repo)

SCOPE_PREFIXES = ("chef_", "system_")


def _customer_scoped_functions():
    for module in CUSTOMER_SCOPED_MODULES:
        for name, function in vars(module).items():
            if name.startswith("_") or name.startswith(SCOPE_PREFIXES):
                continue
            if inspect.isfunction(function) and function.__module__ == module.__name__:
                yield module.__name__, name, function


def test_customer_repository_functions_take_user_id_first():
    functions = list(_customer_scoped_functions())
    assert functions, "no customer-scoped repository functions were found"

    for module_name, name, function in functions:
        parameters = list(inspect.signature(function).parameters.values())
        first = parameters[0]
        assert first.name == "user_id", (
            f"{module_name}.{name} must take user_id as its first argument"
        )
        assert first.default is inspect.Parameter.empty, (
            f"{module_name}.{name} must not default user_id"
        )


def test_the_reference_counter_returns_a_reference_and_nothing_else(app, db):
    """The one cross-customer read, held to what it is allowed to return.

    `system_highest_reference` exists so a new order can be numbered, and
    it draws over every customer's orders. It is only defensible while it
    hands back a reference string: a document would be one customer's
    order handed to another.
    """
    with app.app_context():
        orders_repo.create_order(
            USER_A,
            Order(
                user_id=USER_A,
                reference="MP-2609-0001",
                subtotal_cents=0,
                total_cents=0,
            ),
        )
        highest = orders_repo.system_highest_reference("MP-2609-")

    assert highest == "MP-2609-0001"
    assert isinstance(highest, str)


def test_no_repository_offers_an_all_users_flag():
    """A tenancy bypass is a distinct function name, never a flag."""
    for module_name, name, function in _customer_scoped_functions():
        parameters = inspect.signature(function).parameters
        assert "all_users" not in parameters, f"{module_name}.{name}"


@pytest.fixture()
def seeded_order(app, db):
    with app.app_context():
        return orders_repo.create_order(
            USER_A,
            Order(user_id=USER_A, reference="MP-2609-0001"),
        )


def test_other_customer_cannot_read_an_order_by_id(app, seeded_order):
    with app.app_context():
        assert orders_repo.get_order(USER_A, seeded_order.id) is not None
        assert orders_repo.get_order(USER_B, seeded_order.id) is None


def test_other_customer_cannot_read_an_order_by_reference(app, seeded_order):
    with app.app_context():
        assert orders_repo.get_order_by_reference(USER_A, "MP-2609-0001") is not None
        assert orders_repo.get_order_by_reference(USER_B, "MP-2609-0001") is None


def test_listing_never_leaks_another_customers_orders(app, seeded_order):
    with app.app_context():
        assert [o.reference for o in orders_repo.list_orders(USER_A)] == [
            "MP-2609-0001"
        ]
        assert orders_repo.list_orders(USER_B) == []


def test_a_transition_cannot_be_written_to_another_customers_order(
    app, seeded_order
):
    """The write is scoped like every read, and it says it did nothing.

    A view that filtered by `user_id` itself would still be wrong here:
    the update has to *not match*, so a caller cannot cancel somebody
    else's order by handing this function their order.
    """
    cancelled = seeded_order.model_copy(
        update={"status": OrderStatus.CANCELLED, "status_history": []}
    )

    with app.app_context():
        assert (
            orders_repo.apply_transition(
                USER_B,
                cancelled.model_copy(update={"user_id": USER_B}),
                expected_status=OrderStatus.PLACED,
            )
            is False
        )
        assert orders_repo.get_order(USER_A, seeded_order.id).status is (
            OrderStatus.PLACED
        )


def test_a_transition_only_applies_from_the_status_it_was_decided_against(
    app, seeded_order
):
    """The compare-and-set that stops a double cancellation."""
    cancelled = seeded_order.model_copy(update={"status": OrderStatus.CANCELLED})

    with app.app_context():
        assert (
            orders_repo.apply_transition(
                USER_A, cancelled, expected_status=OrderStatus.PLACED
            )
            is True
        )
        # The order is no longer `placed`, so the same write applies again
        # to nothing.
        assert (
            orders_repo.apply_transition(
                USER_A, cancelled, expected_status=OrderStatus.PLACED
            )
            is False
        )


def test_malformed_id_reads_as_not_found(app, db):
    """A malformed id is 'not found', never an error a view could tell apart."""
    with app.app_context():
        assert orders_repo.get_order(USER_A, "not-an-object-id") is None


def test_create_order_rejects_a_mismatched_user_id(app, db):
    with app.app_context():
        with pytest.raises(ValueError, match="does not match"):
            orders_repo.create_order(
                USER_B, Order(user_id=USER_A, reference="MP-2609-0002")
            )


def test_ledger_is_scoped_and_balance_is_aggregated(app, db):
    with app.app_context():
        ledger_repo.append_entry(
            USER_A,
            LedgerEntry(
                user_id=USER_A,
                entry_type=LedgerEntryType.CHARGE,
                amount_cents=1250,
                description="Order MP-2609-0001",
                created_by="system",
            ),
        )
        ledger_repo.append_entry(
            USER_A,
            LedgerEntry(
                user_id=USER_A,
                entry_type=LedgerEntryType.CREDIT,
                amount_cents=-250,
                description="Adjustment",
                created_by="chef@example.com",
            ),
        )
        ledger_repo.append_entry(
            USER_B,
            LedgerEntry(
                user_id=USER_B,
                entry_type=LedgerEntryType.CHARGE,
                amount_cents=9999,
                description="Someone else's order",
                created_by="system",
            ),
        )

        assert len(ledger_repo.list_recent_entries(USER_A)) == 2
        assert ledger_repo.balance_cents(USER_A) == 1000
        assert ledger_repo.balance_cents(USER_B) == 9999


def test_chef_bypass_is_a_separately_named_function():
    assert hasattr(orders_repo, "chef_get_order")
    assert "user_id" not in inspect.signature(orders_repo.chef_get_order).parameters
