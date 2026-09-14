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
                                             names the widest rendition; the
                                             narrower ones share its stem
image_alt          str | None                the chef's description of the
                                             photograph. Required whenever
                                             image_path is set
image_width        int | None                the widest rendition's intrinsic
image_height       int | None                size, for width/height attributes
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

# --- review staleness ---
ingredients_updated_at  datetime | None      when the ingredients last changed

# --- dishes only ---
component_refs     [str]                     optional provenance links
serves             int
```

### Images

An `image_path` is a **storage-interface path, never a URL**. It is written
only by the catalogue editor, from a file the chef uploads, and it is read
back only through `StorageBackend`. No template builds a filesystem path or
a bucket address: one route serves every rendition, so moving to object
storage changes no catalogue document.

An upload is accepted on the strength of **what is actually in the file**.
It is decoded, and only a JPEG, PNG or WebP raster survives; the
`Content-Type` and the extension are the uploader's to choose and are not
consulted. SVG is refused — it is a document that can carry script, and
these bytes are served back to browsers from this origin.

One upload produces a **ladder of renditions** at fixed widths, sharing one
path stem, so `image_path` names the whole set. The widest one's intrinsic
size is stored as `image_width`/`image_height`, which is what lets the
`width` and `height` attributes be rendered server-side and stop the page
reflowing as images arrive.

`image_alt` is **required whenever an image is stored, and never derived**.
An `alt` defaulted to the item's name would repeat the heading a screen
reader has just read and describe nothing. The four fields move together:
an item with any of them missing renders no image rather than a broken one.

**An item with no image renders no image.** There is no placeholder. The
catalogue card's layout does not depend on one existing.

**An image never appears in an allergen surface** — not the allergen tab,
not an order's consolidated declaration, not the prep sheet. Those are
compliance surfaces, the prep sheet is printed in monochrome, and an image
carries nothing that is not also in text.

### Ingredient

```
name               str       required
quantity           str | None   free text: "2 tbsp", "roughly a handful"
note               str | None   "substitute with X if unavailable"
is_optional        bool
```

Ingredients are ordered, chef-authored, and displayed in authored order. No automatic alphabetisation.

### `ingredients_updated_at` and a stale review

04-WORKFLOWS.md requires that editing the ingredients of an already-reviewed
item flags its allergen block stale and prompts for re-review — without
invalidating the item and without unpublishing it. This timestamp is how that
is stored, and it is the **only** thing stored: staleness is derived, so the
two can never disagree.

```
allergen_review_is_stale  ==  allergens.reviewed_at is not None
                              and ingredients_updated_at is not None
                              and ingredients_updated_at > allergens.reviewed_at
```

It is a field on the **item**, deliberately not a boolean inside
`AllergenBlock`. Allergen fields are never modified by any code path except the
chef allergen editor, and the thing that makes a declaration stale is an edit
made by the *catalogue* editor — a flag in the block would have the catalogue
form writing a compliance field on every save, which is the rule this document
sets two sections down. The catalogue editor writes this timestamp and touches
nothing in `allergens`.

The stamp moves only when the ingredients actually changed. A save that
corrected a price does not demand a re-review of a declaration nobody altered,
and a prompt that fires on every save is a prompt the chef learns to dismiss.

An **unreviewed** item is never "stale". It is unreviewed, which is a different
state with different wording and a harder rule: it cannot be published at all.
A stale item stays available and keeps rendering the declaration it was last
reviewed with — the true statement of what was last checked — while the chef is
told to look at it again.

## Allergens — Australia (FSANZ)

Australian service. Allergen declaration follows FSANZ Standard 1.2.3 / the PEAL requirements. Controlled vocabulary only — **no free-text allergen entry**.

```
AllergenBlock:
  contains          [AllergenCode]   declared present
  may_contain       [AllergenCode]   cross-contact risk
  gluten_cereals    [str]            required if "gluten" in contains
                                     wheat | rye | barley | oats
  tree_nut_species  [str]            required if "tree_nuts" in contains
                                     almond | brazil | cashew | hazelnut | macadamia
                                     | pecan | pine_nut | pistachio | walnut
  sulphites_declared bool            true if ≥10 mg/kg
  chef_note         str | None       free text, additive only, never a substitute
  reviewed_at       datetime | None  null until the chef allergen editor reviews it
  reviewed_by       str | None       null until reviewed; required once reviewed_at is set
