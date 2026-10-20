"""
A day from the server's database to the lab's, for real: two PostgreSQL 16 of
their own (pgserver), the server's day written by the admin's export through
a read-only connection (src/admin/research_export.py), loaded by the lab
(lab/db.py), then read back by the backtest's own reader (DatabaseData).

Skipped unless pgserver and the real asyncpg are there (the lab's
environment, ``.venv-lab``), and in a run where another file has put a
stand-in in asyncpg's place: run it on its own.
"""

import asyncio
import gzip
import json
from datetime import date, datetime, timedelta, timezone

import pytest

pgserver = pytest.importorskip("pgserver")
import asyncpg  # noqa: E402

if not hasattr(asyncpg, "connect"):
    pytest.skip("asyncpg is a stand-in in this run of the tests", allow_module_level=True)

from lab import db as lab_db  # noqa: E402
from src.admin.queries import AdminQueries, LazyDatabase  # noqa: E402
from src.admin.research_export import ResearchExport  # noqa: E402
from src.backtest.clock import TradingClock  # noqa: E402
from src.backtest.data import DatabaseData  # noqa: E402
from src.database.postgres import AsyncDbPriceHistoryManager, PostgresConnectionManager  # noqa: E402

DAY = date(2026, 10, 14)
SEVEN = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)


@pytest.fixture
def databases(tmp_path):
    server = pgserver.get_server(tmp_path / "server-pg", cleanup_mode="delete")
    lab = pgserver.get_server(tmp_path / "lab-pg", cleanup_mode="delete")
    yield server.get_uri(), lab.get_uri()
    server.cleanup()
    lab.cleanup()


async def fill_the_server(dsn: str) -> dict:
    """A day of the recorder, the journal, a position and an order request with its bodies."""
    connection = await asyncpg.connect(dsn)
    try:
        await lab_db.ensure_schema(connection)
        await connection.execute(
            "INSERT INTO signal_journal (signal_uuid, action, indice, received_timestamp, decided_at, outcome) "
            "VALUES ('s-1', 'long', 'us100', $1, $1, 'executed'), ('s-0', 'long', 'us100', $2, $2, 'refused')",
            SEVEN, SEVEN - timedelta(days=1),
        )
        await connection.execute(
            "INSERT INTO turbo_data_position (action, position_id, position_amount, position_open_price, position_status, "
            "execution_time_open, instrument_uic) VALUES ('long', 'p-1', 100, 5.05, 'Closed', $1, 40000001)",
            SEVEN + timedelta(seconds=4),
        )
        await connection.execute(
            "INSERT INTO order_request (request_id, requested_at, operation, purpose, outcome, request_body, response_body) "
            "VALUES ('r-1', $1, 'place', 'open', 'accepted', '{\"secret\": 1}', '{\"OrderId\": \"1\"}')",
            SEVEN + timedelta(seconds=2),
        )
        await connection.execute("INSERT INTO account_balance_history (date_day, total_value, recorded_at) VALUES ($1, 10250, $2)", DAY, SEVEN)
    finally:
        await connection.close()
    writer = PostgresConnectionManager(dsn=dsn, min_size=1, max_size=1)
    await writer.connect()
    try:
        history = AsyncDbPriceHistoryManager(writer)
        await history.ensure_partitions(date(2026, 10, 1))
        turbo = {
            "uic": 40000001, "asset_type": "WarrantOpenEndKnockOut", "role": "turbo", "indice": "us100", "symbol": "S1",
            "description": "NASDAQ 100 Turbo long 24800 SG", "currency": "EUR", "direction": "long", "barrier": 24800.0, "issuer": "SG",
        }
        turbo_id = await history.upsert_instrument(turbo, SEVEN)
        bar = lambda ts, bid: (ts, turbo_id, bid, bid + 0.1, bid - 0.1, bid, bid + 0.05, bid + 0.05, bid + 0.05, bid + 0.05, 1, 0)  # noqa: E731
        await history.insert_bars([bar(SEVEN, 5.0), bar(SEVEN + timedelta(seconds=10), 5.1), bar(SEVEN - timedelta(days=1), 4.0)])
        await history.record_coverage(SEVEN, True, 1, 1)
        await history.record_coverage(SEVEN + timedelta(seconds=10), True, 1, 1)
        return {"turbo_id": turbo_id, "bar": bar, "history": history, "writer": writer}
    except BaseException:
        await writer.close()
        raise


