"""
The research files written each finished day (src/admin/research_export.py):
what is read of each table, what never leaves the server, when a day is
written, and that a day is written whole or not at all.
"""

import asyncio
import gzip
import json
import os
import re
import sys
import types
from datetime import date, datetime, timezone

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.admin.errors import InvalidRequest  # noqa: E402
from src.admin.research_export import (  # noqa: E402
    DUE,
    MANIFEST_FILE,
    OFF,
    ORDER_REQUEST_KEPT,
    TABLES,
    UP_TO_DATE,
    WAITING_FOR_CLOSE,
    ResearchExport,
    settings_snapshot,
)
from src.database.postgres import ORDER_REQUEST_COLUMNS  # noqa: E402

CONFIG = {
    "trade": {
        "rules": [
            {"rule_type": "market_hours", "rule_config": {
                "trading_start_hour": 8, "trading_end_hour": 22, "risky_trading_start_hour": 21, "risky_trading_start_minute": 54,
            }},
            {"rule_type": "allowed_indices", "rule_config": {"indices": {"us100": "Nasdaq 100"}}},
        ],
        "config": {
            "general": {"timezone": "Europe/Paris"},
            "signal_polling": {"service_url": "http://web_server1:80", "token": "never-leaves"},
            "watchlist_manager": {"service_url": "http://watchlist1:8081", "api_host": "0.0.0.0", "api_port": 8081},
        },
    },
    "saxo_auth": {"app_secret": "never-leaves-either"},
}


def epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


class FakeQueries:
    """Writes, for each table asked, a header and one line per row it holds; some tables are missing."""

    def __init__(self, rows: dict[str, list[str]], missing: tuple[str, ...] = ()):
        self.rows = rows
        self.missing = missing
        self.asked: list[tuple[str, tuple]] = []

    async def copy_csv(self, query: str, *args, output, timeout=None):
        self.asked.append((query, args))
        table = re.search(r"FROM (\w+)", query).group(1)
        if table in self.missing:
            return False
        await output(b"a,b\n")
        for line in self.rows.get(table, []):
            await output(line.encode() + b"\n")
        return True


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(CONFIG))
    return str(path)


def exporter(tmp_path, config_path, queries=None, now="2026-10-20T03:00:00+02:00") -> ResearchExport:
    return ResearchExport(
        prices_root=str(tmp_path / "prices"), queries=queries or FakeQueries({}), config_path=config_path,
        clock=lambda: epoch(now), version="0.20.0",
    )


def days_root(tmp_path):
    return tmp_path / "prices" / "research" / "days"


# ── what is read ──


def test_each_table_is_read_for_the_local_day_and_the_instruments_whole():
    by_name = {table.name: table for table in TABLES}
    start, end = datetime(2026, 10, 18, 22, tzinfo=timezone.utc), datetime(2026, 10, 19, 22, tzinfo=timezone.utc)

    assert by_name["price_bar_10s"].query(start, end, date(2026, 10, 19)) == (
        "SELECT * FROM price_bar_10s WHERE ts >= $1 AND ts < $2", (start, end),
    )
    assert by_name["price_instrument"].query(start, end, date(2026, 10, 19)) == ("SELECT * FROM price_instrument", ())
    assert by_name["account_balance_history"].query(start, end, date(2026, 10, 19)) == (
        "SELECT * FROM account_balance_history WHERE date_day = $1", (date(2026, 10, 19),),
    )
    assert by_name["signal_journal"].moment == "COALESCE(received_timestamp, decided_at, signal_timestamp)"


def test_what_saxo_was_sent_and_answered_never_leaves_the_server():
    query, _ = {table.name: table for table in TABLES}["order_request"].query(datetime.now(timezone.utc), datetime.now(timezone.utc), date.today())
    assert "request_body" not in query and "response_body" not in query
    # Every other column of the table is kept: the list follows the schema.
    assert ORDER_REQUEST_KEPT == tuple(column for column in ORDER_REQUEST_COLUMNS if column not in ("request_body", "response_body"))
    # The tokens and the trading services' own state are never among the tables, nor what the TA engine
    # measures of itself (the Engine monitor's, kept a week on the server): no research reads it.
    assert not {"tokens", "trade_risk_state", "position_live_value", "ta_engine_tick", "ta_rule_run"} & {table.name for table in TABLES}


def test_the_configuration_written_holds_no_address_of_a_service_and_no_secret():
    snapshot = settings_snapshot(CONFIG)

    text = json.dumps(snapshot)
    assert "never-leaves" not in text and "web_server1" not in text and "watchlist1" not in text
    assert "saxo_auth" not in snapshot["config"]
    assert snapshot["config"]["trade"]["config"]["general"] == {"timezone": "Europe/Paris"}
    assert snapshot["backtest"]["timezone"] == "Europe/Paris" and snapshot["error"] is None


# ── a day ──


