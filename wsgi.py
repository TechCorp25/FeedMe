"""WSGI entrypoint.

Every platform starts the application here: `gunicorn wsgi:app` on
Railway and Render, `flask --app wsgi run` locally and in a Codespace.

`.env` is loaded before the factory runs, because gunicorn — unlike
`flask run` — does not read it, and a variable that only exists under one
of the two start commands is a difference between environments waiting to
be debugged. Real environment variables always win: `load_dotenv` does
not overwrite what the platform has already set.
"""

from dotenv import load_dotenv

load_dotenv(override=False)

from app import create_app  # noqa: E402 — configuration must load first

application = create_app()
app = application
