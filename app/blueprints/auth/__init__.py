"""auth blueprint: register, sign in, sign out.

`login_manager.login_view` points at `auth.login`, and checkout is the
first surface that requires an account, so the redirect it configures is
now reachable.

Password reset is deliberately absent. A reset is delivered — by email —
and 04-WORKFLOWS.md keeps notifications out of v1 entirely. A reset flow
with no delivery channel would be a form that appears to work and does
not, so the chef resets a password out of band until there is something
to send a token over. `security/tokens.py` is not that channel: it issues
API access tokens to a client that already has the password.
"""

from flask import Blueprint

bp = Blueprint("auth", __name__)

from app.blueprints.auth import routes  # noqa: E402,F401
