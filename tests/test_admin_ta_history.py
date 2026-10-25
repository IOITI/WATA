"""
How many bars the TA engine runs a rule's script on (src/admin/ta_history.py),
which the signal rule's dialog says under its days of history: counted as the
engine loads them, and the route that reads the feed's rows for it.
"""

import types
from datetime import datetime, timezone

import pytest

from src.admin.ta_history import DAY, HistoryCount, last_restart
from src.ta_engine.engine import Engine
from src.ta_engine.settings import TaEngineSettings
from tests.test_admin_api import config_path, make_ctx  # noqa: F401 (fixtures)
from tests.test_admin_ta_rules import _client, feed_config_path  # noqa: F401 (fixtures)
from tests.test_ta_engine import T0, engine_with, run, scripts, ten_second_rows  # noqa: F401 (fixtures)

RESTART_MINUTE = 21 * 60 + 15
# The restart before T0 (Thursday 15 Oct 2026, 08:00 UTC): Wednesday 14 Oct, 21:15 UTC.
RESTART = T0 - 10 * 3600 - 45 * 60


def counted(rows, bars, history_days, *, now=T0, side="bid", decimals=1):
    """The count of a rule of these bars and days, at ``now``, its rows read as the route reads them."""
    restart = last_restart(now, RESTART_MINUTE)
    count = HistoryCount(bars, side, decimals, start=restart - history_days * DAY, restart=restart, now=now)
    count.add([row for row in rows if count.first <= row[0] < now])
    count.finish()
    return count


@pytest.mark.parametrize("moment", [
    T0, RESTART, RESTART - 1, RESTART + 1, T0 - T0 % DAY, T0 - T0 % DAY - 0.5, RESTART + DAY - 0.25,
])
def test_the_last_restart_is_the_engines(moment):
    engine = types.SimpleNamespace(_settings=TaEngineSettings(restart_time="21:15"))

    assert last_restart(moment, RESTART_MINUTE) == Engine.last_restart(engine, moment)


@pytest.mark.parametrize("bars, history_days", [
    ({"type": "time", "timeframe": "1"}, 0),
    ({"type": "time", "timeframe": "1"}, 1),
    ({"type": "time", "timeframe": "45S"}, 1),
    ({"type": "time", "timeframe": "60"}, 2),
    ({"type": "range", "ticks": 60}, 1),
    ({"type": "range", "ticks": 25}, 2),
])
def test_the_bars_counted_are_those_the_engine_loads(scripts, bars, history_days):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts, bars=bars, history_days=history_days)
    # Two and a half days of ten seconds before T0, which the feed has written up to.
    source.rows_by_time = ten_second_rows(T0 - 60 * 3600, 60 * 360)

    run(engine.tick())

    load = next(request for request in runner.requests if request["op"] == "load")
    count = counted(source.rows_by_time, bars, history_days)
    assert count.loaded == len(load["bars"]) > 0
    assert count.at_restart == sum(1 for bar in load["bars"] if bar[6] <= RESTART * 1000) < count.loaded
    if history_days == 0:
        assert count.at_restart == 0


def test_a_day_is_24_hours_back_from_the_restart_and_a_weekend_brings_no_bar():
    # Friday 16 Oct 2026, 13:00 to 21:00 UTC, then Sunday 18 Oct 23:00 to Monday 19 Oct 10:00.
    friday = int(datetime(2026, 10, 16, 13, tzinfo=timezone.utc).timestamp())
    sunday = int(datetime(2026, 10, 18, 23, tzinfo=timezone.utc).timestamp())
    monday = int(datetime(2026, 10, 19, 10, tzinfo=timezone.utc).timestamp())
    rows = ten_second_rows(friday, 8 * 360) + ten_second_rows(sunday, 11 * 360, seed=3)
    minutes = {"type": "time", "timeframe": "1"}

    one_day = counted(rows, minutes, 1, now=monday)
    three_days = counted(rows, minutes, 3, now=monday)

    # Restarted on Sunday at 21:15: the day before it is the weekend, and Friday's session three days back.
    assert (one_day.at_restart, one_day.loaded) == (0, 11 * 60)
    assert (three_days.at_restart, three_days.loaded) == (8 * 60, 8 * 60 + 11 * 60)
    days = one_day.engine_days()
    assert [datetime.fromtimestamp(day["end"], timezone.utc).strftime("%a") for day in days] == [
        "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
    ]
    assert [day["bars"] for day in days] == [0, 0, 0, 0, 8 * 60, 0, 0]
    assert days[-1]["end"] == last_restart(monday, RESTART_MINUTE) and days[0]["start"] == days[-1]["end"] - 7 * DAY


