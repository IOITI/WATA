import requests
import secrets
import urllib.parse
import json
import time
import datetime
import os
import logging
import base64
import fcntl
from contextlib import contextmanager
import pika
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import stat

from src.configuration import ConfigurationManager
from src.logging_helper.redaction import register_secret
from src.mq_telegram.tools import send_message_to_mq_for_telegram
from src.saxo_authen import endpoints
from src.saxo_authen.token_store import (
    DatabaseThread,
    DbTokenStore,
    FileTokenStore,
    Tokens,
    TokenStoreUnavailable,
)

logger = logging.getLogger(__name__)

# Without a timeout a stalled token endpoint would block the caller forever.
TOKEN_REQUEST_TIMEOUT_SECONDS = 10
# A renewal Saxo did not answer, or answered with one of these, is tried again after each wait:
# the refusal of the token itself (400, 401) is not.
REFRESH_RETRY_WAITS_SECONDS = (1.0, 2.0)
TRANSIENT_REFRESH_STATUSES = {429, 500, 502, 503, 504}
# A lapsed login: how long a call waits for a code to be handed over (the lock released meanwhile),
# how often it looks, and how often Telegram is reminded while nobody logs in.
AUTHORIZATION_WAIT_SECONDS = 300
CODE_POLL_SECONDS = 5
REMINDER_EVERY_SECONDS = 1800


class AuthorizationRequired(Exception):
    """Saxo's login lapsed and no code was handed over in time: nothing can be traded until one is."""


def describe_token_refusal(response, secret: str | None = None) -> str:
    """What Saxo's token endpoint said when it refused, its tokens never repeated."""
    try:
        body = response.json()
        said = " ".join(str(body.get(key)) for key in ("error", "error_description") if body.get(key)) or str(body)
    except Exception:
        said = response.text or ""
    said = said.strip()[:200]
    if secret:
        said = said.replace(secret, "…")
    return f"Saxo answered {response.status_code}" + (f": {said}" if said else "")


def _register_tokens(token_data: dict) -> None:
    """The tokens in hand, never to be written in a log line (a renewal replaces the ones before)."""
    register_secret("saxo.access_token", token_data.get("access_token"))
    register_secret("saxo.refresh_token", token_data.get("refresh_token"))