```

Both review fields are nullable and move together. An unreviewed block genuinely
has neither, and the pair is enforced by a validator rather than by a non-null
default: a sentinel would put a fabricated reviewer's name on a compliance
record.

`AllergenCode` enum:

```
wheat, gluten, crustacea, mollusc, egg, fish, milk,
peanut, sesame, soy, tree_nuts, lupin, sulphites
```

`wheat` and `gluten` are **two declarations, not one**, because the table to
S9—3 gives them two rows: wheat is declarable irrespective of whether it
contains gluten, and barley, oats and rye are declarable only if they do. A
wheat-containing item that also contains gluten declares both. See
*Verification record* below.

### Retired vocabulary

```
cereals_gluten                      superseded by wheat + gluten
gluten_cereals: spelt               superseded by wheat
```

**Parsed, never written.** Both values are frozen into
`OrderLine.allergen_snapshot` on orders already placed, and a snapshot is never
rewritten — a declaration a customer was given is what they were given. So they
still parse, still render with the wording they were written with, and are
never offered by the allergen editor or accepted from it. `WRITABLE_CODES` and
`WRITABLE_GLUTEN_CEREALS` in `models/allergens.py` are the live vocabulary, and
`AllergenBlock.uses_retired_vocabulary` is how a page says a block predates the
split.

The **browse exclusion filter** offers the live vocabulary only — three
overlapping names for two things is not a choice a customer should be asked to
make — but excluding either `wheat` or `gluten` also matches a stored
`cereals_gluten`, at the query and in the cross-contact caution. That widens an
exclusion beyond what it literally names, which is the safe direction for a
browsing aid to fail; it never rewrites or re-renders a declaration.

Rules, enforced in code:

- Crustacea, mollusc and fish are **three separate declarations**. Never collapsed.
- `gluten` present ⇒ `gluten_cereals` non-empty. Validation error otherwise. The
  retired `cereals_gluten` carries the same rule, so a stored block cannot lose
  its cereals on re-read.
- `tree_nuts` present ⇒ `tree_nut_species` non-empty. Validation error otherwise.
- `gluten` present **and** `wheat` named among `gluten_cereals` ⇒ `wheat` present
  in `contains`. Validation error otherwise. Item 3 makes wheat declarable in its
  own right, so gluten that comes from wheat is two declarations; without this
  rule a block renders "Gluten (wheat)" and never declares the wheat, which is
  the under-declaration the split was made to prevent. Live declarations only —
  a block carrying the retired `cereals_gluten` was written under a vocabulary
  that could not express the distinction, and holding a stored record to a rule
  that did not exist would make it unreadable.
- `sulphites` in `contains` ⇔ `sulphites_declared`. A biconditional, so the flag
  is a **checked mirror** of the `contains` entry rather than a second, quieter
  route to a declaration. Validation error either way round, and it raises rather
  than filling either half in: adding the `contains` entry would author a
  declaration the chef did not make, and Schedule 9 item 1 makes sulphites
  declarable only at ≥10 mg/kg, so an entry with the flag false asserts a
  threshold nobody recorded. `sulphites_threshold_note` qualifies the chip; it
  never stands in for it.

  **On the read path this one rule is relaxed.** It is newer than the schema,
  which permitted the pair to disagree, so documents already stored can carry
  that state — an order's frozen snapshot among them. Refusing to parse one
  would be the failure the retired-vocabulary rules exist to prevent: a
  declaration a customer was given, unreadable because the rules moved. Worse,
  `parse_many` isolates nothing, so one such snapshot would take out a whole
  order history rather than one row. `parse_one` and `parse_many` therefore
  validate with a `stored` context that skips this check and nothing else;
  every write still goes through it, the block is rendered exactly as written
  (`sulphites_threshold_note` still requires both halves, so nothing is
  invented), and `AllergenBlock.sulphites_mirror_disagrees` is what tells the
  chef to repair it. No other rule is relaxed: the rest have always been
  enforced, so no stored document can violate one without having bypassed the
  model entirely.
- `reviewed_at` set ⇒ `reviewed_by` non-empty. Validation error otherwise. An unreviewed block carries `None` in both fields; there is no placeholder reviewer.
- An allergen code is never in both `contains` and `may_contain`. Validation error otherwise — a declared allergen is not simultaneously a cross-contact risk.
- An item with **no** allergen review (`reviewed_at is None`) cannot be published to customers. See *Publication* below.
- The tab renders "No declared allergens" only when the block has been reviewed and `contains` is empty. An unreviewed item never renders that phrase.
- Allergen fields are never modified by any code path except the chef allergen editor.

### Verification record

**Checked 12 September 2026. Acted on 14 September 2026.** Source: the table to
section **S9—3** of *Australia New Zealand Food Standards Code — Schedule 9 —
Mandatory advisory statements and declarations*, **compilation No. 2, in force
25 February 2021, up to Amendment 197** (F2021C00195), read from FSANZ's *Food
Standards Code — Compilation (April 2026)* PDF. That is the PEAL amendment; its
transition period ended 25 February 2024 and its stock-in-trade period ended
25 February 2026, so it is in full force with no remaining concession.

**Column 4 of the table** — the required name for a declaration made outside a
statement of ingredients, which is what this application renders — gives *two*
rows where `AllergenCode` had one:

| Schedule 9 item | Declarable when | Required name (column 4) |
|---|---|---|
| 3 — wheat, and its hybridised strains | always, *irrespective of whether it contains gluten* | `wheat`; and `gluten` as well, if gluten is present |
| 2 — barley, oats, rye, and their hybridised strains | only *if they contain gluten* | `gluten` |

Two discrepancies followed, and both are now closed in code:

1. **`cereals_gluten` collapsed two separate declarations.** It could not
   express wheat present without gluten — which item 3 requires be declared
   anyway — and the name it rendered is not one the table uses. `WHEAT` and
   `GLUTEN` were added; `CEREALS_GLUTEN` was retired.
2. **`spelt` is not a food in the table.** Spelt is of the genus *Triticum*, so
   item 3 covers it and its required name is `wheat`. `GlutenCereal.SPELT` was
   retired.

**Two label changes, not enum changes.** `crustacea` renders as `Crustacean`,
which is the required name; and because S9—3(2)(c) defines mollusc as a *marine*
mollusc, that code renders as `Marine mollusc` rather than leaving a customer to
decide for themselves what counts.

**Neither retired value was deleted**, and this is the part that is a rule
rather than a convenience. Both are frozen into `OrderLine.allergen_snapshot` on
orders already placed, and a snapshot is never rewritten. Deleting them would
break every historical order on read — the customer's own order page, the chef's
queue and the rolled-up summary alike. A declaration a customer was given is
what they were given, whatever the vocabulary has since become. They are
therefore parseable, renderable and unwritable, as *Retired vocabulary* above
sets out.

**No migration.** Catalogue items reviewed before the split keep declaring
`cereals_gluten` until the chef re-reviews them; the chef editor says so, the
browse filter still catches them, and nothing rewrites a declaration on their
behalf — that would be a compliance record authored by a script.

**Otherwise the vocabulary matches.** The nine tree nut species are exactly the
table's; crustacean, mollusc and fish are three separate rows, as this document
already requires; and sulphites are declarable at 10 mg/kg or above.

> This file is a record of a check, not a legal source. Food standards are
> amended. Re-verify against the current compilation before go-live and after
> any amendment.

## Publication

There is **no `is_published` field.** Publication is derived, not stored:

```
published  ==  is_available and not is_archived and allergens.reviewed_at is not None
```

`is_available` and `is_archived` already encode the chef's two independent
decisions, and the allergen review is the compliance gate over both. A stored
fourth flag would be a state that can disagree with the other three, and the
disagreement would be invisible until a customer saw an item the chef believed
was withdrawn.

Enforced in the shared item model: making an item visible to customers while its
allergen block is unreviewed is a validation error, not a runtime filter. The
customer-facing repository reads apply the same predicate as a query
(`is_available: true, is_archived: false`), so an unreviewed item cannot reach a
browse page even if one were written directly to the collection.

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
  storage_snapshot: StorageBlock | None
```

**Snapshotting is mandatory.** Name, price, allergen block and storage block
are copied onto the line at order time. A later catalogue edit must never
retroactively change what a customer was told they were eating, nor how long
they were told to keep it.

`storage_snapshot` is the **whole `StorageBlock`**, not `shelf_life_days`
alone. A date computed from a snapshotted shelf life, sitting beside a method
and temperature the chef has since changed from "refrigerate" to "freeze", is
worse than either alone; the allergen block set the precedent that the whole
compliance block travels with the line.

It is **nullable and never backfilled**. Lines written before the field
existed have no snapshot, and an item with no storage block of its own has
none either. Inventing one from today's catalogue would be exactly the
retroactive edit the snapshot exists to prevent, so there is no migration.
The customer's use-by (`prepared_at + shelf_life_days`, shortest across
lines — 04-WORKFLOWS.md) is computed **only when every line carries a
snapshot**: the shortest of the remaining lines would be a date that does not
cover the whole order. Otherwise the order page points at the item's current
guidance, which is what it did before the field existed.

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
orders:         user_id+created_at desc, status, requested_for, reference (unique)
account_ledger: user_id+created_at
```
