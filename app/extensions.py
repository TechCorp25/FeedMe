"""Flask extension singletons, created unbound and initialised in the factory."""

from __future__ import annotations

from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.session_protection = "strong"

csrf = CSRFProtect()
