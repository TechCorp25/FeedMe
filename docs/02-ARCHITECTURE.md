# 02-ARCHITECTURE.md — Application Architecture

## Layout

```
app/
  __init__.py            create_app() factory
  config.py              env-driven config classes
  deployment.py          platform detection (local, Codespaces, Railway, Render)
  extensions.py          login_manager, mongo client, csrf
  db/
    client.py            MongoClient singleton, get_db()
    indexes.py           idempotent index bootstrap
    repositories/        one module per collection
      users.py
      components.py
      dishes.py
      orders.py
      ledger.py
      meal_types.py
  models/                Pydantic v2 models — the schema of record
    base.py
    catalogue.py         ItemBase, Component, Dish, Ingredient
    allergens.py         AllergenCode, AllergenBlock, validators
    orders.py
    users.py
  blueprints/
    public/              landing, catalogue browse, item detail
    auth/                login, logout, register, password reset
    account/             customer profile, order history, ledger
    order/               cart, checkout, order tracking
    chef/                catalogue CRUD, order queue, allergen editor
    api/                 JSON: cart mutation, order status polling
  services/              business logic between blueprint and repository
    cart.py
    pricing.py
    order_state.py
    allergen_rollup.py
  security/
    decorators.py        login_required wrappers, chef_required
    tokens.py            JWT issue/verify (future mobile client)
    passwords.py         hashing
  storage/
    base.py              StorageBackend interface
    local.py             filesystem implementation
  templates/
  static/
    css/tailwind.css     source
    css/app.css          compiled output, committed
    js/
tailwind.config.js
wsgi.py
```

## Layering rule

`blueprint → service → repository → PyMongo`

- Blueprints never touch PyMongo. Blueprints never see raw dicts.
- Repositories return Pydantic models, never cursors or dicts.
- Services hold business rules. A blueprint function longer than ~30 lines is a signal that logic belongs in a service.
- Templates receive Pydantic models or plain primitives.

## Repository contract

Every function touching customer-owned data takes `user_id` as a **required, non-defaulted** first argument:

```python
def get_order(user_id: str, order_id: str) -> Order | None: ...
def list_orders(user_id: str, limit: int = 20) -> list[Order]: ...
```

Chef-scope access uses separately named functions so the intent is visible at the call site:

```python
def chef_get_order(order_id: str) -> Order | None: ...
def chef_list_order_queue(status: OrderStatus | None = None) -> list[Order]: ...
```

Never add an `all_users: bool` flag or an optional `user_id`. A tenancy bypass must be a distinct function name.

## Auth

**Web: sessions.** Flask-Login, server-side session cookie, `HttpOnly`, `Secure`, `SameSite=Lax`. CSRF token required on every state-changing form POST.

**Future mobile: JWT.** `security/tokens.py` issues short-lived access tokens against the same `users` collection. Build the module and a `/api/auth/token` route now; the web app does not use it. Access tokens carry `sub` (user id) and `role`. No refresh-token rotation until a mobile client actually exists — stub it, do not design it speculatively.

**Roles.** Two only: `customer`, `chef_admin`. `chef_admin` implies full administrative capability; there is no separate admin role and no other staff.

**Decorators.** Every route carries exactly one of:

```python
@login_required          # any authenticated user
@chef_required           # chef_admin only
@public_route            # explicit marker; no auth
```

An unmarked route is a defect. Enforce with a startup check that iterates `app.url_map` and fails to boot if any endpoint lacks a marker.

**Passwords.** Argon2 (preferred) or bcrypt. Never MD5/SHA family. Reset tokens are single-use, time-limited, stored hashed.

**Isolation test.** The test suite includes a standing test: for every customer-facing route taking an object id, authenticate as user B and request user A's object. Expect 404 (not 403 — do not confirm existence).

## Configuration

12-factor. No hardcoded hosts, no cloud-provider assumptions in application code.

```
FLASK_ENV                 development | production   (detected default)
SECRET_KEY                required in production, no default
MONGO_URI                 required
MONGO_DB_NAME             required
JWT_SECRET                required
SESSION_COOKIE_SECURE     true in production        (detected default)
STORAGE_BACKEND           local
STORAGE_LOCAL_PATH
BASE_URL                  required in production    (detected default)
DEPLOY_PLATFORM           local | codespaces | railway | render, forces detection
PORT                      port to bind              (detected default)
TRUST_PROXY_HEADERS       read X-Forwarded-*        (detected default)
```

Production config asserts every required variable is present **when the config object is constructed** — `load_config()` / `ProductionConfig()`, which `create_app` calls before anything else touches the environment — and refuses to start otherwise. Never fall back to a development secret.

`app/deployment.py` detects the host — a workstation, a GitHub Codespace, Railway or Render — from the variables that host sets, and supplies the defaults marked above: the external origin, the port, whether TLS is terminated upstream, and which `FLASK_ENV` to assume. This is not a cloud-provider assumption in application code: nothing branches on the platform, the platform only answers questions about itself, and every answer is overridden by stating the variable explicitly. A managed host defaults to production, so a deploy that omits `FLASK_ENV` gets the hardened configuration and fails loudly on a missing secret rather than serving a development one on a public URL. `05-DEPLOYMENT.md` covers the per-platform detail.

The assertion is deliberately not at import time. `import app.config` must succeed in a shell, in tooling and during test collection without a full production environment; an import-time assertion would make the configuration tests themselves unrunnable. Construction is the earliest point that still leaves the module importable.

## Errors and logging

- Structured logging (JSON in production). Never log passwords, tokens, session ids or full customer addresses.
- Customer-facing error pages: 404, 403, 500 — plain, styled, no stack traces.
- Any allergen validation failure logs at WARNING with item id and the failing rule.

## Testing

- `pytest` with `mongomock` or a disposable test database. Never test against a live database.
- Required coverage areas: allergen model validators, tenancy isolation, order state transitions, price arithmetic (integer cents, no floats anywhere).
- Every bug fix ships with a test that fails before the fix.
