"""
The key pairs credentials are sealed with: one per service (its role).

On the server, ``/app/wata/keys/<role>/private.key`` is that service's alone
(0600, mounted read-only into its container only, at ``/app/keys/own``) and
``/app/wata/keys/public/<role>.pub`` is everyone's (mounted read-only into
every WATA container, at ``/app/keys/public``). They are outside ``var/lib``,
which five services mount whole, and outside ``etc``, which the admin writes:
no container reaches another's private key. They are in no dump and no export,
so a copy of the database is only ciphertext. A lost key means entering the
credentials again: keep a copy of ``/app/wata/keys``.

``python -m src.vault keygen`` makes the missing ones and never replaces one.
"""

import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

# Every service that reads a credential, by its WATA_APP_ROLE. The control service reads none.
ROLES = (
    "web_server", "trader", "position_monitor", "scheduler", "telegram", "watchlist_manager", "admin",
    "price_recorder", "capital_feed", "ta_engine",
)
# Inside a container.
KEYS_DIR = os.getenv("WATA_KEYS_DIR", "/app/keys")
PRIVATE_NAME = "private.key"
PUBLIC_FOLDER = "public"


class KeyProblem(Exception):
    """A key that is missing or cannot be read: said with its path, never its content."""


def _private_path(keys_dir: str) -> str:
    return os.path.join(keys_dir, "own", PRIVATE_NAME)


def private_key(keys_dir: str = KEYS_DIR) -> X25519PrivateKey:
    """This container's own private key."""
    path = _private_path(keys_dir)
    try:
        with open(path, "rb") as key_file:
            key = serialization.load_pem_private_key(key_file.read(), password=None)
    except FileNotFoundError:
        raise KeyProblem(f"No private key at {path}: the keys are made by the install script (python -m src.vault keygen)") from None
    except (OSError, ValueError) as e:
        raise KeyProblem(f"The private key at {path} cannot be read ({type(e).__name__})") from None
    if not isinstance(key, X25519PrivateKey):
        raise KeyProblem(f"The key at {path} is not an X25519 key")
    return key


def has_private_key(keys_dir: str = KEYS_DIR) -> bool:
    return os.path.exists(_private_path(keys_dir))


def public_keys(keys_dir: str = KEYS_DIR) -> dict[str, X25519PublicKey]:
    """Every service's public key found, by role."""
    folder = os.path.join(keys_dir, PUBLIC_FOLDER)
    found = {}
    for role in ROLES:
        path = os.path.join(folder, f"{role}.pub")
        try:
            with open(path, "rb") as key_file:
                key = serialization.load_pem_public_key(key_file.read())
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            raise KeyProblem(f"The public key at {path} cannot be read ({type(e).__name__})") from None
        if not isinstance(key, X25519PublicKey):
            raise KeyProblem(f"The key at {path} is not an X25519 key")
        found[role] = key
    return found


def generate(root: str, roles=ROLES) -> list[str]:
    """
    On the server: the key pair of each role that has none yet, under
    ``root`` (``/app/wata/keys``). Never replaces a key: the credentials sealed
    for it would no longer open. The roles given keys, in order.
    """
    made = []
    os.makedirs(os.path.join(root, PUBLIC_FOLDER), mode=0o700, exist_ok=True)
    for role in roles:
        folder = os.path.join(root, role)
        private_path = os.path.join(folder, PRIVATE_NAME)
        public_path = os.path.join(root, PUBLIC_FOLDER, f"{role}.pub")
        if os.path.exists(private_path):
            if not os.path.exists(public_path):
                # Its public half, made again from it: the same key.
                key = private_key_at(private_path)
                _write(public_path, _public_pem(key.public_key()), 0o644)
            continue
        os.makedirs(folder, mode=0o700, exist_ok=True)
        key = X25519PrivateKey.generate()
        _write(private_path, key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ), 0o600)
        _write(public_path, _public_pem(key.public_key()), 0o644)
        made.append(role)
    return made


def private_key_at(path: str) -> X25519PrivateKey:
    with open(path, "rb") as key_file:
        key = serialization.load_pem_private_key(key_file.read(), password=None)
    if not isinstance(key, X25519PrivateKey):
        raise KeyProblem(f"The key at {path} is not an X25519 key")
    return key


def _public_pem(key: X25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def _write(path: str, content: bytes, mode: int) -> None:
    """Written whole and once: an existing file is never opened for writing."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as key_file:
        key_file.write(content)
    os.chmod(path, mode)
