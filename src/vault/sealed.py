"""
A credential sealed for the services that read it, and opened by one of them.

The value is encrypted once, with a data key of its own (AES-256-GCM). The
data key is then wrapped for each reader: an X25519 key agreement between a key
made for that wrap and the reader's public key, HKDF-SHA256 over it, AES-256-GCM
again. Whoever writes a credential needs only the readers' public keys; only a
reader's private key opens it. The credential's name is bound into every
encryption (the associated data), so that a row moved under another name opens
no more, nor a wrap moved to another reader.

Pure: no file, no database. Only the ``cryptography`` package, already pinned.
"""

import base64
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FORMAT = 1
ALGORITHM = "X25519-HKDF-SHA256-AES256GCM"
NONCE_BYTES = 12


class SealError(Exception):
    """A credential that cannot be opened here: what was tried, never a value."""


class NotAReader(SealError):
    """The credential was not sealed for this service."""


class Unreadable(SealError):
    """The envelope is not one this code made, was changed, or is opened with another key."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text) -> bytes:
    if not isinstance(text, str):
        raise Unreadable("a field of the envelope is not text")
    try:
        return base64.b64decode(text, validate=True)
    except ValueError:
        raise Unreadable("a field of the envelope is not base64") from None


def _raw(public: X25519PublicKey) -> bytes:
    return public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _bound(name: str, role: str | None = None) -> bytes:
    """What every encryption of ``name`` is bound to: its name, and the reader for a wrap."""
    said = f"wata-credential/{FORMAT}/{name}"
    return (said if role is None else f"{said}/{role}").encode()


def _wrap_key(shared: bytes, wrap_public: bytes, reader_public: bytes, name: str, role: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=wrap_public + reader_public, info=_bound(name, role),
    ).derive(shared)


def seal(name: str, value, readers: dict[str, X25519PublicKey]) -> dict:
    """``value`` (anything JSON holds) sealed for ``readers``, by role: the envelope kept in the database."""
    if not readers:
        raise ValueError(f"{name} would be sealed for no service")
    plaintext = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    data_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(NONCE_BYTES)
    wraps = {}
    for role in sorted(readers):
        wrap_private = X25519PrivateKey.generate()
        wrap_public = _raw(wrap_private.public_key())
        key = _wrap_key(wrap_private.exchange(readers[role]), wrap_public, _raw(readers[role]), name, role)
        wrap_nonce = os.urandom(NONCE_BYTES)
        wraps[role] = {
            "epk": _b64(wrap_public),
            "nonce": _b64(wrap_nonce),
            "key": _b64(AESGCM(key).encrypt(wrap_nonce, data_key, _bound(name, role))),
        }
    return {
        "format": FORMAT,
        "alg": ALGORITHM,
        "nonce": _b64(nonce),
        "data": _b64(AESGCM(data_key).encrypt(nonce, plaintext, _bound(name))),
        "readers": wraps,
    }


def readers_of(envelope: dict) -> list[str]:
    """The services an envelope was sealed for."""
    return sorted((envelope.get("readers") or {}) if isinstance(envelope, dict) else {})


def open_sealed(name: str, envelope: dict, role: str, private: X25519PrivateKey):
    """The value of ``name``, opened by ``role`` with its private key; SealError when it cannot be."""
    if not isinstance(envelope, dict) or envelope.get("format") != FORMAT or envelope.get("alg") != ALGORITHM:
        raise Unreadable(f"{name} is not sealed as this version of WATA seals")
    wrap = (envelope.get("readers") or {}).get(role)
    if not isinstance(wrap, dict):
        raise NotAReader(f"{name} is not sealed for the {role} service")
    try:
        wrap_public = _unb64(wrap.get("epk"))
        key = _wrap_key(
            private.exchange(X25519PublicKey.from_public_bytes(wrap_public)), wrap_public, _raw(private.public_key()),
            name, role,
        )
        data_key = AESGCM(key).decrypt(_unb64(wrap.get("nonce")), _unb64(wrap.get("key")), _bound(name, role))
        plaintext = AESGCM(data_key).decrypt(_unb64(envelope.get("nonce")), _unb64(envelope.get("data")), _bound(name))
    except (InvalidTag, ValueError) as e:
        raise Unreadable(f"{name} cannot be opened by the {role} service's key ({type(e).__name__})") from None
    try:
        return json.loads(plaintext)
    except ValueError:
        raise Unreadable(f"{name} opened to something that is not JSON") from None
