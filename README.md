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

## Stylesheet

`app/static/css/app.css` is compiled output and is committed, so
deployment needs no Node runtime. Rebuild it after any template or
`tailwind.css` change:

```bash
npm install
npm run css        # or: npm run css:watch
```

CI fails if the committed stylesheet is out of date with its source.

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

## What is built so far

The application skeleton, the Pydantic schema of record, the repository
layer with its tenancy contract, the order state machine, and two
proof-of-life routes: `GET /` (server-rendered, works with JavaScript
disabled) and `GET /health` (liveness plus a database round-trip).

The catalogue browse pages, item detail view, cart, checkout and
chef-admin screens are not built yet.
