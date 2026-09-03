# 01-DOMAIN.md — Data Model

MongoDB. All documents validated by Pydantic v2 models. `_id` is `ObjectId`, exposed to templates and JSON as a string `id`.

## Collections

| Collection | Purpose | Owner |
|---|---|---|
| `users` | Customers and the chef-admin account | system |
| `components` | Sellable building blocks: dressings, sauces, purées, sides, proteins, bases | chef |
| `dishes` | Complete, self-contained meals | chef |
| `meal_types` | Breakfast, lunch, dinner, snack, etc. — ordered, renameable | chef |
| `orders` | Customer orders and their progress | customer + chef |
| `account_ledger` | Per-customer cost entries | system |

## Two catalogues

`components` and `dishes` are **separate catalogues** with separate browse pages, separate admin editors and separate ordering flows. Both are sellable directly.

A dish **may** reference components (`component_refs: [ObjectId]`) — for provenance, kitchen prep and "contains our harissa" style display.

**A dish's own tabs are authoritative.** The dish carries its own complete `ingredients`, `allergens`, `storage`, and `preparation`. Referenced components never substitute for, merge into, or override those fields at render time.

Rollup is used **only** as an authoring-time warning: if a referenced component declares an allergen absent from the dish, the chef editor surfaces a warning. It never mutates the dish document. The chef resolves it manually.

## Shared item shape

`components` and `dishes` share this core shape. Implement as a Pydantic base model, subclassed per collection.

```
id                 str
name               str                       required
slug               str                       unique, indexed
summary            str                       one line, catalogue card
description        str                       long form
category           str                       component: dressing|sauce|puree|side|protein|base|other
                                             dish:      chef-defined
meal_type_ids      [str]                     dishes only; a dish may span several
image_path         str | None                storage-interface path, not a URL
price_cents        int                       integer minor units, AUD. Never float.
unit               str                       "each" | "100g" | "portion" | "250ml"
is_available       bool                      soft availability toggle
is_archived        bool                      archived items never appear to customers
sort_order         int
created_at         datetime
updated_at         datetime

# --- the four tabs ---
ingredients        [Ingredient]              tab 1
allergens          AllergenBlock             tab 2
storage            StorageBlock              tab 3
preparation        PreparationBlock          tab 4

# --- non-compliance taste metadata ---
preference_flags   [str]                     see below
spice_level        int                       0-5

# --- dishes only ---
component_refs     [str]                     optional provenance links
serves             int
```

### Ingredient

```
name               str       required
quantity           str | None   free text: "2 tbsp", "roughly a handful"
note               str | None   "substitute with X if unavailable"
is_optional        bool
```

Ingredients are ordered, chef-authored, and displayed in authored order. No automatic alphabetisation.

## Allergens — Australia (FSANZ)

Australian service. Allergen declaration follows FSANZ Standard 1.2.3 / the PEAL requirements. Controlled vocabulary only — **no free-text allergen entry**.

```
AllergenBlock:
  contains          [AllergenCode]   declared present
  may_contain       [AllergenCode]   cross-contact risk
  gluten_cereals    [str]            required if "cereals_gluten" in contains
                                     wheat | rye | barley | oats | spelt
  tree_nut_species  [str]            required if "tree_nuts" in contains
                                     almond | brazil | cashew | hazelnut | macadamia
                                     | pecan | pine_nut | pistachio | walnut
  sulphites_declared bool            true if ≥10 mg/kg
  chef_note         str | None       free text, additive only, never a substitute
  reviewed_at       datetime
  reviewed_by       str
```

`AllergenCode` enum:

```
cereals_gluten, crustacea, mollusc, egg, fish, milk,
peanut, sesame, soy, tree_nuts, lupin, sulphites
```

Rules, enforced in code:

- Crustacea, mollusc and fish are **three separate declarations**. Never collapsed.
- `cereals_gluten` present ⇒ `gluten_cereals` non-empty. Validation error otherwise.
- `tree_nuts` present ⇒ `tree_nut_species` non-empty. Validation error otherwise.
- An item with **no** allergen review (`reviewed_at is None`) cannot be published to customers.
- The tab renders "No declared allergens" only when the block has been reviewed and `contains` is empty. An unreviewed item never renders that phrase.
- Allergen fields are never modified by any code path except the chef allergen editor.

> Verify the enum against the current text of FSANZ Standard 1.2.3 before go-live. The list above reflects the PEAL requirements but food standards are amended; treat this file as a starting point, not a legal source.

## Preference flags — not allergens

Taste and dietary preferences live in a **separate field** so compliance labelling is never diluted by preference data. Filterable at browse time alongside, but visually distinct from, allergens.

```
preference_flags   [str]   controlled but chef-extensible:
                           chilli, garlic, coriander, onion, dairy_free,
                           vegetarian, vegan, high_protein, low_carb

spice_level        int     0 = none
                           1 = mild
                           2 = medium
                           3 = hot
                           4 = very hot
                           5 = extreme
```

The UI must never render a preference flag inside the allergen tab, and never render an allergen as a preference chip.

## StorageBlock

```
method             str     refrigerate | freeze | ambient | reheat_from_frozen
temperature_c      str     "0-4" | "-18 or below"
shelf_life_days    int     from date of preparation
shelf_life_note    str     "3 days once opened"
freezable          bool
freezer_life_days  int | None
```

Customer-facing use-by is computed as `order.prepared_at + shelf_life_days`, never stored on the catalogue item.

## PreparationBlock

```
steps              [str]         ordered, one instruction per entry
reheat_method      str | None    oven | microwave | pan | none
reheat_minutes     int | None
reheat_note        str | None
serving_suggestion str | None
```

## users

```
id, email (unique, lowercased, indexed), password_hash,
display_name, phone, delivery_address, role ("customer" | "chef_admin"),
is_active, created_at, last_login_at,
dietary_notes: str | None            free text, visible to chef on orders
default_preference_filters: [str]    pre-applies browse filters
```

Exactly one `chef_admin` in normal operation. There is no other staff role.

## orders

```
id, user_id (indexed), reference (human-readable, e.g. "MP-2409-0134"),
status (see 04-WORKFLOWS.md), lines: [OrderLine],
subtotal_cents, total_cents,
payment_status ("unpaid" | "settled" | "waived"),   no capture, tracking only
requested_for: date, fulfilment ("collection" | "delivery"),
customer_note, chef_note,
prepared_at: datetime | None,
status_history: [{status, at, by}],
created_at, updated_at

OrderLine:
  item_type ("component" | "dish")
  item_id, name_snapshot, unit_price_cents, quantity, line_total_cents
  allergen_snapshot: AllergenBlock
```

**Snapshotting is mandatory.** Name, price and allergen block are copied onto the line at order time. A later catalogue edit must never retroactively change what a customer was told they were eating.

## account_ledger

```
id, user_id (indexed), order_id | None,
entry_type ("charge" | "credit" | "adjustment"),
amount_cents (signed), description, created_at, created_by
```

Running balance is computed by aggregation, never stored as a mutable field.

## Indexes

Declared in one bootstrap module, applied at startup, idempotent:

```
users:          email (unique)
components:     slug (unique), category, is_archived+is_available, preference_flags
dishes:         slug (unique), meal_type_ids, is_archived+is_available, preference_flags
orders:         user_id+created_at desc, status, reference (unique)
account_ledger: user_id+created_at
```
