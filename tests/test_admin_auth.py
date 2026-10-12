import asyncio
import json
import os
import stat

import pytest

from src.admin.auth import (
    MIN_PASSWORD_LENGTH,
    CredentialStore,
    LoginThrottle,
    SessionStore,
    TrustedProxies,
    matching_totp_counter,
    totp_code,
    totp_uri,
)

PASSWORD = "correct horse battery"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def credentials(tmp_path):
    return CredentialStore(str(tmp_path / "admin" / "credentials.json"))


def test_credentials_are_not_configured_until_a_password_is_set(credentials):
    assert not credentials.is_configured()
    assert credentials.version() is None
    assert not credentials.verify(PASSWORD)


def test_password_round_trip(credentials):
    credentials.set_password(PASSWORD)

    assert credentials.is_configured()
    assert credentials.verify(PASSWORD)
    assert not credentials.verify(PASSWORD + "x")
    assert not credentials.verify("")


def test_password_is_stored_hashed_in_a_private_file(credentials):
    credentials.set_password(PASSWORD)

    with open(credentials.path) as credentials_file:
        content = credentials_file.read()
    assert PASSWORD not in content
    assert json.loads(content)["algorithm"] == "scrypt"
    assert stat.S_IMODE(os.stat(credentials.path).st_mode) == 0o600


def test_short_password_is_refused(credentials):
    with pytest.raises(ValueError):
        credentials.set_password("x" * (MIN_PASSWORD_LENGTH - 1))

    assert not credentials.is_configured()


def test_same_password_gets_a_different_hash_each_time(credentials):
    credentials.set_password(PASSWORD)
    with open(credentials.path) as credentials_file:
        first = json.load(credentials_file)

    credentials.set_password(PASSWORD)
    with open(credentials.path) as credentials_file:
        second = json.load(credentials_file)

    assert first["salt"] != second["salt"]
    assert first["hash"] != second["hash"]


def test_corrupt_credentials_file_refuses_every_password(credentials):
    os.makedirs(os.path.dirname(credentials.path))
    with open(credentials.path, "w") as credentials_file:
        credentials_file.write("not json")

    assert not credentials.verify(PASSWORD)


def test_session_is_valid_until_idle_timeout():
    clock = FakeClock()
    sessions = SessionStore(idle_seconds=60, max_age_seconds=3600, clock=clock)
    token = sessions.create()

    clock.advance(59)
    assert sessions.touch(token)
    clock.advance(59)
    assert sessions.touch(token)

    clock.advance(61)
    assert not sessions.touch(token)


def test_session_ends_at_its_absolute_lifetime_even_when_active():
    clock = FakeClock()
    sessions = SessionStore(idle_seconds=60, max_age_seconds=100, clock=clock)
    token = sessions.create()

    clock.advance(50)
    assert sessions.touch(token)
    clock.advance(51)
    assert not sessions.touch(token)


def test_unknown_and_revoked_sessions_are_refused():
    sessions = SessionStore(idle_seconds=60, max_age_seconds=3600)
    token = sessions.create()

    assert not sessions.touch("not-a-session")
    sessions.revoke(token)
    assert not sessions.touch(token)


def test_changing_the_password_closes_open_sessions():
    sessions = SessionStore(idle_seconds=60, max_age_seconds=3600)
    token = sessions.create(credential_version=1)

    assert sessions.touch(token, credential_version=1)
    assert not sessions.touch(token, credential_version=2)
    assert not sessions.touch(token, credential_version=1)


def test_throttle_locks_out_after_consecutive_failures():
    clock = FakeClock()
    throttle = LoginThrottle(max_failures=3, lockout_seconds=600, clock=clock)

    throttle.record_failure("1.2.3.4")
    throttle.record_failure("1.2.3.4")
    assert throttle.retry_after("1.2.3.4") == 0

    throttle.record_failure("1.2.3.4")
    assert throttle.retry_after("1.2.3.4") > 0
    assert throttle.retry_after("5.6.7.8") == 0

    clock.advance(601)
    assert throttle.retry_after("1.2.3.4") == 0


