# 04-WORKFLOWS.md — Flows and State

## Three ordering entry points

All three converge on one cart and one order model. They differ only in how the customer discovers items.

**1. Component ordering** — `/components`
Browse dressings, sauces, purées, sides, proteins, bases. Filter by category, preference flags, spice level, allergen exclusion. Add any component directly to cart at its own unit and price.

**2. Complete dish ordering** — `/dishes`
Browse finished meals. Each dish is self-contained and priced whole. Referenced components display as provenance ("contains our harissa purée") and link to the component page; they are **not** separately added to the cart and not separately priced.

**3. Meal-type ordering** — `/menu/<meal_type_slug>`
Dishes grouped by `meal_type_ids` — breakfast, lunch, dinner, snack. Same cards, same detail pages. A dish may appear under several meal types.

**Allergen exclusion filter** is available on all three. Selecting "exclude peanut" hides any item whose `allergens.contains` includes it. Items with `may_contain` are shown with a visible caution marker rather than hidden — hiding them would imply the filter is a safety guarantee. The filter UI states plainly that it is a browsing aid, not a medical safeguard.

## Cart

- Server-side cart keyed to session for guests, to `user_id` once authenticated. A guest cart merges into the user cart on login.
- Cart lines store `item_type`, `item_id` and `quantity` only. Price and allergen data resolve live from the catalogue until checkout.
- JS updates the cart via `POST /api/cart` and re-renders the cart badge. Non-JS falls back to a form POST that redirects back to the referring page with the anchor preserved.
- Cart never silently drops an item. If an item becomes unavailable or archived, the cart shows it struck through with an explicit message; checkout is blocked until the customer removes it.

*Decided by the cart and auth slices:* the cart lives in the signed session
cookie and carries the `user_id` it belongs to. 01-DOMAIN.md names six
collections and a cart is not one of them, so "keyed to `user_id`" is a stamp
on the stored cart rather than a seventh collection: a cart stamped for
somebody else is never read, sign-out clears it, and the merge on login folds
the guest cart into the customer's. What that costs is a cart that does not
follow a customer to another device, which is the price of not inventing a
collection the domain does not have. A line that will not fit the merge is
reported to the customer, never dropped.

## Checkout

1. Review lines, quantities, subtotal.
2. Choose `requested_for` date and `collection` or `delivery`.
3. Optional customer note.
4. Confirm.

On confirm, atomically:

- Snapshot `name`, `unit_price_cents` and the full `allergen_snapshot` onto each `OrderLine`.
- Compute `subtotal_cents` and `total_cents` in integer cents. No floats anywhere in pricing.
- Generate `reference` (`MP-YYMM-NNNN`, unique).
- Set `status = "placed"`, `payment_status = "unpaid"`.
- Write a `charge` entry to `account_ledger`.
- Clear the cart.

No payment is taken. No provider is contacted. Do not add one.

*Decided by the checkout slice:* "atomically" is honoured where it can be. The
order is one document and its insert is one atomic write, so an order never
exists half-priced or half-snapshotted. The ledger entry is a second document
in a second collection, and a two-collection transaction needs a replica set —
which a workstation, a single-node deployment and the test suite's mongomock
do not have. The order is therefore written first and the entry second,
carrying `order_id`: the remaining failure is a charge missing from a ledger,
which is detectable, repairable by appending the entry, and never a customer
charged for an order that does not exist. It is logged at ERROR.

*Also decided here:* `fulfilment = "delivery"` needs an address, and 01-DOMAIN.md
keeps the address on the customer rather than on the order. What the customer
types at checkout is therefore saved to `users.delivery_address` and the chef
reads the current one; an order carries no address of its own. The address is
written **before** the order, so a failure to save it leaves nothing behind
rather than a delivery order with nowhere to deliver to.

*Three more rules the confirmation keeps:*

- The review page shows each item's **current allergen declaration** — the one
  about to be frozen onto the order. A declaration can have changed since the
  customer read the item's page, and showing it only afterwards would be a
  declaration made after the decision.
- The form carries a **single-use token** and a **digest of what it showed**.
  The token is spent when an order is written, so a double-clicked confirm
  shows the order it already placed instead of writing a second one; the digest
  covers every line, quantity and unit price, so a cart changed in another tab
  — or a price edited while the page sat open — re-renders the page rather than
  charging for an order nobody reviewed. Two requests racing before either
  replies still share one cookie and are not covered: that needs a durable
  idempotency key on the order document, which is this document's to grant.
- `requested_for` is validated against **the kitchen's own date**
  (`BUSINESS_TIMEZONE`), not UTC. Stored timestamps stay UTC; a date a customer
  picks is local, and for the ten hours between Melbourne midnight and UTC
  midnight the two differ.

A month's references are `MP-YYMM-0001` to `MP-YYMM-9999`. That ceiling is
enforced rather than overflowed: a five-digit sequence sorts below `-9999` in
the lexical read that draws the next number, so the counter would repeat itself
and every checkout for the rest of the month would fail with nothing saying
why. Exhausting it refuses the order loudly instead.

## Order state machine

```
placed → confirmed → prepping → ready → collected
                                     └→ delivered
placed → cancelled
confirmed → cancelled
prepping → cancelled       (chef only; requires chef_note)
ready → cancelled          (chef only; requires chef_note)
collected / delivered      terminal
cancelled                  terminal
```

Rules:

