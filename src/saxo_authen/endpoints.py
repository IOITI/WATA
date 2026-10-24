"""
Where Saxo's login is asked for, and where its codes and tokens are turned
into tokens, for each environment: written here, never read from the
database. Every container connects to PostgreSQL as the same user and sealing
proves nothing of who wrote a row, so an address taken from a row could send
the next code, or the app's secret, elsewhere.

The address Saxo sends the browser back to with a code (the redirect) is the
server's: ``SAXO_REDIRECT_URL`` in ``.env``, handed to the admin and to the
four services that exchange codes. It must be one the app registers in Saxo's
developer portal: the admin's own ``/api/saxo/callback`` takes the code
there. Without it, the first redirect address of the app object in
config.json, as before (the code is then pasted).
"""

import os

ENVIRONMENTS = ("live", "simulation")

ENDPOINTS = {
    "live": {
        "authorize": "https://live.logonvalidation.net/authorize",
        "token": "https://live.logonvalidation.net/token",
    },
    "simulation": {
        "authorize": "https://sim.logonvalidation.net/authorize",
        "token": "https://sim.logonvalidation.net/token",
    },
}

REDIRECT_VARIABLE = "SAXO_REDIRECT_URL"
CALLBACK_PATH = "/api/saxo/callback"


def environment_of(config_manager) -> str:
    environment = config_manager.get_config_value("saxo_auth.env", "live")
    if environment not in ENVIRONMENTS:
        raise ValueError(f"saxo_auth.env must be one of {', '.join(ENVIRONMENTS)}")
    return environment


def redirect_url(config_manager, environ=None) -> str | None:
    """The redirect of the server's, else the app object's first one (config.json), else None."""
    environ = os.environ if environ is None else environ
    pinned = (environ.get(REDIRECT_VARIABLE) or "").strip()
    if pinned:
        return pinned
    app = config_manager.get_config_value("authentication.saxo.app_config_object") or {}
    redirects = app.get("RedirectUrls") or []
    return redirects[0] if redirects else None