def test_a_day_is_written_whole_with_its_manifest_last(tmp_path, config_path):
    queries = FakeQueries({"price_bar_10s": ["1,2", "3,4"], "signal_journal": ["x,y"]}, missing=("ta_signal",))
    export = exporter(tmp_path, config_path, queries)

    manifest = asyncio.run(export.export_day(date(2026, 10, 19)))

    folder = days_root(tmp_path) / "2026-10-19"
    assert sorted(os.listdir(days_root(tmp_path))) == ["2026-10-19"]
    assert manifest["day"] == "2026-10-19" and manifest["timezone"] == "Europe/Paris"
    # The local day: midnight to midnight in Paris.
    assert (manifest["start"], manifest["end"]) == ("2026-10-18T22:00:00Z", "2026-10-19T22:00:00Z")
    assert manifest["tables"]["price_bar_10s"]["lines"] == 2 and manifest["tables"]["price_bar_10s"]["present"]
    assert manifest["tables"]["ta_signal"] == {"present": False}
    assert not (folder / "ta_signal.csv.gz").exists()
    with gzip.open(folder / "price_bar_10s.csv.gz", "rt") as in_file:
        assert in_file.read() == "a,b\n1,2\n3,4\n"
    assert json.loads((folder / MANIFEST_FILE).read_text()) == manifest
    assert "never-leaves" not in (folder / "settings.json").read_text()
    # One query per table, with the day's bounds.
    assert len(queries.asked) == len(TABLES)


def test_the_same_rows_give_the_same_bytes(tmp_path, config_path):
    export = exporter(tmp_path, config_path, FakeQueries({"price_bar_10s": ["1,2"]}))
    first = asyncio.run(export.export_day(date(2026, 10, 19)))["tables"]["price_bar_10s"]["sha256"]
    again = asyncio.run(export.export_day(date(2026, 10, 19)))["tables"]["price_bar_10s"]["sha256"]
    assert first == again


def test_a_day_that_fails_leaves_nothing(tmp_path, config_path):
    class Failing(FakeQueries):
        async def copy_csv(self, query, *args, output, timeout=None):
            if "signal_journal" in query:
                raise RuntimeError("connection lost")
            return await super().copy_csv(query, *args, output=output, timeout=timeout)

    export = exporter(tmp_path, config_path, Failing({}))
    with pytest.raises(RuntimeError):
        asyncio.run(export.export_day(date(2026, 10, 19)))
    assert os.listdir(days_root(tmp_path)) == []
    assert export.written_days() == []


# ── when ──


def test_off_until_switched_on(tmp_path, config_path):
    export = exporter(tmp_path, config_path)
    assert asyncio.run(export.export_once()) == OFF
    assert export.status()["state"] == OFF
    with pytest.raises(InvalidRequest):
        export.update(keep_days=0)
    with pytest.raises(InvalidRequest):
        export.update(keep_days=True)
    assert export.update(enabled=True, keep_days=3)["keep_days"] == 3


def test_the_finished_days_are_written_while_the_market_is_closed(tmp_path, config_path):
    # Tuesday 20 October, 03:00 in Paris: closed.
    export = exporter(tmp_path, config_path)
    export.update(enabled=True, keep_days=3)
    assert [day.isoformat() for day in export.due_days()] == ["2026-10-17", "2026-10-18", "2026-10-19"]
    assert export.status()["state"] == DUE

    assert asyncio.run(export.export_once()) == UP_TO_DATE
    assert [day.isoformat() for day in export.written_days()] == ["2026-10-17", "2026-10-18", "2026-10-19"]
    status = export.status()
    assert (status["state"], status["days"], status["first"], status["last"]) == (UP_TO_DATE, 3, "2026-10-17", "2026-10-19")


def test_nothing_is_written_while_the_market_is_open(tmp_path, config_path):
    # Tuesday 20 October, 10:00 in Paris.
    export = exporter(tmp_path, config_path, now="2026-10-20T10:00:00+02:00")
    export.update(enabled=True, keep_days=2)
    assert asyncio.run(export.export_once()) == WAITING_FOR_CLOSE
    assert export.written_days() == []
    assert export.status()["state"] == WAITING_FOR_CLOSE


def test_hours_written_in_new_yorks_time_are_read_in_it_while_the_days_stay_those_of_paris(tmp_path):
    config = json.loads(json.dumps(CONFIG))
    config["trade"]["rules"][0]["rule_config"].update(
        timezone="America/New_York", trading_start_hour=2, trading_end_hour=16, risky_trading_start_hour=15,
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))

    # Tuesday 20 October: 21:30 in Paris is 15:30 in New York, open; 22:30 is 16:30, closed.
    assert exporter(tmp_path, str(path), now="2026-10-20T21:30:00+02:00")._market_is_open(epoch("2026-10-20T21:30:00+02:00"))
    late = exporter(tmp_path, str(path), now="2026-10-20T22:30:00+02:00")
    assert not late._market_is_open(epoch("2026-10-20T22:30:00+02:00"))
    late.update(enabled=True, keep_days=2)
    # Still Tuesday in Paris: Sunday and Monday are the finished days, as with the hours of Paris.
    assert [day.isoformat() for day in late.due_days()] == ["2026-10-18", "2026-10-19"]


def test_the_days_older_than_those_kept_go(tmp_path, config_path):
    export = exporter(tmp_path, config_path)
    export.update(enabled=True, keep_days=5)
    asyncio.run(export.export_once())
    (days_root(tmp_path) / "2026-10-19.part").mkdir()

    export.update(keep_days=2)
    removed = export.prune()

    assert [day.isoformat() for day in removed] == ["2026-10-15", "2026-10-16", "2026-10-17"]
    assert sorted(os.listdir(days_root(tmp_path))) == ["2026-10-18", "2026-10-19"]