class SaxoAuth:
    """
    Handles authentication and token management for Saxo.
    """

    def __init__(self, config_manager, rabbit_connection=None, token_store=None, environ=None):
        self.state = secrets.token_hex(16)
        self.config_manager = config_manager
        self.rabbit_connection = rabbit_connection
        self.environ = os.environ if environ is None else environ
        self.role = self.environ.get("WATA_APP_ROLE", "")
        self.environment = config_manager.get_config_value("saxo_auth.env", "live")
        self.token_file_path = self.config_manager.get_config_value(
            "authentication.persistant.token_path"
        )
        self.auth_code_path = os.path.join(os.path.dirname(self.token_file_path), "saxo_auth_code.txt")
        # Why the last renewal of the access token by this process did not go through; None once one did.
        self.last_refresh_error: str | None = None
        self.token_lock_path = os.path.join(os.path.dirname(self.token_file_path), ".token_refresh.lock")

        # Ensure token directory exists
        os.makedirs(os.path.dirname(self.token_file_path), exist_ok=True)

        # The token file of before, until the migration moved it into PostgreSQL (and renamed it); then the
        # environment's row, when this service has its key. The lab and the tests keep the file.
        self.store = token_store or self._chosen_store()
        self.app_data = self._app_data()
        # The tokens last read from the row: asked again once they are near their end.
        self._cached: Tokens | None = None
        if self.store.kind == "file":
            # Initialize the encryption key
            self._initialize_encryption()
            self.store = token_store or FileTokenStore(self.token_file_path, self.cipher)

    def _chosen_store(self):
        from src.vault import keys

        if os.path.exists(self.token_file_path) or not keys.has_private_key():
            return FileTokenStore(self.token_file_path, None)
        dsn = self.config_manager.get_config_value("postgresql.dsn")

        async def connect():
            import asyncpg

            return await asyncpg.connect(dsn, timeout=10)

        return DbTokenStore(DatabaseThread(connect), self.environment, self.role, os.path.dirname(self.token_file_path))

    def _app_data(self) -> dict:
        """
        The app this service logs in with: the environment's, sealed in PostgreSQL, else config.json's
        object (its old place). In PostgreSQL's case, Saxo's addresses are the environment's constants
        and the redirect the server's (endpoints.py): never what a row says.
        """
        from src.vault import loader as vault

        config_app = self.config_manager.get_config_value("authentication.saxo.app_config_object") or {}
        stored = vault.credential(f"saxo.{self.environment}.app")
        if self.store.kind == "file" and not stored:
            return config_app
        found = dict(endpoints.ENDPOINTS.get(self.environment) or {})
        redirect = endpoints.redirect_url(self.config_manager, self.environ)
        return {
            "AppKey": (stored or {}).get("app_key") or config_app.get("AppKey"),
            "AppSecret": (stored or {}).get("app_secret") or config_app.get("AppSecret"),
            "AuthorizationEndpoint": found.get("authorize") or config_app.get("AuthorizationEndpoint"),
            "TokenEndpoint": found.get("token") or config_app.get("TokenEndpoint"),
            "RedirectUrls": [redirect] if redirect else list(config_app.get("RedirectUrls") or []),
        }

    def _initialize_encryption(self):
        """
        Initialize encryption for secure token storage.
        The key is derived from APP_SECRET plus a salt stored in a separate file.
        """
        key_file_path = os.path.join(os.path.dirname(self.token_file_path), ".key_salt")
        # The file was encrypted with config.json's app: its secret, whatever app is sealed in PostgreSQL.
        app_secret = (self.config_manager.get_config_value("authentication.saxo.app_config_object") or {})["AppSecret"]
        
        # Create or load salt
        if os.path.exists(key_file_path):
            with open(key_file_path, "rb") as key_file:
                salt = key_file.read()
        else:
            # Generate new salt if doesn't exist
            salt = os.urandom(16)
            with open(key_file_path, "wb") as key_file:
                key_file.write(salt)
            # Set restrictive permissions on the key file
            os.chmod(key_file_path, stat.S_IRUSR | stat.S_IWUSR)
        
        # Derive key from app secret and salt
        password = app_secret.encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password))
        self.cipher = Fernet(key)
        
    def _encrypt_data(self, data):
        """Encrypt data before storing"""
        json_data = json.dumps(data).encode()
        return self.cipher.encrypt(json_data)
    
    def _decrypt_data(self, encrypted_data):
        """Decrypt stored data"""
        try:
            decrypted_data = self.cipher.decrypt(encrypted_data)
            return json.loads(decrypted_data)
        except Exception as e:
            logger.error(f"Error decrypting data: {e}")
            return None

    def _write_encrypted_token_file(self, encrypted_data):
        """Persist encrypted token bytes to the configured token file."""
        # Written aside then renamed, so the other services reading this file
        # never see it empty or half-written.
        tmp_path = f"{self.token_file_path}.tmp"
        with open(tmp_path, "wb") as token_file:
            token_file.write(encrypted_data)
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp_path, self.token_file_path)

    def _read_encrypted_token_file(self):
        """Read encrypted token bytes from the configured token file."""
        if not os.path.exists(self.token_file_path):
            return None

        with open(self.token_file_path, "rb") as token_file:
            return token_file.read()

    def _load_token_data(self):
        """Read and decrypt the stored token data ({} when there is none)."""
        encrypted_data = self._read_encrypted_token_file()
        if not encrypted_data:
            return {}
        token_data = self._decrypt_data(encrypted_data) or {}
        logger.debug("Token data retrieved from encrypted file")
        _register_tokens(token_data)
        return token_data

    @contextmanager
    def _token_refresh_lock(self):
        """
        Exclusive lock held while renewing the token.

        Several services share this token file. A Saxo refresh token can only be
        used once, so they must not renew at the same time.
        """
        with open(self.token_lock_path, "a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _send_telegram_notification(self, message):
        """
        Send a Telegram notification via RabbitMQ.

        If a pre-existing blocking RabbitMQ connection was provided, reuse it.
        Otherwise open a short-lived synchronous connection from config so
        notifications still work during async service startup.
        """
        if self.rabbit_connection is not None:
            try:
                send_message_to_mq_for_telegram(self.rabbit_connection, message)
                return True
            except Exception as e:
                logger.error(f"Failed to send Telegram notification via existing RabbitMQ connection: {e}")

        connection = None
        try:
            rabbitmq_config = self.config_manager.get_rabbitmq_config()
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=rabbitmq_config["hostname"],
                    credentials=pika.PlainCredentials(
                        rabbitmq_config["authentication"]["username"],
                        rabbitmq_config["authentication"]["password"],
                    ),
                )
            )
            channel = connection.channel()
            channel.queue_declare(queue="telegram_channel")
            payload = json.dumps({"message": message})
            channel.basic_publish(
                exchange="",
                routing_key="telegram_channel",
                body=payload,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False
        finally:
            if connection is not None and connection.is_open:
                connection.close()

    def get_authorization_url(self):
        """
        Generate and return the authorization URL for the user to visit in their browser.
        """
        params = {
            "response_type": "code",
            "client_id": self.app_data["AppKey"],
            "redirect_uri": self.app_data["RedirectUrls"][0],
            "state": self.state,
        }
        auth_url = f"{self.app_data['AuthorizationEndpoint']}?{urllib.parse.urlencode(params)}"
        return auth_url

    def read_auth_code_from_file(self):
        """
        Read the authorization code from the temporary file.
        """
        try:
            if os.path.exists(self.auth_code_path):
                with open(self.auth_code_path, "r") as file:
                    code = file.read().strip()
                    # Remove the file after reading
                    os.remove(self.auth_code_path)
                    if code:
                        logger.info("Successfully read authorization code from file")
                        return code
            return None
        except Exception as e:
            logger.error(f"Error reading authorization code from file: {e}")
            return None

    def get_authorization_code(self):
        """
        Get the authorization code from the temporary file or prompt the user to obtain it.
        """
        # Check if we already have an auth code
        code = self.read_auth_code_from_file()
        if code:
            return code

        # Generate authorization URL for the user to visit
        auth_url = self.get_authorization_url()
        auth_instructions = "\nPlease follow these steps to authorize the application:"
        auth_instructions += "\n1. Open the following URL in your browser:"
        auth_instructions += f"\n\n{auth_url}\n"
        auth_instructions += "\n2. Log in with your Saxo credentials and authorize the application"
        auth_instructions += "\n3. After authorization, you will be redirected to a page with a URL containing a 'code' parameter"
        auth_instructions += "\n4. Copy the code parameter and run the following command on the server:"
        auth_instructions += f"\n   watasaxoauth\n"
        auth_instructions += "\n5. Paste the code when prompted (the code won't be visible when typing)"
        auth_instructions += "\n6. The application will then continue with the authentication process"
        auth_instructions += "\n\nNote: The authorization code will be saved to a temporary file for further processing, then it will be deleted."
        auth_instructions += "\n\n⏳ Waiting for authorization code..."
        
        print(auth_instructions)

        if self._send_telegram_notification(
            f"--- 🔐 SAXO AUTHORIZATION REQUIRED ---\n{auth_instructions}"
        ):
            logger.info("Authorization instructions sent to Telegram")
        else:
            logger.warning("Unable to send authorization instructions to Telegram")
        
        # Wait for the auth code file to appear (with timeout)
        max_wait_time = 300  # 5 minutes
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            code = self.read_auth_code_from_file()
            if code:
                self._send_telegram_notification(
                    "✅ Authorization code received successfully!"
                )
                return code
            time.sleep(5)
        
        error_message = "Timeout waiting for authorization code"
        logger.error(error_message)

        self._send_telegram_notification(f"❌ ERROR: {error_message}")
        
        raise TimeoutError(error_message)

    def exchange_code_for_token(self, code):
        """
        Exchange the authorization code for an access token.
        """
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.app_data["RedirectUrls"][0],
            "client_id": self.app_data["AppKey"],
            "client_secret": self.app_data["AppSecret"],
        }
        try:
            response = requests.post(self.app_data["TokenEndpoint"], data=data, timeout=TOKEN_REQUEST_TIMEOUT_SECONDS)
            if response.status_code == 201:
                logger.info("Successfully exchanged code for token")
                return response.json()
            else:
                logger.error(f"Failed to exchange code for token: {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Error exchanging code for token: {e}")
            return None
        except Exception as e:
            logger.error(f"Unknown error exchanging code for token: {e}")
            return None

    def refresh_token(self, refresh_token_param):
        """
        Refresh the access token using a refresh token.
        """
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token_param,
            "client_id": self.app_data["AppKey"],
            "client_secret": self.app_data["AppSecret"],
        }
        for wait in (None, *REFRESH_RETRY_WAITS_SECONDS):
            if wait is not None:
                time.sleep(wait)
            try:
                response = requests.post(self.app_data["TokenEndpoint"], data=data, timeout=TOKEN_REQUEST_TIMEOUT_SECONDS)
            except requests.exceptions.RequestException as e:
                self.last_refresh_error = f"no answer from Saxo's token endpoint ({type(e).__name__})"
                logger.error(f"Error refreshing the access token: {self.last_refresh_error}")
                continue
            except Exception as e:
                self.last_refresh_error = f"{type(e).__name__}"
                logger.error(f"Unknown error refreshing the access token: {type(e).__name__}")
                return None
            if response.status_code == 201:
                logger.info("Successfully refreshed access token")
                self.last_refresh_error = None
                return response.json()
            # Kept and said: a refusal dropped in silence is a lapse no one can explain afterwards.
            self.last_refresh_error = describe_token_refusal(response, refresh_token_param)
            logger.error(f"Saxo refused to renew the access token: {self.last_refresh_error}")
            if response.status_code not in TRANSIENT_REFRESH_STATUSES:
                return None
        return None

    def ask_new_token(self):
        """
        Obtain a new authorization code and exchange it for a new set of tokens.
        """
        try:
            # Generate a new authorization code
            code = self.get_authorization_code()
            if not code:
                print("Failed to obtain new authorization code")
                return None
        except Exception as e:
            logger.error(f"Error obtaining new authorization code: {e}")
            return None

        try:
            # Exchange the authorization code for a new set of tokens
            token_response = self.exchange_code_for_token(code)
            if not token_response:
                print("Failed to exchange code for token")
                return None

            return token_response
        except Exception as e:
            logger.error(f"Error exchanging code for token: {e}")
            return None

    def save_token_data(self, token_data):
        """
        Save token data to the configured file in encrypted format.
        """
        token_data["date_saved"] = datetime.datetime.now().isoformat()
        _register_tokens(token_data)

        # Encrypt the token data
        encrypted_data = self._encrypt_data(token_data)

        self._write_encrypted_token_file(encrypted_data)

        logger.info("Token data securely saved to encrypted file")
        

    def is_token_expired(self, token_data):
        """
        Check if the access token is expired.
        """
        if (
            not token_data
            or "date_saved" not in token_data
            or "expires_in" not in token_data
        ):
            logger.debug("Token data is missing or incomplete, considered expired")
            return True
        date_saved = datetime.datetime.fromisoformat(token_data["date_saved"])
        expires_in_second = token_data["expires_in"] - 120
        expiration_time = date_saved + datetime.timedelta(
            seconds=expires_in_second
        )
        logger.debug(f"Token wanted expiration time: {expiration_time}, current time: {datetime.datetime.now()}")
        return datetime.datetime.now() > expiration_time

    def is_refresh_token_expired(self, token_data):
        """
        Check if the refresh token is expired.
        """
        if (
            not token_data
            or "date_saved" not in token_data
            or "refresh_token_expires_in" not in token_data
        ):
            logger.debug("Refresh Token data is missing or incomplete, considered expired")
            return True
        date_saved = datetime.datetime.fromisoformat(token_data["date_saved"])
        refresh_token_expires_in_second = token_data["refresh_token_expires_in"] - 60
        refresh_token_expiration_time = date_saved + datetime.timedelta(
            seconds=refresh_token_expires_in_second
        )
        logger.debug(f"Refresh token wanted expiration time: {refresh_token_expiration_time}, current time: {datetime.datetime.now()}")
        return datetime.datetime.now() > refresh_token_expiration_time

    def get_token_status(self):
        """
        Describe the stored token without exposing it (the trader's watch; the admin before v0.44.0).
        """
        if self.store.kind != "file":
            return self.store.status()
        token_data = self._load_token_data()
        date_saved = None
        if token_data.get("date_saved"):
            date_saved = datetime.datetime.fromisoformat(token_data["date_saved"])

        def to_utc(naive_local):
            # date_saved is naive local time, which is how astimezone() reads it.
            return naive_local.astimezone(datetime.timezone.utc).isoformat()

        def expiry(lifetime_key):
            if date_saved is None or lifetime_key not in token_data:
                return None
            return to_utc(date_saved + datetime.timedelta(seconds=token_data[lifetime_key]))

        return {
            "has_token": bool(token_data.get("access_token")),
            "saved_at": to_utc(date_saved) if date_saved else None,
            "access_token_expires_at": expiry("expires_in"),
            "refresh_token_expires_at": expiry("refresh_token_expires_in"),
            "access_token_expired": self.is_token_expired(token_data),
            "refresh_token_expired": self.is_refresh_token_expired(token_data),
        }

    def get_token(self):
        """
        Get a valid access token, either by refreshing an existing token or obtaining a new one.
        """
        if self.store.kind != "file":
            return self._get_token_from_database()
        try:
            token_data = self._load_token_data()

            if self.is_token_expired(token_data):
                with self._token_refresh_lock():
                    # Another service may have renewed it while this one waited for the lock.
                    token_data = self._load_token_data()
                    if self.is_token_expired(token_data):
                        if self.is_refresh_token_expired(token_data):
                            new_token_data = self.ask_new_token()
                            if new_token_data:
                                self.save_token_data(new_token_data)
                                token_data = new_token_data
                            else:
                                logger.error("Failed to obtain new tokens")
                                raise Exception("Failed to obtain new tokens")
                        else:
                            new_token_data = self.refresh_token(token_data["refresh_token"])
                            if new_token_data:
                                self.save_token_data(new_token_data)
                                token_data = new_token_data
                            else:
                                logger.error("Failed to renew token")
                                raise Exception("Failed to renew token")
            if token_data["access_token"]:
                logger.debug("Give token for Saxo API")
            return token_data["access_token"]
        except FileNotFoundError as e:
            logger.error(f"Token file not found: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding token data: {e}")
            raise
        except Exception as e:
            logger.error(f"Error getting token: {e}")
            raise

    # ── the tokens in PostgreSQL ──

    def _fresh(self, tokens: Tokens | None) -> bool:
        return tokens is not None and bool(tokens.data.get("access_token")) and not self.is_token_expired(tokens.data)

    def _kept(self, data: dict, expected_generation: int) -> str:
        """Renewed or obtained tokens stamped, written as the next generation, kept in memory."""
        data["date_saved"] = datetime.datetime.now().isoformat()
        _register_tokens(data)
        generation = self.store.save(data, expected_generation, by=self.role or "saxo")
        self._cached = Tokens(data, generation)
        return data["access_token"]

    def _exchanged(self, code: dict, tokens: Tokens | None) -> str | None:
        """A code handed over, turned into tokens with the server's redirect, kept; None when Saxo refuses it."""
        if not self.app_data.get("RedirectUrls"):
            self.store.note_refusal("No redirect address to exchange the code with: SAXO_REDIRECT_URL in .env")
            logger.error("A code was handed over, but no redirect address is set (SAXO_REDIRECT_URL in .env)")
            return None
        token_response = self.exchange_code_for_token(code["code"])
        if not token_response:
            self.store.note_refusal("Saxo refused the code handed over: ask for another login")
            return None
        logger.info("Saxo's login renewed with the code handed over")
        self.last_refresh_error = None
        token = self._kept(token_response, tokens.generation if tokens else 0)
        self._send_telegram_notification(f"✅ Saxo's login renewed ({self.environment} account): trading can go on.")
        return token

    def _say_lapse(self, first: bool) -> None:
        if first:
            message = (
                f"🔐 Saxo's login has lapsed ({self.environment} account): nothing can be bought or sold until "
                "someone logs in again. Authorise from the admin (System, Saxo), or run watasaxoauth on the server."
            )
        elif self.store.reminder_due(REMINDER_EVERY_SECONDS):
            message = f"🔐 Still no Saxo login ({self.environment} account): nothing can be traded meanwhile."
        else:
            return
        if not self._send_telegram_notification(message):
            logger.warning("Could not say on Telegram that Saxo's login lapsed")

    def _get_token_from_database(self) -> str:
        """
        The access token of the environment's row: kept in memory until near its
        end, renewed under the lock (one renewal for all the services: a refresh
        token is used once), or, once the login lapsed, obtained from a code
        handed over (the admin's page, watasaxoauth). Waiting for that code, the
        lock is released: said once on Telegram, then every half hour.
        """
        if self._fresh(self._cached):
            return self._cached.data["access_token"]
        tokens = self.store.load()
        if self._fresh(tokens):
            self._cached = tokens
            return tokens.data["access_token"]
        with self._token_refresh_lock():
            try:
                self.store.flush_spool()
            except TokenStoreUnavailable:
                pass
            # Another service may have renewed it while this one waited for the lock.
            tokens = self.store.load()
            if self._fresh(tokens):
                self._cached = tokens
                return tokens.data["access_token"]
            if tokens is not None and tokens.data.get("refresh_token") and not self.is_refresh_token_expired(tokens.data):
                renewed = self.refresh_token(tokens.data["refresh_token"])
                if renewed:
                    return self._kept(renewed, tokens.generation)
                self.store.note_refusal(self.last_refresh_error or "Saxo refused to renew the login")
                logger.error("Failed to renew token")
                raise Exception("Failed to renew token")
            code = self.store.claim_code()
            if code:
                token = self._exchanged(code, tokens)
                if token:
                    return token
            first = self.store.needs_authorization(self.last_refresh_error)
        self._say_lapse(first)
        deadline = time.time() + AUTHORIZATION_WAIT_SECONDS
        while time.time() < deadline:
            time.sleep(CODE_POLL_SECONDS)
            with self._token_refresh_lock():
                tokens = self.store.load()
                if self._fresh(tokens):
                    self._cached = tokens
                    return tokens.data["access_token"]
                code = self.store.claim_code()
                if code:
                    token = self._exchanged(code, tokens)
                    if token:
                        return token
        raise AuthorizationRequired(
            f"No Saxo login for the {self.environment} account: authorise from the admin (System, Saxo) or watasaxoauth"
        )


if __name__ == "__main__":
    try:
        print("Starting")
        config_path = os.getenv("WATA_CONFIG_PATH")
        print("Configuring")
        # Create an instance of ConfigurationManager
        config_manager = ConfigurationManager(config_path)

        saxo_auth = SaxoAuth(config_manager)
        saxo_auth.get_token()
        # Never the token itself: whoever reads a terminal or a log could trade with it.
        print(f"Token status: {saxo_auth.get_token_status()}")
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
