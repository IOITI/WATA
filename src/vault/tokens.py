"""
The web server's two tokens (``webhook.token``, TradingView's ``?token=``;
``internal.token``, the Bearer of the other services), made by WATA and
rotated: the token before stays accepted for a while, the time to restart
the services that send it, or to change TradingView's alerts.
"""

import secrets
from datetime import datetime, timezone

# How long the token before a rotation is still accepted.
GRACE_SECONDS = {
    # TradingView's alerts are changed by hand, one by one.
    "webhook.token": 3600,
    # The services that send it are restarted by the admin in a few minutes.
    "internal.token": 900,
}


def new_token() -> str:
    """256 bits, in the 43 characters URLs and headers carry as they are."""
    return secrets.token_urlsafe(32)


def rotated(current: dict | None, now: datetime | None = None) -> dict:
    """A new token, the current one kept as the one before."""
    now = now or datetime.now(timezone.utc)
    return {
        "token": new_token(),
        "previous": (current or {}).get("token"),
        "rotated_at": now.isoformat(),
    }


def accepted(name: str, value: dict | None, now: datetime | None = None) -> tuple[str, ...]:
    """The tokens the web server accepts now for ``name``: the current one, and the one before for a while."""
    if not value or not value.get("token"):
        return ()
    tokens = [value["token"]]
    previous, rotated_at = value.get("previous"), value.get("rotated_at")
    if previous and rotated_at:
        now = now or datetime.now(timezone.utc)
        try:
            since = (now - datetime.fromisoformat(rotated_at)).total_seconds()
        except (TypeError, ValueError):
            since = None
        if since is not None and 0 <= since < GRACE_SECONDS.get(name, 0):
            tokens.append(previous)
    return tuple(tokens)
