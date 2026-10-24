"""Sealing a credential for the services that read it, and opening it (src/vault/sealed.py, keys.py)."""

import base64
import copy
import os
import stat

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from src.vault import keys
from src.vault.sealed import NotAReader, Unreadable, open_sealed, readers_of, seal

VALUE = {"token": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi", "chat_id": "4242"}


@pytest.fixture(scope="module")
def pairs():
    return {role: X25519PrivateKey.generate() for role in ("telegram", "trader", "admin", "capital_feed")}


def sealed_for(pairs, *roles, name="telegram.bot", value=VALUE):
    return seal(name, value, {role: pairs[role].public_key() for role in roles})


def test_each_reader_opens_the_same_value(pairs):
    envelope = sealed_for(pairs, "telegram", "trader", "admin")

    for role in ("telegram", "trader", "admin"):
        assert open_sealed("telegram.bot", envelope, role, pairs[role]) == VALUE
    assert readers_of(envelope) == ["admin", "telegram", "trader"]


def test_the_envelope_holds_no_value_and_differs_each_time(pairs):
    first, second = sealed_for(pairs, "telegram"), sealed_for(pairs, "telegram")

    for envelope in (first, second):
        assert VALUE["token"] not in str(envelope) and "4242" not in str(envelope)
    assert first["data"] != second["data"] and first["readers"]["telegram"]["key"] != second["readers"]["telegram"]["key"]


def test_a_service_it_was_not_sealed_for_cannot_open_it(pairs):
    envelope = sealed_for(pairs, "telegram", "trader")

    with pytest.raises(NotAReader, match="not sealed for the capital_feed service"):
        open_sealed("telegram.bot", envelope, "capital_feed", pairs["capital_feed"])
    # Its wrap taken by another reader's name, with that reader's key: refused.
    stolen = copy.deepcopy(envelope)
    stolen["readers"]["capital_feed"] = stolen["readers"]["trader"]
    with pytest.raises(Unreadable):
        open_sealed("telegram.bot", stolen, "capital_feed", pairs["capital_feed"])


def test_another_key_opens_nothing(pairs):
    envelope = sealed_for(pairs, "trader")

    with pytest.raises(Unreadable):
        open_sealed("telegram.bot", envelope, "trader", X25519PrivateKey.generate())


def _flipped(text: str) -> str:
    raw = bytearray(base64.b64decode(text))
    raw[-1] ^= 1
    return base64.b64encode(bytes(raw)).decode()


@pytest.mark.parametrize("where", ["data", "nonce", "wrap", "wrap_nonce", "epk"])
def test_a_change_to_any_byte_is_refused(pairs, where):
    envelope = sealed_for(pairs, "trader")
    wrap = envelope["readers"]["trader"]
    if where in ("data", "nonce"):
        envelope[where] = _flipped(envelope[where])
    else:
        key = {"wrap": "key", "wrap_nonce": "nonce", "epk": "epk"}[where]
        wrap[key] = _flipped(wrap[key])

    with pytest.raises(Unreadable):
        open_sealed("telegram.bot", envelope, "trader", pairs["trader"])


def test_a_value_moved_under_another_name_opens_no_more(pairs):
    envelope = sealed_for(pairs, "admin", name="webhook.token", value={"token": "x" * 43})

    with pytest.raises(Unreadable):
        open_sealed("internal.token", envelope, "admin", pairs["admin"])


def test_what_is_not_an_envelope_is_refused(pairs):
    for envelope in (None, {}, {"format": 2, "alg": "X25519-HKDF-SHA256-AES256GCM"}, {"format": 1, "alg": "other"}):
        with pytest.raises(Unreadable):
            open_sealed("telegram.bot", envelope, "trader", pairs["trader"])
    broken = sealed_for(pairs, "trader")
    broken["readers"]["trader"]["key"] = "not base64 !"
    with pytest.raises(Unreadable):
        open_sealed("telegram.bot", broken, "trader", pairs["trader"])


def test_a_value_is_sealed_for_somebody(pairs):
    with pytest.raises(ValueError):
        seal("telegram.bot", VALUE, {})


# ── the keys ──


def test_each_service_is_given_a_key_pair_once_and_never_another(tmp_path):
    made = keys.generate(str(tmp_path))

    assert made == list(keys.ROLES)
    for role in keys.ROLES:
        private = tmp_path / role / "private.key"
        assert stat.S_IMODE(os.stat(private).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(tmp_path / "public" / f"{role}.pub").st_mode) == 0o644
    before = (tmp_path / "trader" / "private.key").read_bytes()

    assert keys.generate(str(tmp_path)) == []
    assert (tmp_path / "trader" / "private.key").read_bytes() == before


def test_a_public_key_lost_is_made_again_from_its_private_half(tmp_path):
    keys.generate(str(tmp_path))
    public = tmp_path / "public" / "admin.pub"
    before = public.read_bytes()
    public.unlink()

    assert keys.generate(str(tmp_path)) == []
    assert public.read_bytes() == before


def test_a_container_reads_its_own_key_and_everyone_s_public_one(tmp_path):
    keys.generate(str(tmp_path))
    # As the compose file mounts them in the trader's container.
    container = tmp_path / "container"
    (container / "own").mkdir(parents=True)
    (container / "own" / "private.key").write_bytes((tmp_path / "trader" / "private.key").read_bytes())
    (container / "public").symlink_to(tmp_path / "public")

    private = keys.private_key(str(container))
    publics = keys.public_keys(str(container))

    assert sorted(publics) == sorted(keys.ROLES)
    envelope = seal("internal.token", {"token": "t" * 43}, {"trader": publics["trader"]})
    assert open_sealed("internal.token", envelope, "trader", private) == {"token": "t" * 43}


def test_a_missing_key_is_said_with_its_path_and_how_it_is_made(tmp_path):
    with pytest.raises(keys.KeyProblem, match="python -m src.vault keygen"):
        keys.private_key(str(tmp_path))
    assert keys.public_keys(str(tmp_path)) == {}
    assert keys.has_private_key(str(tmp_path)) is False
