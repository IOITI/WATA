import json
import os
import stat

import pytest

from src.admin.mcp_server.tokens import MAX_ACTIVE, TOKEN_FORMAT, TokenError, TokenStore, digest


class Clock:
    def __init__(self, now=1_760_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "admin" / "mcp_tokens.json")


@pytest.fixture
def store(path, clock):
    return TokenStore(path, clock=clock)


def test_a_token_is_shown_once_and_only_its_digest_is_kept(store, path):
    token, entry = store.create("claude-mac", days=90)

    assert TOKEN_FORMAT.match(token)
    assert "sha256" not in entry
    written = open(path).read()
    assert token not in written
    assert digest(token) in written
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_the_token_opens_and_says_who_it_is(store):
    token, entry = store.create("claude-mac", days=90)

    caller = store.verify(token)

    assert (caller.name, caller.token_id, caller.expires_at) == ("claude-mac", entry["id"], entry["expires_at"])


@pytest.mark.parametrize("presented", ["", None, "wata_mcp_", "Bearer x", "wata_mcp_0123abcd_short"])
def test_anything_not_shaped_like_a_token_opens_nothing(store, presented):
    store.create("claude-mac")

    assert store.verify(presented) is None


def test_the_right_id_with_another_secret_opens_nothing(store):
    token, _ = store.create("claude-mac")
    prefix, secret = token.rsplit("_", 1)
    other = prefix + "_" + ("A" if secret[0] != "A" else "B") + secret[1:]

    assert store.verify(other) is None


def test_a_token_stops_at_its_expiry(store, clock):
    token, _ = store.create("claude-mac", days=1)
    clock.now += 86_400 - 1
    assert store.verify(token) is not None

    clock.now += 1

    assert store.verify(token) is None
    assert store.entries()[0]["state"] == "expired"


def test_a_revoked_token_opens_nothing_and_its_name_can_be_used_again(store):
    token, _ = store.create("claude-mac")

    revoked = store.revoke("claude-mac")

    assert [entry["name"] for entry in revoked] == ["claude-mac"]
    assert store.verify(token) is None
    assert store.entries()[0]["state"] == "revoked"
    new_token, _ = store.create("claude-mac")
    assert store.verify(new_token).name == "claude-mac"


def test_a_token_can_be_revoked_by_its_id(store):
    token, entry = store.create("claude-mac")

    store.revoke(entry["id"])

    assert store.verify(token) is None


def test_revoking_what_does_not_exist_says_so(store):
    with pytest.raises(TokenError, match="No valid token"):
        store.revoke("nobody")


def test_a_name_still_valid_is_not_given_twice(store):
    store.create("claude-mac")

    with pytest.raises(TokenError, match="revoke it first"):
        store.create("claude-mac")


def test_only_a_few_tokens_are_valid_at_once(store):
    for number in range(MAX_ACTIVE):
        store.create(f"machine-{number}")

    with pytest.raises(TokenError, match="revoke one first"):
        store.create("one-more")


@pytest.mark.parametrize("name", ["", "Claude", "claude mac", "-claude", "a" * 41, "claude_mac"])
def test_a_name_is_lowercase_letters_digits_and_dashes(store, name):
    with pytest.raises(TokenError, match="lowercase"):
        store.create(name)


@pytest.mark.parametrize("days", [0, 366, -1])
def test_a_token_lasts_a_day_to_a_year(store, days):
    with pytest.raises(TokenError, match="days"):
        store.create("claude-mac", days=days)


def test_a_token_made_by_the_command_line_opens_the_running_admin_at_once(path, clock):
    admin = TokenStore(path, clock=clock)
    assert admin.verify("wata_mcp_00000000_" + "A" * 43) is None

    command_line = TokenStore(path, clock=clock)
    token, _ = command_line.create("claude-mac")
    assert admin.verify(token).name == "claude-mac"

    command_line.revoke("claude-mac")
    assert admin.verify(token) is None


def test_a_damaged_file_lets_no_token_in_and_is_never_written_over(store, path, clock):
    token, _ = store.create("claude-mac")
    with open(path, "w") as token_file:
        token_file.write("{not json")

    admin = TokenStore(path, clock=clock)
    assert admin.verify(token) is None
    assert not admin.readable()
    with pytest.raises(TokenError, match="move it away"):
        admin.create("another")
    assert open(path).read() == "{not json"


def test_an_entry_that_is_not_what_this_code_writes_refuses_the_whole_file(store, path, clock):
    token, _ = store.create("claude-mac")
    record = json.load(open(path))
    record["tokens"][0]["sha256"] = "not a digest"
    with open(path, "w") as token_file:
        json.dump(record, token_file)

    assert TokenStore(path, clock=clock).verify(token) is None


def test_the_list_never_shows_a_digest_and_says_when_a_token_was_last_used(store, clock):
    token, _ = store.create("claude-mac")
    assert store.entries()[0]["last_used_at"] is None

    store.verify(token)

    listed = store.entries()
    assert "sha256" not in listed[0]
    assert listed[0]["last_used_at"] == "2025-10-09T08:53:20Z"
    assert listed[0]["state"] == "active"