- Transitions live in `services/order_state.py` as an explicit allowed-transition map. Any transition not in the map raises. Never set `status` by direct assignment on the document.
- Every transition appends to `status_history` with `{status, at, by}`.
- `prepared_at` is stamped on entry to `ready`. Customer-facing use-by is `prepared_at + shelf_life_days` per line, showing the **shortest** across lines.
- Customer may cancel only from `placed` or `confirmed`. After that, chef only.
- Cancellation writes a `credit` entry to `account_ledger` offsetting the original charge.
- `payment_status` moves independently of `status`. The chef sets `settled` or `waived` manually.

## Customer account

- `/account` — profile, delivery address, dietary notes, default preference filters.
- `/account/orders` — order history, newest first, with live status for non-terminal orders.
- `/account/orders/<reference>` — full detail, snapshotted allergen data as shown at order time, storage and use-by guidance.
- `/account/balance` — ledger entries and running balance, computed by aggregation.

All routes scoped by `user_id` at the repository. Requesting another customer's order returns 404, never 403.

*Decided by the account slice:*

- **One order, one page.** `/orders/<reference>`, which checkout redirected
  to before this area existed, is a permanent redirect to
  `/account/orders/<reference>`. Two pages rendering one order drift the
  moment either gains a control the other lacks, and the cancel button is
  that control.
- **Cancellation is a compare-and-set.** `order_state.py` decides the
  transition and `orders.apply_transition` writes it filtered on the status
  it was decided against, so a confirmation clicked twice — or a chef
  moving the same order on at that moment — cancels once and credits once.
  The transition is written first and the offsetting `credit` second: a
  credit written first would stand alone if the transition then lost its
  race, crediting a customer for an order still being prepared. The
  remaining failure is a cancellation whose credit is missing, which is
  detectable by `order_id`, repairable by appending the entry, and logged
  at ERROR — the mirror of the one checkout carries.
- **Live status is an enhancement.** `GET /api/orders/<reference>/status` is
  the second of the two JSON surfaces 00-SYSTEM.md allows. Every status is
  rendered server-side on first request, and the poller stops asking about
  an order that has reached a terminal status.
- **Saved preference filters need a marker.** `default_preference_filters`
  pre-applies to a browse page arrived at with nothing stated. A GET form
  submitted with every box cleared sends no `preference` at all, which the
  server cannot tell from a fresh arrival, so the filter form carries a
  hidden `filtered=1` and every "clear" link sets it: a URL that states its
  filters is taken literally. A page narrowed by the defaults says so and
  offers the unfiltered catalogue, because a shortened list that does not
  explain itself reads as the whole catalogue.
- **Sign-out empties the session, not just the login.** `logout_user` ends
  the login; a message flashed but never rendered, and the open checkout's
  `last_order_reference`, both survived it and reached whoever signed in
  next on that browser. Both are cleared at sign-out now.

*Deferred, and owned by 01-DOMAIN.md:* **the use-by date.** This document
computes it as `prepared_at + shelf_life_days` per line, shortest across
lines. `OrderLine` snapshots the name, the unit price and the allergen
block, and not the `StorageBlock` — so the shelf life is only readable from
the catalogue as it stands now, which the chef may have edited since the
order was prepared. A use-by that has been *lengthened* under a customer is
the one direction this must never fail in, so the order page points at the
item's current guidance rather than computing a date it cannot stand
behind. Closing it means adding a storage snapshot to `OrderLine`, which is
a change to the order document 01-DOMAIN.md owns. Decide the direction
there: either the whole `StorageBlock` is snapshotted like the allergen
block, or `shelf_life_days` alone is.

## Chef-admin flows

Single `chef_admin` account. Full capability, no sub-roles.

**Order queue** — `/chef/orders`
Default view: non-terminal orders sorted by `requested_for` ascending. Filter by status and date. Each order shows customer name, dietary notes, lines, and a consolidated allergen summary for the whole order. One-click transitions along the allowed map.

**Prep sheet** — `/chef/prep/<date>`
Aggregates all orders for a date into a component-level pick list — quantities rolled up across dishes and standalone components. Printable, plain layout, no dependence on colour.

**Catalogue editors** — `/chef/components`, `/chef/dishes`
Full CRUD. Create, edit, archive, reorder, toggle availability. Dish editor includes optional component linking.

**Allergen editor** — a deliberately separate step, not a section of the main item form.

- Opened explicitly from the item editor.
- Requires the chef to confirm the declaration before saving; sets `reviewed_at` and `reviewed_by`.
- Displays the rollup warning when a linked component declares an allergen the dish omits. The warning is advisory. The chef resolves it; the system never auto-applies it.
- An item cannot be published while `reviewed_at is None`.
- Editing ingredients on an already-reviewed item flags the allergen block as stale and surfaces a re-review prompt. It does not silently invalidate the item, and it does not unpublish it — it prompts.

*Deferred, owned by this editor:* `sulphites_declared` and `contains` are not
cross-validated. A block can set the flag without listing `sulphites` in
`contains`, and the customer page deliberately renders nothing in that case —
`AllergenBlock.sulphites_threshold_note` qualifies a declaration and never
invents one. That leaves the defect visible to nobody. The rule belongs here,
in the one code path allowed to write the block, and as a model validator
alongside the `cereals_gluten` and `tree_nuts` rules in 01-DOMAIN.md. It was
not added with the read-only catalogue slice because a cross-validator changes
the model contract, and a browse-and-detail change has no business altering
compliance semantics. Decide the direction when building this editor: either
the flag requires the `contains` entry, or setting the flag adds it.

**Ledger** — `/chef/customers/<user_id>/ledger`
View entries, add manual `adjustment` or `credit` entries with a description. Entries are append-only; corrections are new offsetting entries, never edits or deletes.

## Notifications

Out of scope for v1. Do not add email or SMS sending. Design the order state machine so a transition hook can be attached later without restructuring.
