"""
Authentication for the admin interface.

One operator password, stored as a scrypt hash in a file outside the config,
in-memory sessions and a per-client login throttle. Sessions do not survive a
restart of the admin container, which only means logging in again.
"""

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import struct
import time
from urllib.parse import quote, urlsplit

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "wata_admin_session"
# With this prefix a browser only accepts the cookie over HTTPS, for this exact
# host and for the whole site: a page of a sibling subdomain cannot set one.
SECURE_SESSION_COOKIE_NAME = f"__Host-{SESSION_COOKIE_NAME}"
MIN_PASSWORD_LENGTH = 12
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_SCRYPT_PARAMS = {"n": 2 ** 14, "r": 8, "p": 1}
_SCRYPT_KEY_LENGTH = 32

# Time-based one-time codes (RFC 6238), as authenticator apps generate them.
TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
# A code of the previous or next period is accepted, for clocks that are slightly off.
TOTP_WINDOW = 1


def _derive_key(password: str, salt: bytes, params: dict) -> bytes:
    return hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=params["n"],
        r=params["r"],
        p=params["p"],
        dklen=_SCRYPT_KEY_LENGTH,
    )


def generate_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def totp_code(secret: str, counter: int) -> str:
    """The code of the ``counter``-th period for ``secret`` (base32)."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** TOTP_DIGITS).zfill(TOTP_DIGITS)


def matching_totp_counter(secret: str, code: str, now: float) -> int | None:
    """The period ``code`` is valid for around ``now``, or None when it is not valid."""
    code = "".join(str(code).split())
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    current = int(now // TOTP_PERIOD_SECONDS)
    for counter in range(current - TOTP_WINDOW, current + TOTP_WINDOW + 1):
        if hmac.compare_digest(totp_code(secret, counter), code):
            return counter
    return None


def totp_uri(secret: str, issuer: str = "WATA Admin", account: str = "operator") -> str:
    """The otpauth:// address authenticator apps read (as a QR code or typed in)."""
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
    )


class CredentialStore:
    """
    The operator password, kept as a salted scrypt hash in a 0600 file, and the
    secret of the optional second factor.
    """

    def __init__(self, path: str):
        self.path = path
        # The last one-time code period accepted: a code is not accepted twice.
        self._last_totp_counter = -1

    def is_configured(self) -> bool:
        return os.path.exists(self.path)

    def _read(self) -> dict | None:
        try:
            with open(self.path, "r") as credentials_file:
                record = json.load(credentials_file)
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    def _write(self, record: dict) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temp_path = f"{self.path}.tmp"
        # Created with 0600 from the start, so its content is never world-readable.
        file_descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(file_descriptor, "w") as credentials_file:
            json.dump(record, credentials_file)
        os.replace(temp_path, self.path)

    def version(self) -> int | None:
        """Changes whenever the password does, which is what closes the sessions opened with the old one."""
        try:
            return os.stat(self.path).st_mtime_ns
        except OSError:
            return None

    def set_password(self, password: str) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long")

        salt = os.urandom(16)
        record = {
            "algorithm": "scrypt",
            **_SCRYPT_PARAMS,
            "salt": salt.hex(),
            "hash": _derive_key(password, salt, _SCRYPT_PARAMS).hex(),
        }
        # Changing the password does not switch the second factor off.
        totp_secret = (self._read() or {}).get("totp_secret")
        if totp_secret:
            record["totp_secret"] = totp_secret

        self._write(record)
        logger.info("Admin password updated")

    def verify(self, password: str) -> bool:
        record = self._read()
        try:
            params = {name: int(record[name]) for name in _SCRYPT_PARAMS}
            expected = bytes.fromhex(record["hash"])
            candidate = _derive_key(password, bytes.fromhex(record["salt"]), params)
        except (ValueError, KeyError, TypeError) as e:
            logger.error(f"Unable to read the admin credentials file: {e!r}")
            return False
        return hmac.compare_digest(candidate, expected)

    # ── second factor ──

    def totp_enabled(self) -> bool:
        return bool((self._read() or {}).get("totp_secret"))

    def enable_totp(self) -> str:
        """Turn the second factor on (or replace its secret); returns the new secret."""
        record = self._read()
        if record is None:
            raise ValueError("Set the admin password before enabling the second factor")
        record["totp_secret"] = generate_totp_secret()
        self._write(record)
        logger.info("Admin second factor enabled")
        return record["totp_secret"]

    def disable_totp(self) -> None:
        record = self._read()
        if record is None or "totp_secret" not in record:
            return
        del record["totp_secret"]
        self._write(record)
        logger.info("Admin second factor disabled")

    def verify_totp(self, code: str, now: float | None = None) -> bool:
        secret = (self._read() or {}).get("totp_secret")
        if not secret:
            return False
        try:
            counter = matching_totp_counter(secret, code, time.time() if now is None else now)
        except (ValueError, TypeError) as e:
            logger.error(f"Unable to use the stored second-factor secret: {e!r}")
            return False
        if counter is None or counter <= self._last_totp_counter:
            return False
        self._last_totp_counter = counter
        return True


