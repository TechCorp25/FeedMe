# 05-DEPLOYMENT.md — Environments and deployment

One codebase, four hosts: a workstation, a GitHub Codespace, Railway and
Render. Nothing in `app/` names a host. What differs between them is
detected at boot from the variables each platform sets, and everything
detected can be overridden by stating it explicitly.

## Detection

`app/deployment.py` identifies the host and derives four facts from it.

| Platform | Recognised by | External origin from | Port | Default `FLASK_ENV` |
|---|---|---|---|---|
| `local` | nothing else matched | — | `PORT`, else 5000 | `development` |
| `codespaces` | `CODESPACES`, `CODESPACE_NAME` | `CODESPACE_NAME` + port + `GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN` | `PORT`, else 5000 | `development` |
| `railway` | `RAILWAY_ENVIRONMENT`, `RAILWAY_ENVIRONMENT_NAME`, `RAILWAY_SERVICE_ID` | `RAILWAY_PUBLIC_DOMAIN`, else `RAILWAY_STATIC_URL` | `PORT`, else 8080 | `production` |
| `render` | `RENDER`, `RENDER_SERVICE_ID` | `RENDER_EXTERNAL_URL`, else `RENDER_EXTERNAL_HOSTNAME` | `PORT`, else 10000 | `production` |

Detection is checked in that order and stops at the first match, so a
Codespace that happens to carry a platform CLI's variables is still a
Codespace. `DEPLOY_PLATFORM` forces a result, which is how a platform's
URL and cookie behaviour is reproduced on a workstation, and the escape
hatch if a host renames a variable.

Three consequences follow from detection, and each can be overridden:

- **`BASE_URL`** comes from the platform when it is not set. In
  production it is still required — but Railway and Render answer for it,
  so a deploy does not have to restate a URL the platform assigned. A
  workstation publishes no external origin, so it can never satisfy that
  requirement.
- **`SESSION_COOKIE_SECURE`** defaults on wherever the platform serves
  HTTPS. Production sets it regardless.
- **`TRUST_PROXY_HEADERS`** defaults on for exactly the three hosts that
  terminate TLS in front of the process, and `create_app` then reads
  `X-Forwarded-*` for one hop. Off anywhere else: a forwarded header no
  proxy is guaranteed to have written is a claim by the client, and
  trusting it lets a caller choose the scheme and host the app believes.

Without the forwarded headers, an app behind an HTTPS edge sees plain
HTTP on an internal hostname: external URLs come out as `http://`, and a
`Secure` session cookie is dropped by the browser that was just given it.

## Required variables

Production refuses to start when any of these is missing, and never
falls back to a development secret (`02-ARCHITECTURE.md`).

| Variable | Notes |
|---|---|
| `SECRET_KEY` | Render generates one via `render.yaml`; set it yourself elsewhere |
| `JWT_SECRET` | as above |
| `MONGO_URI` | the only value the platform cannot derive |
| `MONGO_DB_NAME` | an Atlas SRV URI usually carries no database name, so state it |
| `BASE_URL` | derived on Railway and Render; set it for a custom domain |

Run `python scripts/env_report.py` on any host to see what was detected,
what the configuration resolved to, and which secrets are still
placeholders. It prints no secret values and touches no network.

`GET /health` reports the same detection plus a real database
round-trip, and returns 503 when the database cannot be reached. Railway
and Render are both configured to gate a deploy on it, so a release that
cannot reach its database never replaces a working one.

## MongoDB Atlas

Atlas is reached over `mongodb+srv://`, which resolves through DNS SRV
records — hence `dnspython` in `requirements.txt`. Two things must be
true for every host that connects:

1. The Atlas **network access list** includes that host. A workstation, a
   Codespace and a Railway or Render service are different addresses,
   and each is a separate entry. Railway and Render do not publish a
   fixed egress IP on their lower tiers.
2. `MONGO_DB_NAME` is set, because the connection string usually names no
   database.

The connection string is a credential. It belongs in `.env` (git-ignored)
locally, in a Codespaces secret, and in the platform's own variable store
in production — never in this repository. Rotate it in Atlas the moment
it is pasted anywhere shared.

## GitHub Codespaces

`.devcontainer/devcontainer.json` builds a Python 3.11 container, installs
`requirements-dev.txt` and the Tailwind CLI, and forwards port 5000.

```bash
cp .env.example .env      # fill in SECRET_KEY, JWT_SECRET, MONGO_URI
flask --app wsgi run --host 0.0.0.0 --port 5000
```

Bind `0.0.0.0`: Codespaces forwards from outside the container, so a
server listening only on the loopback address is not reachable. Set
`MONGO_URI` as a **Codespaces secret** (Settings → Codespaces → Secrets)
rather than in `.env`, so it survives a rebuild and is not typed into a
file. Forwarded ports are private by default; making one public exposes
the app to anyone with the URL.

## Railway

`railway.json` sets the start command, the `/health` check and the
restart policy. Nixpacks reads `.python-version` and installs
`requirements.txt`.

1. New project → deploy from this repository.
2. Set `SECRET_KEY`, `JWT_SECRET`, `MONGO_URI`, `MONGO_DB_NAME` as service
   variables. `FLASK_ENV` is optional — Railway is detected, so
   production is the default.
3. Generate a domain. `RAILWAY_PUBLIC_DOMAIN` then appears in the
   environment and becomes `BASE_URL` on the next deploy.

A service with no public domain is reachable only on the private network
and advertises no external origin, so production has no `BASE_URL` to
derive: set one, or generate the domain.

## Render

`render.yaml` is a Blueprint: **New → Blueprint**, point it at this
repository. It generates `SECRET_KEY` and `JWT_SECRET`, and marks
`MONGO_URI` and `MONGO_DB_NAME` `sync: false`, so Render asks for them
once and stores them on the service instead of in this file.

`RENDER_EXTERNAL_URL` supplies `BASE_URL`. On the free instance type the
service sleeps when idle and the first request after that pays the cold
start, which includes the index bootstrap and the Atlas handshake.

## Start command

Every platform starts the same process:

```
gunicorn --bind "0.0.0.0:${PORT:-5000}" --workers "${WEB_CONCURRENCY:-2}" \
         --timeout 60 --access-logfile - wsgi:app
```

`wsgi.py` loads `.env` before the factory runs — `flask run` does that on
its own and gunicorn does not, and a variable that exists under only one
of the two start commands is a difference between environments waiting to
be debugged. Real environment variables always win over the file.
