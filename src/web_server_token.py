"""
The web server's tokens: ``webhook.token``, which TradingView's alerts carry
(``?token=``), and ``internal.token``, the Bearer of the services that call
it (the trader's polling, the scheduler's close, the TA engine, the admin).

Both are credentials kept in PostgreSQL (src/vault/), read at each service's
start. Until a token has its row, the token file of before is read: the one
token, encrypted with ``webserver.app_secret``, which both were until
v0.43.0. A token is never made up here: a service that finds none refuses,
or is refused, and says so.
"""

import base64
import json
import logging
import os
import stat
from datetime import datetime

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.logging_helper.redaction import register_secret
from src.vault import loader as vault
from src.vault.tokens import accepted

logger = logging.getLogger(__name__)

WEBHOOK = "webhook.token"
INTERNAL = "internal.token"


class NoWebServerToken(Exception):
    """No token in PostgreSQL nor in the file of before."""


class WebServerToken:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.token_file_path = self.config_manager.get_config_value("webserver.persistant.token_path")
        self._file_token: str | None = None
        self._file_read = False

    # ── the file of before ──

    def _cipher(self) -> Fernet | None:
        """The key the file was encrypted with: app_secret and the salt beside the file. None without the salt."""
        key_file_path = os.path.join(os.path.dirname(self.token_file_path), ".webserver_key_salt")
        try:
            with open(key_file_path, "rb") as key_file:
                salt = key_file.read()
        except OSError:
            return None
        app_secret = self.config_manager.get_config_value("webserver.app_secret", default="DEFAULT_SECRET_CHANGE_ME")
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000)
        return Fernet(base64.urlsafe_b64encode(kdf.derive(app_secret.encode())))

    def stored_token(self) -> str | None:
        """The token of the file of before, or None: nothing is written when there is none."""
        if self._file_read:
            return self._file_token
        self._file_read = True
        try:
            with open(self.token_file_path, "rb") as token_file:
                encrypted = token_file.read()
        except OSError:
            return None
        cipher = self._cipher()
        if cipher is None:
            logger.error("The web server's token file cannot be read without its salt")
            return None
        try:
            decrypted = cipher.decrypt(encrypted).decode()
        except Exception as e:
            logger.error("The web server's token file cannot be decrypted (%s)", type(e).__name__)
            return None
        try:
            token = json.loads(decrypted)
        except ValueError:
            token = decrypted
        if not isinstance(token, str) or not token:
            return None
        register_secret("webserver.token", token)
        self._file_token = token
        return token

    def save_token_data(self, token: str) -> None:
        """The file of before, written: for a server not moved to PostgreSQL yet (watawebtoken --new there)."""
        cipher = self._cipher()
        if cipher is None:
            directory = os.path.dirname(self.token_file_path)
            os.makedirs(directory, exist_ok=True)
            salt_path = os.path.join(directory, ".webserver_key_salt")
            with open(salt_path, "wb") as key_file:
                key_file.write(os.urandom(16))
            os.chmod(salt_path, stat.S_IRUSR | stat.S_IWUSR)
            cipher = self._cipher()
        with open(self.token_file_path, "wb") as token_file:
            token_file.write(cipher.encrypt(token.encode()))
        os.chmod(self.token_file_path, stat.S_IRUSR | stat.S_IWUSR)
        self._file_token, self._file_read = token, True

    # ── the tokens ──

    def value(self, name: str) -> dict | None:
        """``name`` as this service read it at its start, or the file's token while it has no row."""
        loaded = vault.credential(name)
        if loaded:
            return loaded
        if vault.source(name) == vault.DATABASE:
            return None
        token = self.stored_token()
        return {"token": token} if token else None

    def accepted(self, name: str, now: datetime | None = None) -> tuple[str, ...]:
        """The tokens the web server accepts now for ``name``: the current one, the one before for a while."""
        return accepted(name, self.value(name), now)

    def get_token(self) -> str:
        """The internal token, as the services that call the web server send it."""
        value = self.value(INTERNAL)
        if not value:
            raise NoWebServerToken(
                "No internal token: set it from the admin's Credentials (or, on a server not moved to PostgreSQL, "
                "run watawebtoken --new)"
            )
        return value["token"]

    def webhook_token(self) -> str:
        value = self.value(WEBHOOK)
        if not value:
            raise NoWebServerToken("No TradingView webhook token: set it from the admin's Credentials")
        return value["token"]