class SessionStore:
    """
    In-memory sessions with an idle timeout and an absolute lifetime.

    The absolute lifetime matters because the interface polls: an open tab would
    otherwise keep an idle-only session alive forever.
    """

    def __init__(self, idle_seconds: float, max_age_seconds: float, clock=time.monotonic):
        self._idle_seconds = idle_seconds
        self._max_age_seconds = max_age_seconds
        self._clock = clock
        # token -> (created_at, last_seen_at, credential_version)
        self._sessions: dict[str, tuple[float, float, int | None]] = {}

    def _is_alive(self, created_at: float, last_seen_at: float, now: float) -> bool:
        return now - last_seen_at < self._idle_seconds and now - created_at < self._max_age_seconds

    def create(self, credential_version: int | None = None) -> str:
        now = self._clock()
        self._sessions = {
            token: session for token, session in self._sessions.items() if self._is_alive(session[0], session[1], now)
        }
        token = secrets.token_urlsafe(32)
        self._sessions[token] = (now, now, credential_version)
        return token

    def is_alive(self, token: str, credential_version: int | None = None) -> bool:
        """Whether ``token`` is a live session, without counting as activity."""
        session = self._sessions.get(token)
        if session is None:
            return False
        created_at, last_seen_at, session_credential_version = session
        return session_credential_version == credential_version and self._is_alive(
            created_at, last_seen_at, self._clock()
        )

    def touch(self, token: str, credential_version: int | None = None) -> bool:
        """
        Return whether ``token`` is a live session opened with the current
        password, and record the activity if so.
        """
        session = self._sessions.get(token)
        if session is None:
            return False
        created_at, last_seen_at, session_credential_version = session
        now = self._clock()
        if session_credential_version != credential_version or not self._is_alive(created_at, last_seen_at, now):
            del self._sessions[token]
            return False
        self._sessions[token] = (created_at, now, session_credential_version)
        return True

    def revoke(self, token: str) -> None:
        self._sessions.pop(token, None)


class LoginThrottle:
    """Locks a client out for a while after too many consecutive failed logins."""

    MAX_TRACKED_CLIENTS = 10000

    def __init__(self, max_failures: int = 5, lockout_seconds: float = 900, clock=time.monotonic):
        self._max_failures = max_failures
        self._lockout_seconds = lockout_seconds
        self._clock = clock
        # client -> (consecutive failures, locked until)
        self._clients: dict[str, tuple[int, float]] = {}

    def retry_after(self, client: str) -> int:
        """Seconds the client still has to wait (0 when it may try)."""
        _, locked_until = self._clients.get(client, (0, 0.0))
        remaining = locked_until - self._clock()
        return int(remaining) + 1 if remaining > 0 else 0

    def record_failure(self, client: str) -> None:
        now = self._clock()
        failures, locked_until = self._clients.get(client, (0, 0.0))
        failures += 1
        if failures >= self._max_failures:
            failures, locked_until = 0, now + self._lockout_seconds
        if client not in self._clients and len(self._clients) >= self.MAX_TRACKED_CLIENTS:
            self._clients.clear()
        self._clients[client] = (failures, locked_until)

    def record_success(self, client: str) -> None:
        self._clients.pop(client, None)


