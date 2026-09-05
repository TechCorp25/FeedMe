# FeedMe

A private meal-preparation service application: a chef-authored catalogue
of components and complete dishes, full allergen declarations on every
item, customer ordering, and a chef order queue.

The specification lives in [`docs/`](docs/) and is authoritative:

| Doc | Covers |
|---|---|
| [`00-SYSTEM.md`](docs/00-SYSTEM.md) | Role, locked stack, non-negotiables, quality gates |
| [`01-DOMAIN.md`](docs/01-DOMAIN.md) | Collections, item shape, allergen model, orders, ledger |
| [`02-ARCHITECTURE.md`](docs/02-ARCHITECTURE.md) | Flask layout, layering, repository contract, auth, config |
| [`03-FRONTEND.md`](docs/03-FRONTEND.md) | Tailwind, responsive and orientation rules, tabbed item view |
| [`04-WORKFLOWS.md`](docs/04-WORKFLOWS.md) | Ordering flows, order state machine, chef-admin flows |

## Stack

Python 3.11+ · Flask (app factory + blueprints) · Jinja2 · MongoDB via
PyMongo · Pydantic v2 · Tailwind CSS compiled with the Tailwind CLI ·
vanilla ES modules, no bundler.

## Local setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env      # then fill in SECRET_KEY and JWT_SECRET
```

A MongoDB instance must be reachable at `MONGO_URI`. The application
applies its index bootstrap at startup and refuses to boot if the
database is unreachable.

```bash
flask --app wsgi run
```

## Design system

The interface follows the **"Butcher's Label"** system: a condensed stamped
label over a calm serif description, on a kraft-toned ground. Tokens, type
scale, the two deliberate deviations and the reasoning are recorded in
[`docs/03-FRONTEND.md`](docs/03-FRONTEND.md#visual-language--butchers-label).

Both type families are self-hosted from `app/static/fonts/` under the SIL Open
Font License 1.1, with each licence beside the font it covers. There is no CDN
at runtime and no network access needed at build time.

`tests/test_design_system.py` guards the parts that fail silently: a colour
token defined for one theme only, a text pair dropping under 4.5:1, a font
reference that resolves to nothing, and the allergen chips being told apart by
colour alone.

## Stylesheet

`app/static/css/app.css` is compiled output and is committed, so
deployment needs no Node runtime. Rebuild it after any template or
`tailwind.css` change:

```bash
npm install
npm run css        # or: npm run css:watch
```

CI fails if the committed stylesheet is out of date with its source.

`tests/test_stylesheet_cascade.py` reads the compiled file and resolves the
cascade over it. It exists because a template test cannot catch a stylesheet
rule that out-ranks the user-agent sheet: Tailwind's preflight resets the
`hidden` attribute through `:where()`, which contributes no specificity, so
any `@apply` that sets `display` on a component primitive — `.btn`, `.chip`
and `.tab` all do — would otherwise leave a `hidden` control painted on the
page. Every class in the compiled sheet that declares `display` is discovered
and checked, so a primitive added later is covered automatically.

## Tests

```bash
python -m pytest
```

Tests run against `mongomock`, never a live database. Coverage centres on
the areas the specification names: allergen validators, tenancy
isolation, order state transitions, and integer-cent price arithmetic.

```bash
python scripts/check_boot.py
```

Boots the application and prints every route with its auth marker. It
exits non-zero if any endpoint lacks one — the same check that runs
inside `create_app`.

## Continuous integration

`.github/workflows/ci.yml` runs three jobs on every pull request:

| Check name | Fails when |
|---|---|
| `pytest` | any test fails |
| `route-marker check` | an endpoint lacks an explicit auth marker |
| `compiled stylesheet is current` | `app.css` is out of date with `tailwind.css` |

These are advisory until they are named as required status checks in the
branch protection rule for `main`. Until then a red build does not stop a
merge, and the checks report rather than enforce.

## What is built so far

The application skeleton, the Pydantic schema of record, the repository
layer with its tenancy contract, the order state machine, and the
read-only public catalogue:

| Route | |
|---|---|
| `GET /` | landing page, server-rendered |
| `GET /health` | liveness plus a database round-trip |
| `GET /components` | components browse, filtered by a plain GET form |
| `GET /components/<slug>` | one component, all four tab panels in the HTML |
| `GET /dishes` | dishes browse, filtered by meal type and preference |
| `GET /dishes/<slug>` | one dish, all four tab panels plus provenance links |

Next slice: **the cart.** `/menu/<meal_type_slug>`, the third ordering entry
point in `04-WORKFLOWS.md`, and the allergen exclusion filter offered on all
three are both still to come.

Not built: cart, checkout, the customer account area, and every chef-admin
screen — the catalogue editors, the allergen editor, the order queue, the
prep sheet and the ledger.