def export(tmp_path, dsn: str, config_path: str):
    reader = PostgresConnectionManager(dsn=dsn, min_size=1, max_size=1, server_settings={"default_transaction_read_only": "on"})
    exporter = ResearchExport(
        prices_root=str(tmp_path / "prices"), queries=AdminQueries(LazyDatabase(reader), "Europe/Paris"),
        config_path=config_path, version="test",
    )
    return exporter, reader


async def read_back(dsn: str):
    reader = PostgresConnectionManager(dsn=dsn, min_size=1, max_size=1, server_settings={"default_transaction_read_only": "on"})
    await reader.connect()
    try:
        start, end = TradingClock("Europe/Paris").day_bounds(DAY)
        recorded = await DatabaseData(reader).day("us100", DAY, start, end)
        journal = await reader.fetch("SELECT signal_uuid FROM signal_journal ORDER BY signal_uuid")
        requests = await reader.fetch("SELECT request_id, request_body, response_body FROM order_request")
        balance = await reader.fetchval("SELECT total_value FROM account_balance_history")
        return recorded, [row["signal_uuid"] for row in journal], [dict(row) for row in requests], balance
    finally:
        await reader.close()


def test_a_day_goes_from_the_servers_database_to_the_labs_and_is_read_as_a_backtest_reads_it(tmp_path, databases):
    server_dsn, lab_dsn = databases
    config_path = "tests/test_config.json"

    async def scenario():
        filled = await fill_the_server(server_dsn)
        exporter, reader = export(tmp_path, server_dsn, config_path)
        try:
            manifest = await exporter.export_day(DAY)
            first_load = await lab_db.load(database_url=lab_dsn, root=tmp_path / "prices" / "research" / "days", report=lambda _: None)
            unchanged = await lab_db.load(database_url=lab_dsn, root=tmp_path / "prices" / "research" / "days", report=lambda _: None)
            first_read = await read_back(lab_dsn)
            # The server records one more bar of that day, and the day is written again.
            await filled["history"].insert_bars([filled["bar"](SEVEN + timedelta(seconds=20), 5.2)])
            await exporter.export_day(DAY)
            second_load = await lab_db.load(database_url=lab_dsn, root=tmp_path / "prices" / "research" / "days", report=lambda _: None)
            second_read = await read_back(lab_dsn)
            status = await lab_db.status(database_url=lab_dsn)
            return manifest, first_load, unchanged, first_read, second_load, second_read, status
        finally:
            await reader.close()
            await filled["writer"].close()

    manifest, first_load, unchanged, first_read, second_load, second_read, status = asyncio.run(scenario())

    # Only the day's rows were written; the instruments whole.
    assert manifest["tables"]["price_bar_10s"]["lines"] == 2
    assert manifest["tables"]["signal_journal"]["lines"] == 1
    assert manifest["tables"]["price_instrument"]["lines"] == 1
    folder = tmp_path / "prices" / "research" / "days" / DAY.isoformat()
    with gzip.open(folder / "order_request.csv.gz", "rt") as in_file:
        written = in_file.read()
    assert "request_body" not in written and "secret" not in written and "OrderId" not in written
    assert "dsn" not in (folder / "settings.json").read_text() and json.loads((folder / "settings.json").read_text())["backtest"]

    assert [entry["day"] for entry in first_load] == [DAY.isoformat()] and unchanged == []
    recorded, journal, requests, balance = first_read
    (series,) = recorded.turbos["long"]
    first = int(SEVEN.timestamp())
    assert list(series.ts) == [first, first + 10]
    assert journal == ["s-1"]
    assert requests == [{"request_id": "r-1", "request_body": None, "response_body": None}]
    assert balance == 10250

    # The day written again replaces the one loaded: the new bar is there, nothing twice.
    assert [entry["day"] for entry in second_load] == [DAY.isoformat()]
    (series,) = second_read[0].turbos["long"]
    assert list(series.ts) == [first, first + 10, first + 20]
    assert status["days"] == 1 and status["first"] == DAY.isoformat()