def session_cookie_name(secure: bool) -> str:
    return SECURE_SESSION_COOKIE_NAME if secure else SESSION_COOKIE_NAME


def _as_ip(address: str):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    return getattr(ip, "ipv4_mapped", None) or ip


async def _resolve_host(name: str) -> list[str]:
    found = await asyncio.get_running_loop().getaddrinfo(name, None)
    return [info[4][0] for info in found]


class TrustedProxies:
    """
    The reverse proxies whose X-Forwarded-For header is believed.

    Given as addresses, networks or host names. Names (the Traefik container)
    are resolved in the background and looked up again regularly, since a
    container gets a new address when it is recreated: nothing is resolved while
    a request is being answered.
    """

    REFRESH_SECONDS = 30
    RESOLVE_TIMEOUT_SECONDS = 5

    def __init__(self, hosts=(), resolve=_resolve_host):
        if isinstance(hosts, str):
            hosts = [hosts]
        self._resolve = resolve
        self._networks = []
        self._names: list[str] = []
        for host in hosts:
            host = str(host).strip()
            if not host:
                continue
            try:
                self._networks.append(ipaddress.ip_network(host, strict=False))
            except ValueError:
                self._names.append(host)
        self._resolved: dict[str, frozenset] = {}
        # Names that did not resolve at the last attempt: reported once, not at every attempt.
        self._failing: set[str] = set()

    def is_trusted(self, address: str) -> bool:
        ip = _as_ip(address)
        if ip is None:
            return False
        if any(ip.version == network.version and ip in network for network in self._networks):
            return True
        return any(ip in addresses for addresses in self._resolved.values())

    async def refresh(self) -> None:
        for name in self._names:
            try:
                found = await asyncio.wait_for(self._resolve(name), timeout=self.RESOLVE_TIMEOUT_SECONDS)
                addresses = frozenset(filter(None, (_as_ip(address) for address in found)))
            except (OSError, asyncio.TimeoutError) as e:
                if name not in self._failing:
                    self._failing.add(name)
                    # The addresses known so far are kept: the proxy may just be restarting.
                    consequence = (
                        "its last known address is kept"
                        if self._resolved.get(name)
                        else "its X-Forwarded-For header is ignored until it resolves"
                    )
                    logger.warning(f"Unable to resolve the reverse proxy '{name}' ({e!r}): {consequence}")
                continue
            self._failing.discard(name)
            if addresses != self._resolved.get(name):
                logger.info(f"Reverse proxy '{name}' is at {', '.join(sorted(map(str, addresses))) or 'no address'}")
            self._resolved[name] = addresses

    async def keep_refreshed(self) -> None:
        """Background task of the application."""
        while self._names:
            await self.refresh()
            await asyncio.sleep(self.REFRESH_SECONDS)


def client_ip(request: Request, trusted_proxies: TrustedProxies) -> str:
    """
    The address of the caller, for the throttle and the audit log.

    X-Forwarded-For is only believed when the request comes from the reverse
    proxy: anything else that reaches this port (another container of the
    network) is known by its own address, whatever the header says. The proxy
    appends the address it saw, so the last entry is the one a browser cannot forge.
    """
    peer = request.client.host if request.client else "unknown"
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for and trusted_proxies.is_trusted(peer):
        return x_forwarded_for.split(",")[-1].strip()
    return peer


def check_same_origin(request: Request) -> None:
    """Refuse a state-changing request that was not sent by this interface's own page."""
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if not origin or not host or urlsplit(origin).netloc != host:
        caller = client_ip(request, request.app.state.ctx.trusted_proxies)
        logger.warning(f"Refused cross-origin {request.method} {request.url.path} from {caller}")
        raise HTTPException(status_code=403, detail="Cross-origin request refused")


async def require_session(request: Request) -> None:
    """Dependency of every authenticated route."""
    ctx = request.app.state.ctx
    token = request.cookies.get(session_cookie_name(ctx.settings.cookie_secure))
    if not token or not ctx.sessions.touch(token, ctx.credentials.version()):
        raise HTTPException(status_code=401, detail="Not authenticated")
    if request.method not in SAFE_METHODS:
        check_same_origin(request)
