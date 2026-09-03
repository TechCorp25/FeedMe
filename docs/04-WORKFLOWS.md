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