def test_successful_login_resets_the_failure_count():
    throttle = LoginThrottle(max_failures=3, lockout_seconds=600)

    throttle.record_failure("1.2.3.4")
    throttle.record_failure("1.2.3.4")
    throttle.record_success("1.2.3.4")
    throttle.record_failure("1.2.3.4")
    throttle.record_failure("1.2.3.4")

    assert throttle.retry_after("1.2.3.4") == 0


# ── second factor ──

# RFC 6238, appendix B: the SHA-1 test vectors, with the ASCII secret "12345678901234567890".
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.parametrize("unix_time,code", [
    (59, "287082"),
    (1111111109, "081804"),
    (1111111111, "050471"),
    (1234567890, "005924"),
    (2000000000, "279037"),
])
def test_codes_match_the_rfc_test_vectors(unix_time, code):
    assert totp_code(RFC_SECRET, unix_time // 30) == code


def test_code_of_the_neighbouring_periods_is_accepted_but_not_an_older_one():
    now = 1111111111
    current = now // 30

    assert matching_totp_counter(RFC_SECRET, totp_code(RFC_SECRET, current), now) == current
    assert matching_totp_counter(RFC_SECRET, totp_code(RFC_SECRET, current - 1), now) == current - 1
    assert matching_totp_counter(RFC_SECRET, totp_code(RFC_SECRET, current + 1), now) == current + 1
    assert matching_totp_counter(RFC_SECRET, totp_code(RFC_SECRET, current - 2), now) is None


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "12 34", None])
def test_malformed_code_is_never_valid(code):
    assert matching_totp_counter(RFC_SECRET, code, 1111111111) is None


def test_code_typed_with_a_space_in_the_middle_is_accepted():
    now = 1111111111

    assert matching_totp_counter(RFC_SECRET, "050 471", now) == now // 30


def test_second_factor_is_off_until_enabled_and_needs_a_password_first(credentials):
    assert not credentials.totp_enabled()
    with pytest.raises(ValueError):
        credentials.enable_totp()

    credentials.set_password(PASSWORD)
    secret = credentials.enable_totp()

    assert credentials.totp_enabled()
    assert len(secret) == 32
    assert stat.S_IMODE(os.stat(credentials.path).st_mode) == 0o600


