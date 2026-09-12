#!/usr/bin/env python
"""Provision the single `chef_admin` account.

01-DOMAIN.md allows exactly one `chef_admin` in normal operation and
`services/accounts.py` deliberately refuses to create one through a
public form — the role is not a parameter of registration. This script is
the deliberate path, and it is a script rather than a route so there is
no endpoint on the running application that can mint an administrator.

Credentials are read from the environment or from an interactive prompt.
Nothing is written to the repository and nothing is echoed back:

    CHEF_ADMIN_EMAIL=chef@example.com python scripts/create_chef_admin.py

Run without `CHEF_ADMIN_PASSWORD` and the password is asked for twice,
unechoed. It refuses to create a second `chef_admin`, and it refuses to
promote an existing customer — an account that has placed orders and
holds a ledger is not the account that administers them.
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo.errors import DuplicateKeyError  # noqa: E402

from app import create_app  # noqa: E402
from app.config import load_config  # noqa: E402
from app.db.client import get_db  # noqa: E402
from app.db.repositories import users as users_repo  # noqa: E402
from app.models.users import Role, User  # noqa: E402
from app.security.passwords import hash_password  # noqa: E402
from app.services import accounts  # noqa: E402


def _read_email() -> str:
    email = accounts.normalise_email(
        os.environ.get("CHEF_ADMIN_EMAIL") or input("Chef email: ")
    )
    if not accounts.EMAIL_PATTERN.match(email):
        raise SystemExit("that does not look like an email address")
    return email


def _read_password() -> str:
    password = os.environ.get("CHEF_ADMIN_PASSWORD")
    if password is None:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Password again: "):
            raise SystemExit("the two passwords do not match")
    if not accounts.MIN_PASSWORD_LENGTH <= len(password) <= accounts.MAX_PASSWORD_LENGTH:
        raise SystemExit(
            f"the password must be at least {accounts.MIN_PASSWORD_LENGTH} "
            f"characters and at most {accounts.MAX_PASSWORD_LENGTH}"
        )
    return password


def main() -> int:
    # The index bootstrap runs first, and it is what actually enforces
    # both invariants: `email` is unique, and a partial unique index on
    # `role` allows exactly one `chef_admin`. The read below is a
    # courtesy that produces a readable message in the ordinary case; it
    # is not what makes the rule hold. Two runs with different addresses
    # would both pass it, and the second insert is refused by the index.
    app = create_app(load_config())
    with app.app_context():
        existing = get_db()["users"].find_one({"role": Role.CHEF_ADMIN.value})
        if existing is not None:
            print(
                f"a chef_admin already exists ({existing['email']}); "
                "01-DOMAIN.md allows one",
                file=sys.stderr,
            )
            return 1

        email = _read_email()
        if users_repo.get_user_by_email(email) is not None:
            print(
                f"{email} is already registered as a customer; "
                "provision the chef on an address of its own",
                file=sys.stderr,
            )
            return 1

        password = _read_password()
        try:
            user = users_repo.create_user(
                User(
                    email=email,
                    password_hash=hash_password(password),
                    display_name="Chef",
                    role=Role.CHEF_ADMIN,
                )
            )
        except users_repo.EmailAlreadyRegistered:
            print(f"{email} is already registered", file=sys.stderr)
            return 1
        except DuplicateKeyError:
            # The partial unique index on `role` refused it: another run
            # created the chef between the read above and this write.
            print(
                "a chef_admin already exists; 01-DOMAIN.md allows one",
                file=sys.stderr,
            )
            return 1

    print(f"chef_admin created: {user.email}")
    print("Sign in at /login; the chef lands on /chef/orders.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
