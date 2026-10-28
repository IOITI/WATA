"""
The credentials WATA keeps sealed in PostgreSQL: who reads each (the only
services it is sealed for), its parts, the public ones shown and the secret
ones never, how each part is checked when it is set, where the code reads it
in the configuration, and where it was kept before (read when its row is
absent, until the old places are cleared).

Never here, and never in PostgreSQL: the database's own password, RabbitMQ's,
the keys, the control service's token, Traefik's certificates. A credential
the database needs to be reached cannot be kept in it.
"""

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# Services that send Telegram messages themselves when RabbitMQ does not take them (AsyncTelegramSender).
TELEGRAM_READERS = ("telegram", "trader", "position_monitor", "price_recorder", "capital_feed", "ta_engine", "admin")
# The endpoints of the web server other WATA services call with the internal token.
INTERNAL_READERS = ("web_server", "trader", "scheduler", "ta_engine", "admin")
# The services that log in to Saxo: its apps and its tokens are sealed for them (src/saxo_authen/).
SAXO_READERS = ("trader", "position_monitor", "watchlist_manager", "price_recorder")
SAXO_ENVIRONMENTS = ("live", "simulation")

TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{24,128}$")


@dataclass(frozen=True)
class Part:
    key: str
    label: str
    secret: bool
    pattern: re.Pattern | None = None
    # What is expected, said when a value is refused.
    expected: str = ""
    max_length: int = 256


@dataclass(frozen=True)
class Credential:
    name: str
    label: str
    readers: tuple[str, ...]
    parts: tuple[Part, ...]
    # Where its parts are laid over the configuration in memory, for the code that reads them there.
    overlay: dict[str, str] = field(default_factory=dict)
    # The secret part whose last four characters are shown, to tell two values apart.
    fingerprint: str | None = None
    # Made by WATA (a token), never typed: rotated, the value before kept for a while.
    generated: bool = False
    # Where it was kept before PostgreSQL, said on the page while its row is absent.
    old_place: str = ""

    @property
    def public_parts(self) -> tuple[str, ...]:
        return tuple(part.key for part in self.parts if not part.secret)


CREDENTIALS = (
    Credential(
        name="telegram.bot",
        label="Telegram bot",
        readers=TELEGRAM_READERS,
        parts=(
            Part("token", "Bot token", secret=True, pattern=re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,64}$"),
                 expected="as BotFather gives it: the bot's number, a colon, then about 35 letters, digits, - and _"),
            Part("chat_id", "Chat id", secret=False, pattern=re.compile(r"^(-?\d{1,20}|@[A-Za-z0-9_]{5,32})$"),
                 expected="a number (negative for a group), or @ and a channel's name"),
        ),
        overlay={"token": "telegram.bot_token", "chat_id": "telegram.chat_id"},
        fingerprint="token",
        old_place="config.json (telegram.bot_token, telegram.chat_id)",
    ),
    Credential(
        name="webhook.token",
        label="TradingView webhook token",
        readers=("web_server", "admin"),
        parts=(Part("token", "Token", secret=True, pattern=TOKEN_PATTERN, expected="24 to 128 letters, digits, - and _"),),
        fingerprint="token",
        generated=True,
        old_place="var/lib/web_server (the token file, encrypted with webserver.app_secret)",
    ),
    Credential(
        name="internal.token",
        label="Internal token",
        readers=INTERNAL_READERS,
        parts=(Part("token", "Token", secret=True, pattern=TOKEN_PATTERN, expected="24 to 128 letters, digits, - and _"),),
        fingerprint="token",
        generated=True,
        old_place="var/lib/web_server (the TradingView webhook's token, which it was until v0.43.0)",
    ),
    Credential(
        name="capital.login",
        label="Capital.com login",
        readers=("capital_feed",),
        parts=(
            Part("api_key", "API key", secret=True, pattern=re.compile(r"^\S{8,128}$"),
                 expected="the key as Capital.com shows it once, without spaces"),
            Part("identifier", "Login", secret=False, pattern=re.compile(r"^\S{3,254}$"),
                 expected="the email (or login) of the account, without spaces", max_length=254),
            Part("password", "Key's password", secret=True, pattern=re.compile(r"^.{1,128}$"),
                 expected="the password chosen for the key, not the account's"),
        ),
        fingerprint="api_key",
        old_place=".env (CAPITAL_API_KEY, CAPITAL_IDENTIFIER, CAPITAL_PASSWORD)",
    ),
    Credential(
        name="heartbeat.url",
        label="Outside watcher",
        readers=("position_monitor",),
        parts=(
            Part("url", "Ping address", secret=True, pattern=re.compile(r"^https://[^\s/?#]+/\S*$"),
                 expected="an https:// address, as the watcher (healthchecks.io...) gives it", max_length=2048),
        ),
        fingerprint="url",
        old_place=".env (WATA_HEARTBEAT_URL)",
    ),
    Credential(
        name="anthropic.api",
        label="Claude API key",
        # The admin's AI agent (src/admin/agent/) alone calls the Claude API; it was never kept anywhere else.
        readers=("admin",),
        parts=(
            Part("key", "API key", secret=True, pattern=re.compile(r"^sk-ant-[A-Za-z0-9_-]{16,200}$"),
                 expected="the key as Anthropic's Console gives it once: sk-ant- then letters, digits, - and _"),
        ),
        fingerprint="key",
    ),
)


