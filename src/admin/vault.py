"""
The credentials as the admin sees them (``ctx.vault``): what each is, whether
it is set, when and by whom, its last four characters, and what the services
that used it last saw. What the operator enters is checked and sealed with the
public keys of the services that read it (src/vault/), and written through the
admin's one writable connection. Never read back: the admin opens only the
credentials it reads itself (the internal token, for the signals sent from
its pages; the Telegram bot, for its own direct fallback; the webhook token,
shown on an explicit and audited request).
"""

import logging
import os

from src.admin.errors import InvalidRequest, NotPossible
from src.vault import credentials as catalog
from src.vault import keys, sealed, store
from src.vault.tokens import GRACE_SECONDS, rotated
from src.vault.writer import put

logger = logging.getLogger(__name__)

ROLE = "admin"
# What the admin opens when it uses it, never at its start: a change of them asks no restart of the admin.
OPENED_ON_DEMAND = ("webhook.token", "internal.token", "anthropic.api")
# The credentials whose old place the admin can read itself (config.json, the token file), or that never had
# one (the Claude API key): missing until set. The others' are in the environment of another container (.env),
# which the admin never has.
OLD_PLACE_SEEN = ("telegram.bot", "webhook.token", "internal.token", "saxo.live.app", "saxo.simulation.app",
                  "anthropic.api")


class AdminVault:
    def __init__(self, *, reader, writer, config_manager, keys_dir: str = keys.KEYS_DIR, feed_state=None):
        # The read-only pool reads the rows; the one writable connection writes them.
        self._reader = reader
        self._writer = writer
        self._config_manager = config_manager
        self._keys_dir = keys_dir
        # () -> the Capital.com feed's state row: its login is said there, not in credential_check.
        self._feed_state = feed_state

    @property
    def keys_dir(self) -> str:
        return self._keys_dir

    @property
    def reader(self):
        """The read-only pool, through which the admin reads its own credentials at its start."""
        return self._reader

    @property
    def writer(self):
        """The admin's one writable connection, for a check it writes itself (the Telegram bot tried)."""
        return self._writer

    def keys_said(self) -> str | None:
        """Why nothing can be sealed or opened here, or None: the keys made by the install script."""
        try:
            publics = keys.public_keys(self._keys_dir)
        except keys.KeyProblem as e:
            return str(e)
        if not publics:
            return ("No key here: credentials are sealed with the services' keys, which the install script makes "
                    "on the server (python -m src.vault keygen)")
        missing = [role for role in keys.ROLES if role not in publics]
        if missing:
            return f"No key for {', '.join(missing)}: the install script makes them (python -m src.vault keygen)"
        return None

    async def listed(self) -> dict:
        rows = {row["name"]: row for row in await store.listed(self._reader)}
        checks: dict[str, list] = {}
        for check in await store.checks(self._reader):
            checks.setdefault(check["name"], []).append(check)
        feed = None
        if self._feed_state is not None:
            try:
                feed = await self._feed_state()
            except Exception:
                feed = None
        items = []
        for credential in catalog.CREDENTIALS:
            row = rows.get(credential.name)
            if row is not None:
                where = "database"
            elif credential.name in OLD_PLACE_SEEN:
                where = "old place" if catalog.old_value(credential.name, self._config_manager, {}) else "missing"
            else:
                where = "unknown"
            item = {
                "name": credential.name,
                "label": credential.label,
                "readers": list(credential.readers),
                "parts": [
                    {"key": part.key, "label": part.label, "secret": part.secret, "expected": part.expected}
                    for part in credential.parts
                ],
                "generated": credential.generated,
                "old_place": credential.old_place,
                "where": where,
                "public": row["public"] if row else {},
                "fingerprint": row["fingerprint"] if row else None,
                "set_at": row["set_at"] if row else None,
                "set_by": row["set_by"] if row else None,
                "checks": checks.get(credential.name, []),
                "grace_seconds": GRACE_SECONDS.get(credential.name),
            }
            if credential.name == "capital.login" and feed:
                item["checks"] = [{
                    "role": "capital_feed", "checked_at": feed.get("updated_at"),
                    "ok": feed.get("state") not in ("no_credentials", "login_refused", "error"),
                    "detail": f"{feed.get('state')}: {feed.get('detail') or ''}".rstrip(": "),
                }]
            items.append(item)
        return {"keys": self.keys_said(), "items": items}

    def _known(self, name: str) -> catalog.Credential:
        credential = catalog.BY_NAME.get(name)
        if credential is None:
            raise InvalidRequest(f"{name} is not one of WATA's credentials")
        return credential

    async def _put(self, name: str, value: dict, by: str | None) -> list[str]:
        said = self.keys_said()
        if said:
            raise NotPossible(said)
        try:
            await put(self._writer, name, value, by=by, keys_dir=self._keys_dir)
        except keys.KeyProblem as e:
            raise NotPossible(str(e)) from None
        return self.restarted_by(name)

    def restarted_by(self, name: str) -> list[str]:
        """The services that apply a change of ``name`` at their next start."""
        return [role for role in self._known(name).readers if not (role == ROLE and name in OPENED_ON_DEMAND)]

    async def set(self, name: str, parts: dict, by: str | None) -> dict:
        """Check ``parts`` and seal them; the services that read it, which apply it at their next start."""
        credential = self._known(name)
        if credential.generated:
            raise InvalidRequest(f"{credential.label} is made by WATA: rotate it")
        try:
            value = catalog.checked(name, parts)
        except ValueError as e:
            raise InvalidRequest(str(e)) from None
        readers = await self._put(name, value, by)
        return {"name": name, "fingerprint": catalog.fingerprint(name, value), "services": readers}

    async def rotate(self, name: str, by: str | None) -> dict:
        """A new token, the one before still accepted for a while; the services that read it."""
        credential = self._known(name)
        if not credential.generated:
            raise InvalidRequest(f"{credential.label} is entered, not made: replace it")
        current = await self.open(name)
        value = rotated(current)
        readers = await self._put(name, value, by)
        return {
            "name": name, "fingerprint": catalog.fingerprint(name, value), "services": readers,
            "previous_accepted_seconds": GRACE_SECONDS.get(name) if current else None,
        }

    async def remove(self, name: str) -> dict:
        credential = self._known(name)
        if not await store.remove(self._writer, name):
            raise NotPossible(f"{credential.label} has no row to remove")
        return {"name": name, "services": self.restarted_by(name)}

    async def open(self, name: str) -> dict | None:
        """
        A credential the admin reads itself, opened with its own key; its old
        place while it has no row. None when it has none.
        """
        credential = self._known(name)
        if ROLE not in credential.readers:
            raise NotPossible(f"The admin does not read {credential.label}")
        rows = await store.read(self._reader, [name])
        if name not in rows:
            return catalog.old_value(name, self._config_manager, os.environ)
        try:
            return sealed.open_sealed(name, rows[name]["sealed"], ROLE, keys.private_key(self._keys_dir))
        except (keys.KeyProblem, sealed.SealError) as e:
            raise NotPossible(f"{credential.label} cannot be opened by the admin: {e}") from None