def test_code_is_accepted_once(credentials):
    credentials.set_password(PASSWORD)
    secret = credentials.enable_totp()
    now = 1_800_000_000
    code = totp_code(secret, now // 30)

    assert credentials.verify_totp(code, now=now)
    assert not credentials.verify_totp(code, now=now)
    # Nor an older code once a newer one was used.
    assert not credentials.verify_totp(totp_code(secret, now // 30 - 1), now=now)
    assert credentials.verify_totp(totp_code(secret, now // 30 + 1), now=now + 30)


def test_no_code_is_valid_while_the_second_factor_is_off(credentials):
    credentials.set_password(PASSWORD)

    assert not credentials.verify_totp("123456")


def test_changing_the_password_keeps_the_second_factor(credentials):
    credentials.set_password(PASSWORD)
    secret = credentials.enable_totp()

    credentials.set_password("another long password")

    assert credentials.totp_enabled()
    assert credentials.verify_totp(totp_code(secret, 1_800_000_000 // 30), now=1_800_000_000)
    assert credentials.verify("another long password")


def test_disabling_and_changing_the_second_factor_closes_sessions(credentials):
    credentials.set_password(PASSWORD)
    os.utime(credentials.path, ns=(1, 1))
    before = credentials.version()

    credentials.enable_totp()
    enabled = credentials.version()
    os.utime(credentials.path, ns=(2, 2))
    credentials.disable_totp()

    assert before != enabled != credentials.version()
    assert not credentials.totp_enabled()
    assert credentials.verify(PASSWORD)


def test_enrolment_address_carries_the_secret_and_the_standard_parameters():
    uri = totp_uri("ABCDEF")

    assert uri.startswith("otpauth://totp/WATA%20Admin:operator?secret=ABCDEF&issuer=WATA%20Admin")
    assert "digits=6" in uri and "period=30" in uri


def test_session_liveness_can_be_read_without_counting_as_activity():
    clock = FakeClock()
    sessions = SessionStore(idle_seconds=60, max_age_seconds=3600, clock=clock)
    token = sessions.create(credential_version=1)

    clock.advance(59)
    assert sessions.is_alive(token, credential_version=1)
    clock.advance(2)
    # Looking at it did not push its idle timeout back.
    assert not sessions.is_alive(token, credential_version=1)
    assert not sessions.is_alive("unknown", credential_version=1)


# ── reverse proxy ──


class FakeResolver:
    def __init__(self, addresses):
        self.addresses = addresses
        self.calls = []

    async def __call__(self, name):
        self.calls.append(name)
        found = self.addresses.get(name)
        if found is None:
            raise OSError(f"Name or service not known: {name}")
        return found


def test_no_proxy_is_trusted_unless_one_is_named():
    proxies = TrustedProxies()

    assert not proxies.is_trusted("172.18.0.2")
    assert not proxies.is_trusted("127.0.0.1")


def test_proxies_given_as_addresses_or_networks_need_no_lookup():
    resolver = FakeResolver({})
    proxies = TrustedProxies(["172.18.0.2", "10.5.0.0/24", " ", "fd00::2"], resolve=resolver)

    asyncio.run(proxies.keep_refreshed())

    assert resolver.calls == []
    assert proxies.is_trusted("172.18.0.2")
    assert proxies.is_trusted("10.5.0.77")
    assert proxies.is_trusted("fd00::2")
    # The same proxy seen through an IPv6 socket.
    assert proxies.is_trusted("::ffff:172.18.0.2")
    assert not proxies.is_trusted("172.18.0.3")
    assert not proxies.is_trusted("10.5.1.1")


@pytest.mark.parametrize("peer", ["unknown", "testclient", "", "traefik", "172.18.0.2, 10.0.0.1"])
def test_a_peer_that_is_not_an_address_is_never_trusted(peer):
    proxies = TrustedProxies(["traefik", "172.18.0.2"])

    assert not proxies.is_trusted(peer)


def test_a_proxy_given_by_name_is_trusted_once_resolved_and_followed_when_it_moves():
    resolver = FakeResolver({"traefik": ["172.18.0.2", "172.18.0.2"]})
    proxies = TrustedProxies("traefik", resolve=resolver)
    assert not proxies.is_trusted("172.18.0.2")

    asyncio.run(proxies.refresh())
    assert proxies.is_trusted("172.18.0.2")

    # The container was recreated with another address.
    resolver.addresses["traefik"] = ["172.18.0.6"]
    asyncio.run(proxies.refresh())

    assert proxies.is_trusted("172.18.0.6")
    assert not proxies.is_trusted("172.18.0.2")


def test_a_proxy_that_stops_resolving_keeps_its_last_address(caplog):
    resolver = FakeResolver({"traefik": ["172.18.0.2"]})
    proxies = TrustedProxies(["traefik"], resolve=resolver)
    asyncio.run(proxies.refresh())
    del resolver.addresses["traefik"]

    with caplog.at_level("WARNING", logger="src.admin.auth"):
        asyncio.run(proxies.refresh())
        asyncio.run(proxies.refresh())

    assert proxies.is_trusted("172.18.0.2")
    # Said once, not every thirty seconds for as long as it lasts.
    assert [message for message in caplog.messages if "Unable to resolve" in message] == [
        "Unable to resolve the reverse proxy 'traefik' (OSError('Name or service not known: traefik')): "
        "its last known address is kept"
    ]

    # Back, at another address: followed again, and a later failure is reported again.
    resolver.addresses["traefik"] = ["172.18.0.6"]
    asyncio.run(proxies.refresh())
    del resolver.addresses["traefik"]
    with caplog.at_level("WARNING", logger="src.admin.auth"):
        asyncio.run(proxies.refresh())

    assert proxies.is_trusted("172.18.0.6") and not proxies.is_trusted("172.18.0.2")
    assert sum("Unable to resolve" in message for message in caplog.messages) == 2


def test_a_proxy_that_never_resolved_is_not_trusted_and_reported_once(caplog):
    proxies = TrustedProxies(["traefik"], resolve=FakeResolver({}))

    with caplog.at_level("WARNING", logger="src.admin.auth"):
        asyncio.run(proxies.refresh())
        asyncio.run(proxies.refresh())

    assert not proxies.is_trusted("172.18.0.2")
    assert sum("Unable to resolve the reverse proxy" in message for message in caplog.messages) == 1