def test_the_week_is_counted_whatever_the_rules_days():
    rows = ten_second_rows(T0 - 9 * DAY, 9 * 8640)
    minutes = {"type": "time", "timeframe": "5"}

    short, long = counted(rows, minutes, 0), counted(rows, minutes, 8)

    assert [day["bars"] for day in short.engine_days()] == [day["bars"] for day in long.engine_days()] == [288] * 7
    assert (short.at_restart, long.at_restart) == (0, 8 * 288)
    assert short.loaded == long.loaded - 8 * 288


# ── the route ──


@pytest.fixture
def api(feed_config_path, tmp_path, scripts, monkeypatch):  # noqa: F811
    import src.admin.routers.ta as ta_router

    monkeypatch.setattr(ta_router, "_now", lambda: T0)
    context = make_ctx(feed_config_path, tmp_path)
    context.queries.capital_rows = [{"id": 7, "epic": "US100", "label": "us100", "decimals": 1, "first_t": T0 - 3 * DAY}]
    context.queries.capital_engine_bar_rows = ten_second_rows(T0 - 60 * 3600, 60 * 360)
    client = _client(context, tmp_path, scripts)
    yield client, context
    client.__exit__(None, None, None)


def test_the_route_counts_the_bars_of_the_rules_settings(api):
    client, context = api

    body = client.get("/api/ta/history?epic=US100&side=bid&timeframe=1&days=2").json()

    expected = counted(context.queries.capital_engine_bar_rows, {"type": "time", "timeframe": "1"}, 2)
    assert (body["history_days"], body["restart_time"], body["at_restart"], body["now"]) == (
        2, "21:15", expected.at_restart, expected.loaded,
    )
    assert body["bars"] == {"type": "time", "timeframe": "1"} and body["asset"] == {"provider": "capital", "key": "US100"}
    assert (body["restarted_at"], body["starts_at"]) == ("2026-10-14T21:15:00+00:00", "2026-10-12T21:15:00+00:00")
    assert [day["bars"] for day in body["engine_days"]] == [day["bars"] for day in expected.engine_days()]
    # From the first of the week's engine days to now, the instrument's rows by its id, a page at a time.
    read = [call for call in context.queries.calls if call[0] == "capital_engine_rows"]
    assert (read[0][1], read[0][2].timestamp(), read[0][3].timestamp()) == (7, RESTART - 7 * DAY, T0)
    assert len(read) == 2 and read[1][2].timestamp() == context.queries.capital_engine_bar_rows[19_999][0] + 1


def test_without_days_of_its_own_a_rule_has_the_engines(api):
    client, context = api
    context.config_manager.config_data["trade"]["config"]["ta_engine"] = {"history_days": 2, "restart_time": "22:00"}

    body = client.get("/api/ta/history?epic=US100&ticks=60").json()

    assert (body["history_days"], body["restart_time"], body["bars"]) == (2, "22:00", {"type": "range", "ticks": 60})
    assert body["restarted_at"] == "2026-10-14T22:00:00+00:00"


def test_the_rows_are_read_a_page_at_a_time(api, monkeypatch):
    import src.admin.routers.ta as ta_router

    client, context = api
    whole = client.get("/api/ta/history?epic=US100&ticks=60&days=1").json()
    monkeypatch.setattr(ta_router, "HISTORY_PAGE", 5000)

    paged = client.get("/api/ta/history?epic=US100&ticks=60&days=1").json()

    assert paged == whole
    assert len([call for call in context.queries.calls if call[0] == "capital_engine_rows" and call[4] == 5000]) > 2


@pytest.mark.parametrize("query, status", [
    ("epic=US500&timeframe=1", 404),
    ("epic=US100&timeframe=7S", 422),
    ("epic=US100", 422),
    ("epic=US100&ticks=0", 422),
    ("epic=US100&timeframe=1&days=31", 422),
    ("epic=US100&timeframe=1&side=mid", 422),
    ("epic=../x&timeframe=1", 422),
])
def test_what_the_route_cannot_count(api, query, status):
    client, _ = api

    assert client.get(f"/api/ta/history?{query}").status_code == status


def test_the_rules_say_when_the_engine_starts_them_again(api):
    client, context = api
    assert client.get("/api/ta/rules").json()["engine_restart_time"] == "21:15"

    context.config_manager.config_data["trade"]["config"]["ta_engine"] = {"restart_time": "22:05"}

    assert client.get("/api/ta/rules").json()["engine_restart_time"] == "22:05"