def _saxo_app(environment: str) -> Credential:
    return Credential(
        name=f"saxo.{environment}.app",
        label=f"Saxo {environment} app",
        readers=SAXO_READERS,
        parts=(
            Part("app_key", "App key", secret=False, pattern=re.compile(r"^[A-Za-z0-9]{16,64}$"),
                 expected="the app's key, as Saxo's developer portal shows it: letters and digits"),
            Part("app_secret", "App secret", secret=True, pattern=re.compile(r"^[A-Za-z0-9]{16,64}$"),
                 expected="the app's secret, as Saxo's developer portal shows it: letters and digits"),
        ),
        fingerprint="app_secret",
        old_place="config.json (authentication.saxo.app_config_object), for the environment saxo_auth.env names",
    )


CREDENTIALS = CREDENTIALS + tuple(_saxo_app(environment) for environment in SAXO_ENVIRONMENTS)

BY_NAME = {credential.name: credential for credential in CREDENTIALS}


def of_role(role: str) -> list[Credential]:
    """The credentials ``role`` reads."""
    return [credential for credential in CREDENTIALS if role in credential.readers]


def checked(name: str, given: dict) -> dict:
    """
    ``given`` as it will be sealed: each part present and as expected, nothing
    else. ValueError says which part and what is expected, never the value.
    """
    credential = BY_NAME.get(name)
    if credential is None:
        raise ValueError(f"{name} is not one of WATA's credentials")
    if not isinstance(given, dict):
        raise ValueError(f"{credential.label}: its parts are expected as an object")
    unknown = set(given) - {part.key for part in credential.parts}
    if unknown:
        raise ValueError(f"{credential.label} has no {', '.join(sorted(unknown))}")
    value = {}
    for part in credential.parts:
        text = given.get(part.key)
        if isinstance(text, int) and not isinstance(text, bool):
            text = str(text)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{credential.label}: {part.label.lower()} is missing")
        text = text.strip() if part.key != "password" else text
        if len(text) > part.max_length or (part.pattern is not None and not part.pattern.match(text)):
            raise ValueError(f"{credential.label}: {part.label.lower()} is not as expected ({part.expected})")
        value[part.key] = text
    return value


def fingerprint(name: str, value: dict) -> str | None:
    """The last four characters of its secret part: enough to tell two values apart, never to use one."""
    credential = BY_NAME[name]
    secret = value.get(credential.fingerprint) if credential.fingerprint else None
    return secret[-4:] if isinstance(secret, str) and len(secret) >= 12 else None


def public_of(name: str, value: dict) -> dict:
    """What of a value is shown: its public parts, and the host of an address (never its path, the secret)."""
    credential = BY_NAME[name]
    public = {key: value[key] for key in credential.public_parts if key in value}
    if name == "heartbeat.url" and isinstance(value.get("url"), str):
        public["host"] = urlsplit(value["url"]).hostname
    return public


def old_value(name: str, config_manager, environ=None) -> dict | None:
    """The value at its place of before PostgreSQL, or None. Read only while its row is absent."""
    environ = os.environ if environ is None else environ
    if name == "telegram.bot":
        token = config_manager.get_config_value("telegram.bot_token")
        chat_id = config_manager.get_config_value("telegram.chat_id")
        return {"token": token, "chat_id": str(chat_id)} if token and chat_id not in (None, "") else None
    if name in ("webhook.token", "internal.token"):
        from src.web_server_token import WebServerToken

        token = WebServerToken(config_manager).stored_token()
        return {"token": token} if token else None
    if name == "capital.login":
        values = [(environ.get(variable) or "").strip() for variable in ("CAPITAL_API_KEY", "CAPITAL_IDENTIFIER", "CAPITAL_PASSWORD")]
        return dict(zip(("api_key", "identifier", "password"), values)) if all(values) else None
    if name == "heartbeat.url":
        url = (environ.get("WATA_HEARTBEAT_URL") or "").strip()
        return {"url": url} if url else None
    if name.startswith("saxo.") and name.endswith(".app"):
        # The one app config.json held, that of the environment the services ran in.
        if name != f"saxo.{config_manager.get_config_value('saxo_auth.env', 'live')}.app":
            return None
        app = config_manager.get_config_value("authentication.saxo.app_config_object") or {}
        key, secret = app.get("AppKey"), app.get("AppSecret")
        return {"app_key": key, "app_secret": secret} if key and secret else None
    return None
