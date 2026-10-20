"""
Capital.com in the admin's Data pages: the provider, its settings, its assets
and every view of one, read from the feed's tables (faked here as in
tests/test_admin_api.py, whose fixtures these tests use).
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src.admin.data import (
    DISABLED,
    DOWN,
    ENDED,
    OK,
    RECORDING,
    WARNING,
    AssetNotFound,
    capital_asset,
    capital_epic,
    capital_provider_state,
    capital_status,
)
from src.capital_feed.day_files import day_path, write_day_file
from tests.test_admin_api import (  # noqa: F401 (fixtures)
    CLOSED_POSITION,
    PASSWORD,
    SAME_ORIGIN,
    _provider,
    anonymous,
    client,
    config_path,
    ctx,
    make_client,
    make_ctx,
    patch_now,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
# Thursday 15 Oct 2026, 10:00 in Paris.
T = int(NOW.timestamp())
HOURS = {
    **{day: ["00:00 - 21:59", "23:05 - 00:00"] for day in ("mon", "tue", "wed", "thu")},
    "fri": ["00:00 - 21:59"], "sat": [], "sun": ["23:05 - 00:00"], "zone": "UTC",
}
US100 = {
    "id": 3, "epic": "US100", "label": "us100", "name": "US Tech 100", "instrument_type": "INDICES",
    "currency": "USD", "decimals": 1, "first_seen_at": NOW - timedelta(days=2), "last_seen_at": NOW,
    "first_t": T - 2 * 86400, "last_t": T - 10, "last_bid": 25000.5, "last_ask": 25001.5,
}


def _state(state="streaming", seconds_ago=5, detail="Streaming US100", **figures):
    return {
        "updated_at": datetime.now(UTC) - timedelta(seconds=seconds_ago), "started_at": NOW - timedelta(hours=1),
        "version": "0.20.0", "environment": "demo", "state": state, "detail": detail,
        "figures": {
            "instruments": [{"epic": "US100", "label": "us100", "market": "open", "status": "TRADEABLE",
                             "next_opening": None, "last_quote_at": "2026-10-15T07:59:59Z", "subscription": "PROCESSED"}],
            "quotes_per_minute": 246, "delay_ms": {"p50": 38.0, "p95": 95.0, "max": 140.0, "count": 246},
            **figures,
        },
    }


# ── the feed's state and its assets, as plain functions ──


@pytest.mark.parametrize("enabled, state, expected", [
    (False, None, (DISABLED, "Switched off in the configuration")),
    (True, None, (DOWN, 'Never started: add "capital" to COMPOSE_PROFILES in .env and start it')),
    (True, _state(seconds_ago=6 * 60), (DOWN, "No sign of it for 6 min")),
    (True, _state(), (OK, "Streaming US100 · 4.1 quotes/s · delay 38 ms")),
    (True, _state("connecting", detail="Connecting to Capital.com's stream"), (WARNING, "Connecting to Capital.com's stream")),
    (True, _state("refused", detail="Capital.com refused the login (error.invalid.details): check ..."),
     (DOWN, "Capital.com refused the login (error.invalid.details): check ...")),
    (True, _state("no_credentials", detail="No Capital.com login: CAPITAL_API_KEY missing in .env."),
     (DOWN, "No Capital.com login: CAPITAL_API_KEY missing in .env.")),
    (False, _state(), (WARNING, "Switched off in the configuration: it records until it is restarted")),
    (True, _state("disabled", detail="switched off"), (WARNING, "Switched on in the configuration: it starts at its next start")),
    (False, _state("disabled", detail="switched off"), (DISABLED, "Switched off in the configuration")),
])
def test_the_feed_is_described_by_what_it_last_wrote_of_itself(enabled, state, expected):
    assert capital_provider_state(enabled, state, datetime.now(UTC)) == expected


def test_closed_markets_say_when_the_first_one_opens_again():
    state = _state("market_closed", detail="Every market followed is closed")
    state["figures"]["instruments"][0].update(market="closed", next_opening="2026-10-15T23:05:00Z")

    assert capital_provider_state(True, state, datetime.now(UTC)) == (OK, "Markets closed: the first opens at 2026-10-15T23:05:00Z")


def test_an_instrument_of_capital_com_is_an_index_named_by_its_epic():
    asset = capital_asset(US100)

    assert (asset["provider"], asset["key"], asset["ref"], asset["role"], asset["indice"]) == (
        "capital", "US100", "capital/US100", "underlying", "us100",
    )
    assert (asset["symbol"], asset["description"], asset["asset_type"], asset["uic"]) == ("US100", "US Tech 100", "Capital.com", None)
    assert (asset["first_t"], asset["last_bid"]) == (US100["first_t"], 25000.5)


def test_an_instrument_is_recording_while_the_feed_runs_and_follows_it():
    now = datetime.now(UTC)
    asset = capital_asset(US100)

    assert capital_status(asset, _state(), now) == RECORDING
    assert capital_status(asset, _state(seconds_ago=600), now) == ENDED
    assert capital_status(asset, _state("no_credentials"), now) == ENDED
    assert capital_status({**asset, "epic": "US500"}, _state(), now) == ENDED
    assert capital_status(asset, None, now) == ENDED


@pytest.mark.parametrize("key", ["us100", "US 100", "", "../US100", "US100;DROP"])
def test_a_key_that_is_not_an_epic_is_not_looked_for(key):
    with pytest.raises(AssetNotFound):
        capital_epic(key)


# ── the provider ──


def _with_feed(config_path, tmp_path, **section):
    config = json.load(open(config_path))
    config["trade"]["config"]["capital_feed"] = {"enabled": True, **section}
    config.setdefault("admin", {}).setdefault("persistant", {})["price_history_path"] = str(tmp_path / "prices")
    with open(config_path, "w") as config_file:
        json.dump(config, config_file)
    return make_ctx(config_path, tmp_path)


def _signed_in(ctx, tmp_path):
    client = make_client(ctx, tmp_path / "no-interface")
    client.__enter__()
    client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
    return client


@pytest.fixture
def feed(config_path, tmp_path):
    ctx = _with_feed(config_path, tmp_path)
    ctx.queries.capital_rows = [US100]
    ctx.queries.capital_state = _state()
    ctx.queries.capital_hours = HOURS
    client = _signed_in(ctx, tmp_path)
    yield ctx, client
    client.__exit__(None, None, None)


def test_a_feed_switched_on_and_streaming_is_a_provider_at_work(feed):
    ctx, client = feed

    capital = _provider(client.get("/api/data/providers").json(), "capital")

    assert (capital["status"], capital["detail"]) == ("ok", "Streaming US100 · 4.1 quotes/s · delay 38 ms")
    assert (capital["enabled"], capital["active"], capital["assets"]) == (True, True, 1)
    assert (capital["first_t"], capital["last_t"]) == (US100["first_t"], US100["last_t"])
    assert capital["feed"]["environment"] == "demo" and capital["feed"]["figures"]["delay_ms"]["p50"] == 38.0


def test_the_provider_is_listed_while_the_database_is_down(feed):
    ctx, client = feed
    ctx.queries.failing = True

    capital = _provider(client.get("/api/data/providers").json(), "capital")

    assert (capital["status"], capital["detail"], capital["assets"]) == ("unknown", "The database is unavailable", None)


def test_the_provider_says_how_it_is_set_what_it_missed_today_and_its_day_files(feed, tmp_path):
    ctx, client = feed
    root = str(tmp_path / "prices")
    for day in (date(2026, 10, 13), date(2026, 10, 14)):
        write_day_file(day_path(root, "us100", day), [(NOW, 1.0, 2.0, 0.5, 1.5, 1.1, 2.1, 0.6, 1.6, 4)],
                       epic="US100", label="us100", name="US Tech 100")

    body = client.get("/api/data/providers/capital").json()

    assert body["settings"] == {
        "enabled": True, "environment": "demo", "instruments": [{"epic": "US100", "label": "us100"}],
        "minute_volume": True, "day_files": True, "retention_months": 0, "alert_after_seconds": 300,
    }
    (files,) = body["day_files"]
    assert (files["label"], files["days"], files["first_day"], files["last_day"], files["location"]) == (
        "us100", 2, "2026-10-13", "2026-10-14", "capital/bars/us100",
    )
    assert files["bytes"] > 0
    # The names of the login's variables, never anything of their values.
    assert body["credential_variables"] == ["CAPITAL_API_KEY", "CAPITAL_IDENTIFIER", "CAPITAL_PASSWORD"]
    assert body["gaps_today"] is not None
    assert ("capital_opening_hours", "US100") in ctx.queries.calls


def test_the_feed_appears_on_the_system_page_once_switched_on(feed):
    ctx, client = feed

    services = {service["id"]: service for service in client.get("/api/system/health").json()["services"]}

    assert (services["capital_feed"]["name"], services["capital_feed"]["status"]) == ("Capital.com feed", "ok")

    ctx.queries.capital_state = _state("refused", detail="Capital.com refused the login (error.invalid.details)")
    services = {service["id"]: service for service in client.get("/api/system/health").json()["services"]}
    assert services["capital_feed"]["status"] == "down"


def test_a_feed_switched_off_is_not_on_the_system_page(client):
    assert "capital_feed" not in {service["id"] for service in client.get("/api/system/health").json()["services"]}


def test_the_log_of_the_feed_can_be_read(client):
    services = {service["id"]: service["name"] for service in client.get("/api/logs").json()["services"]}

    assert services["wata-capital-feed"] == "Capital.com feed"


# ── its settings ──


def test_the_section_is_read_with_what_the_form_needs(client):
    body = client.get("/api/data/providers/capital/config").json()

    assert body["section"] == {}
    assert body["defaults"]["instruments"] == [{"epic": "US100", "label": "us100"}]
    assert body["defaults"]["environment"] == "demo"
    assert body["environments"] == ["demo", "live"]
    assert body["indices"] == ["us100"] and body["editable"] is True


def test_a_section_saved_goes_through_the_validated_save_with_a_backup_and_an_audit_line(client, ctx, config_path):
    revision = client.get("/api/data/providers/capital/config").json()["revision"]
    section = {"enabled": True, "environment": "demo", "instruments": [{"epic": "US100", "label": "us100"}]}

    response = client.put("/api/data/providers/capital/config", json={"section": section, "revision": revision}, headers=SAME_ORIGIN)

    assert response.status_code == 200 and response.json()["restart_required"] is True
    assert json.load(open(config_path))["trade"]["config"]["capital_feed"] == section
    assert ctx.audit_log.read(1)[0]["action"] == "Capital.com feed configuration saved"


@pytest.mark.parametrize("section, words", [
    ({"environment": "paper"}, "environment"),
    ({"instruments": [{"epic": "us100", "label": "us100"}]}, "epic"),
    ({"instruments": [{"epic": "US100", "label": "a"}, {"epic": "US100", "label": "b"}]}, "twice"),
    ({"instruments": [{"epic": "US100", "label": "x"}, {"epic": "US500", "label": "x"}]}, "labelled"),
])
def test_a_section_the_feed_would_refuse_is_refused_before_it_is_saved(client, config_path, section, words):
    revision = client.get("/api/data/providers/capital/config").json()["revision"]
    before = open(config_path).read()

    response = client.put("/api/data/providers/capital/config", json={"section": section, "revision": revision}, headers=SAME_ORIGIN)

    assert response.status_code == 422 and words in response.json()["detail"]
    assert open(config_path).read() == before


# ── its assets ──


def test_its_instruments_are_listed_first_as_the_indexes_followed_live(feed):
    ctx, client = feed

    body = client.get("/api/data/assets").json()

    assert [(item["ref"], item["status"]) for item in body["items"][:2]] == [("capital/US100", "recording"), ("saxo/1", "recording")]
    us100 = body["items"][0]
    assert (us100["provider"], us100["last_bid"], us100["indice"]) == ("capital", 25000.5, "us100")


def test_an_instrument_the_feed_no_longer_follows_has_ended(feed):
    ctx, client = feed
    ctx.queries.capital_state = _state(seconds_ago=3600)

    us100 = client.get("/api/data/assets").json()["items"][0]

    assert (us100["ref"], us100["status"]) == ("capital/US100", "ended")


def test_an_instrument_comes_with_how_it_is_recorded_and_its_day_files(feed):
    ctx, client = feed

    body = client.get("/api/data/assets/capital/US100").json()

    assert body["asset"]["ref"] == "capital/US100" and body["asset"]["status"] == "recording"
    assert body["index"] is None
    recording = body["recording"]
    assert (recording["epic"], recording["label"], recording["environment"], recording["feed_state"]) == ("US100", "us100", "demo", "streaming")
    assert (recording["market"], recording["quotes_per_minute"], recording["delay_ms"]["p95"]) == ("open", 246, 95.0)
    assert recording["opening_hours"] == HOURS and recording["live"] is True
    assert recording["day_files"]["days"] == 0
    assert body["max_days"] == {"chart": 366, "range": 31, "statistics": 31, "availability": 0, "gaps": 31}


def test_bars_are_read_from_the_feeds_tables_with_the_last_ten_seconds_they_hold(feed):
    ctx, client = feed
    day_start = int(datetime(2026, 10, 14, 22, tzinfo=UTC).timestamp())
    ctx.queries.capital_bar_rows = [
        [day_start + 600, 25000.0, 25004.0, 24998.0, 25002.0, 25001.0, 25005.0, 24999.0, 25003.0, 12],
        [day_start + 610, 25002.0, 25003.0, 25001.0, 25001.5, 25003.0, 25004.0, 25002.0, 25002.5, 7],
    ]

    body = client.get("/api/data/assets/capital/US100/bars?from=2026-10-15&interval=10").json()

    assert body["bars"] == ctx.queries.capital_bar_rows and body["through"] == day_start + 610
    assert body["instrument"]["ref"] == "capital/US100"
    assert next(call for call in ctx.queries.calls if call[0] == "capital_bars")[1] == 3


def test_range_bars_are_built_from_the_feeds_ten_second_bars(feed):
    ctx, client = feed
    day_start = int(datetime(2026, 10, 14, 22, tzinfo=UTC).timestamp())
    ctx.queries.capital_series_rows = [(day_start + 10 * n, 25000.0 + n, 25000.5 + n, 24999.5 + n, 25000.0 + n) for n in range(40)]

    body = client.get("/api/data/assets/capital/US100/range-bars?from=2026-10-15&size=5").json()

    assert body["under"] is None and len(body["bars"]) > 3
    assert next(call for call in ctx.queries.calls if call[0] == "capital_series")[4] == "bid"


def test_a_range_in_ticks_counts_capital_coms_own_price_step(feed):
    ctx, client = feed
    day_start = int(datetime(2026, 10, 14, 22, tzinfo=UTC).timestamp())
    ctx.queries.capital_series_rows = [(day_start + 10 * n, 25000.0 + n, 25000.5 + n, 24999.5 + n, 25000.0 + n) for n in range(40)]

    body = client.get("/api/data/assets/capital/US100/range-bars?from=2026-10-15&ticks=60").json()

    # US100 is quoted to one decimal: 60 steps are 6 points, as TradingView's 60R.
    assert body["size"] == 6.0 and body["auto"] is False


def test_the_chart_is_given_watas_trades_of_its_indice_the_gaps_in_its_hours_and_capital_coms_volume(feed):
    ctx, client = feed
    ctx.queries.capital_volume_by_day = {"2026-10-15": 1234.0}

    with patch_now(NOW + timedelta(hours=2)):
        body = client.get("/api/data/assets/capital/US100/context?from=2026-10-15").json()

    assert body["trades"] == [json.loads(json.dumps(CLOSED_POSITION))]
    # WATA trades us100 alone in this configuration: every position is of it.
    trades_asked = next(call for call in ctx.queries.calls if call[0] == "index_trades")
    assert (trades_asked[1], trades_asked[4]) == ("us100", True)
    assert [signal["indice"] for signal in body["signals"]] == ["us100"]
    # Nothing recorded since midnight: a gap from the start of the day, in its opening hours, up to now.
    (gap,) = body["gaps"]
    assert gap["reason"] == "down"
    assert body["volume"] == 1234.0


def test_statistics_read_the_bars_of_the_feed_a_page_at_a_time_and_capital_coms_volume(feed):
    ctx, client = feed
    day_start = int(datetime(2026, 10, 14, 22, tzinfo=UTC).timestamp())
    ctx.queries.capital_bar_rows = [
        [day_start + 36000 + 10 * n, 25000.0, 25001.0, 24999.0, 25000.5, 25001.0, 25002.0, 25000.0, 25001.5, 3] for n in range(6)
    ]
    ctx.queries.capital_volume_by_day = {"2026-10-15": 987.0}

    body = client.get("/api/data/assets/capital/US100/statistics?from=2026-10-15").json()

    (day,) = body["days"]
    assert (day["day"], day["bars"], day["updates"], day["volume"]) == ("2026-10-15", 6, 18, 987.0)
    assert body["spread"] is True and day["spread_average"] == pytest.approx(1.0)


def test_capital_coms_volume_is_given_by_minute_or_longer(feed):
    ctx, client = feed
    ctx.queries.capital_volume_rows = [[T, 187.0], [T + 60, 168.0]]

    body = client.get("/api/data/assets/capital/US100/volume?from=2026-10-15&interval=60").json()

    assert body["columns"] == ["t", "volume"] and body["volume"] == [[T, 187.0], [T + 60, 168.0]]
    assert client.get("/api/data/assets/capital/US100/volume?from=2026-10-15&interval=10").status_code == 422
    assert client.get("/api/data/assets/saxo/2/volume?from=2026-10-15").status_code == 404
