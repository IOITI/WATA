"""
The bot's commands (src/mq_telegram/commands.py, updates.py): what they answer,
to whom, and that they can only read. Telegram's API and PostgreSQL are fakes
here; tests/test_infrastructure_live.py runs the statements on a real schema.
"""

import ast
import json
import logging
import pathlib
import re
import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from src.mq_telegram import commands as commands_module
from src.mq_telegram import updates
from src.mq_telegram.commands import (
    HELP_TEXT, STATEMENTS, Calendars, Commands, CommandSettings, Reader, command_of,
)
from src.mq_telegram.updates import (
    ANSWERS_PER_MINUTE, FAILURE_WAITS, MAX_ANSWER_CHARS, WRONG_BOT_WAIT_SECONDS, CommandPoller, start_commands,
)
from src.trade.macro_calendar import Calendar, MacroEvent, NewsRule
from src.trade.market_calendar import CLOSED, MarketCalendar, MarketDay

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
CHAT = 4242
# Monday 9 November 2026, 14:32 in Paris.
NOW = datetime(2026, 11, 9, 13, 32, tzinfo=timezone.utc)
POSITION_ID = "5023456789"


def at(minutes: float) -> datetime:
    return NOW + timedelta(minutes=minutes)


# ── the boundary ──

COMMAND_FILES = ("src/mq_telegram/commands.py", "src/mq_telegram/updates.py")
# What they may import of WATA's own: pure modules that read files or say things, nothing that writes or trades.
ALLOWED = (
    "src.mq_telegram.commands", "src.heartbeat", "src.logging_helper.redaction", "src.trade.day_breakers",
    "src.trade.macro_calendar", "src.trade.market_calendar",
)
# Nothing that sends to a queue.
FORBIDDEN_LIBRARIES = ("pika", "aio_pika", "aiormq")


def _imports(path: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", COMMAND_FILES)
def test_the_commands_import_nothing_that_trades_writes_or_sends_to_a_queue(path):
    imported = _imports(path)

    assert sorted(name for name in imported if name.startswith("src") and name not in ALLOWED) == []
    assert sorted(name for name in imported if name.split(".")[0] in FORBIDDEN_LIBRARIES) == []


@pytest.mark.parametrize("module", [name for name in ALLOWED if name != "src.mq_telegram.commands"])
def test_what_they_import_imports_nothing_else_of_wata(module):
    path = module.replace(".", "/") + ".py"

    assert sorted(name for name in _imports(path) if name.startswith("src") and name not in ALLOWED) == []


# Words of a statement that writes, takes a lock or changes the session (FOR UPDATE and FOR SHARE included).
WRITES = {"INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER", "DROP", "TRUNCATE", "GRANT", "REVOKE", "COPY",
          "LOCK", "SHARE", "VACUUM", "SET", "SET_CONFIG", "NEXTVAL", "SETVAL", "NOTIFY", "CALL", "DO", "INTO"}


@pytest.mark.parametrize("sql", STATEMENTS)
def test_every_statement_is_a_select(sql):
    words = re.findall(r"[A-Z_]+", sql.upper())

    assert words[0] == "SELECT"
    assert sorted(word for word in words if word in WRITES or word.startswith("PG_ADVISORY")) == []
    assert ";" not in sql


def test_the_reader_runs_no_statement_of_its_own():
    with pytest.raises(ValueError):
        import asyncio

        asyncio.run(Reader(Connection({})).rows("DELETE FROM turbo_data_position"))


def test_the_connection_is_opened_read_only_for_every_transaction(monkeypatch):
    opened = []

    async def connect(dsn, **kwargs):
        opened.append((dsn, kwargs))
        return Connection({})

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    commands = Commands(SETTINGS, calendars=Calendars(), now=lambda: NOW)

    commands.answer("/signals")

    dsn, kwargs = opened[0]
    assert dsn == SETTINGS.dsn
    assert kwargs["server_settings"] == {"default_transaction_read_only": "on"}
    assert kwargs["timeout"] and kwargs["command_timeout"]


def test_the_poller_asks_telegram_for_nothing_but_its_updates_and_to_send():
    post = Telegram([[message("/status"), message("/help"), message("/positions"), message("/day"), message("/signals"),
                      message("/nothing")]])
    poller = CommandPoller(TOKEN, CHAT, lambda command: f"answer to {command}", post=post, now=lambda: NOW.timestamp())

    poller.poll_once()

    assert {url.rsplit("/", 1)[1] for url, _ in post.calls} == {"getUpdates", "sendMessage"}


# ── the fakes ──

class UndefinedTableError(Exception):
    """As asyncpg names a table that does not exist."""


class Connection:
    """PostgreSQL as a test scripts it: the rows of each statement, what was run kept."""

    def __init__(self, rows: dict):
        self.rows = rows
        self.ran = []
        self.closed = False

    async def fetch(self, sql, *args):
        self.ran.append((sql, args))
        found = self.rows.get(sql, [])
        if isinstance(found, Exception):
            raise found
        return found

    async def close(self):
        self.closed = True


SETTINGS = CommandSettings(
    dsn="postgresql://wata:secret@postgres1:5432/wata", timezone="Europe/Paris", saxo_environment="live",
    capital_feed=True, price_recorder=True, ta_engine=True,
    news_rule=NewsRule(events=("CPI",), minutes_before=5, minutes_after=15), max_trades_per_day=8,
)

CPI = MacroEvent(date(2026, 11, 13), "08:30", "CPI", "2026-10")
NFP = MacroEvent(date(2026, 11, 10), "08:30", "NFP", "2026-10")
CALENDARS = Calendars(
    macro=Calendar(by_day={CPI.day: [CPI], NFP.day: [NFP]}, digest="x", until={"CPI": CPI.day, "NFP": NFP.day}),
    market=MarketCalendar(by_day={}, digest="x", until=date(2027, 12, 31)),
)


def beat(minutes_ago: float, **figures) -> dict:
    return {"beat_at": at(-minutes_ago), "version": "0.50.0", "figures": json.dumps(figures)}


def open_position(**more) -> dict:
    return {
        "position_id": POSITION_ID, "action": "long", "instrument_name": "Turbo Long NASDAQ 100 23500 BNP",
        "position_amount": 220, "position_open_price": 4.52, "execution_time_open": at(-30),
        "position_max_performance_percent": 2.4, "bid": 4.61, "performance_percent": 1.99, "observed_at": at(-0.05),
        **more,
    }


def database(**changes) -> dict:
    rows = {
        commands_module.HEARTBEATS_SQL: [
            {"service": "trader", **beat(0.1, last_signal_at=at(-30).isoformat())},
            {"service": "position_monitor", **beat(0.2, stream_connected=True, positions_listed=1)},
        ],
        commands_module.RISK_STATE_SQL: [],
        commands_module.SAXO_LOGIN_SQL: [{"refresh_expires_at": at(60 * 24 * 7), "needs_authorization_since": None}],
        commands_module.CAPITAL_FEED_SQL: [{"updated_at": at(-0.1), "state": "streaming"}],
        commands_module.RECORDER_SQL: [{"ts": at(-0.2), "bars": 42}],
        commands_module.TA_ENGINE_SQL: [{"updated_at": at(-0.1), "state": "running"}],
        commands_module.OPEN_POSITIONS_SQL: [open_position()],
        commands_module.WORST_SQL: [{"position_id": POSITION_ID, "worst_percent": -0.8}],
        commands_module.CLOSED_TODAY_SQL: [
            {"action": "long", "execution_time_open": at(-300), "execution_time_close": at(-290),
             "position_total_performance_percent": 0.6, "position_profit_loss": 1.2,
             "position_close_reason": "Take profit reached at 0.6 %"},
            {"action": "short", "execution_time_open": at(-200), "execution_time_close": at(-180),
             "position_total_performance_percent": -1.2, "position_profit_loss": -2.5,
             "position_close_reason": "Stop loss hit at -1.20 %"},
        ],
        commands_module.OPENED_TODAY_SQL: [{"opened": 3}],
        commands_module.SIGNALS_SQL: [
            {"action": "long", "indice": "us100", "confidence": 0.85, "received_timestamp": at(-30),
             "decided_at": at(-30), "outcome": "executed", "reason": None},
            {"action": "short", "indice": "us100", "confidence": None, "received_timestamp": at(-60),
             "decided_at": at(-60), "outcome": "refused",
             "reason": "Breaking trading rule: no entry around CPI (14:30) from 14:25 to 14:45."},
            {"action": "long", "indice": "us100", "confidence": None, "received_timestamp": at(-90),
             "decided_at": at(-90), "outcome": "failed",
             "reason": "OpenAPIError: order 5005123456 refused for AccountKey=Ab3dEfGh1jKlMn0pQ"},
        ],
    }
    rows.update(changes)
    return rows


def answer(command: str, rows: dict | None = None, settings: CommandSettings = SETTINGS,
           calendars: Calendars = CALENDARS) -> tuple[str, Connection]:
    connection = Connection(database() if rows is None else rows)

    async def connect(dsn):
        return connection

    return Commands(settings, calendars=calendars, connect=connect, now=lambda: NOW).answer(command), connection


# ── what is answered ──

def test_status_says_the_services_the_pause_the_login_the_prices_the_positions_today_and_the_next_release():
    said, connection = answer("/status")

    assert said.splitlines() == [
        "WATA at 14:32 (Europe/Paris)",
        "Trader: running (v0.50.0), last signal 14:02",
        "Position monitor: streaming, 1 position at Saxo",
        "Entries: taken (not paused)",
        "Saxo login: renewable until Mon 16 Nov 14:32",
        "Capital.com feed: streaming, 6 s ago",
        "Price recorder: recording 42 instruments, 12 s ago",
        "TA engine: running, 6 s ago",
        "Positions: 1 open, live +1.99 %",
        "Today: 2 trades closed, −0.61 % compounded, −1.30 €",
        "Next US release: CPI Fri 13 Nov 14:30, no entry from 14:25 to 14:45",
    ]
    assert connection.closed
    assert all(sql in STATEMENTS for sql, _ in connection.ran)


def test_status_says_what_is_wrong_in_capitals():
    rows = database(**{
        commands_module.HEARTBEATS_SQL: [
            {"service": "trader", **beat(5)},
            {"service": "position_monitor", **beat(0.1, stream_connected=True, close_intents=[
                {"position_id": POSITION_ID, "since": at(-3).isoformat(), "attempts": 3, "last_error": f"pos {POSITION_ID}"},
            ])},
        ],
        commands_module.RISK_STATE_SQL: [
            {"key": "entries_paused_since", "value_time": at(-60 * 24 * 5)},
            {"key": "entries_paused_by_account_guard", "value_time": at(-60 * 24 * 5)},
        ],
        commands_module.SAXO_LOGIN_SQL: [{"refresh_expires_at": at(-10), "needs_authorization_since": at(-12)}],
        commands_module.CAPITAL_FEED_SQL: [{"updated_at": at(-12), "state": "streaming"}],
    })

    said, _ = answer("/status", rows)

    assert "Trader: DOWN, last heartbeat 5 min ago" in said
    assert "Position monitor: WARNING, selling a position since 14:29 (3 failed attempts)" in said
    assert "Entries: PAUSED by the account guard since Wed 4 Nov 14:32" in said
    assert "Saxo login: NEEDS AUTHORISING since 14:20 (the admin's Saxo page)" in said
    assert "Capital.com feed: SILENT, last sign 12 min ago" in said
    assert POSITION_ID not in said


def test_status_without_a_news_rule_names_the_next_release_and_that_no_window_is_kept():
    settings = CommandSettings(dsn="x", news_rule=None)

    said, _ = answer("/status", settings=settings)

    assert said.splitlines()[-1] == "Next US release: NFP Tue 10 Nov 14:30 (no window kept: no macro_news rule)"
    # The feeds that are switched off are not named.
    assert "Capital.com feed" not in said and "Price recorder" not in said and "TA engine" not in said


def test_status_says_a_release_whose_window_is_open_and_a_us_holiday():
    calendars = Calendars(
        macro=CALENDARS.macro,
        market=MarketCalendar(by_day={date(2026, 11, 13): MarketDay(date(2026, 11, 13), CLOSED, None, "A holiday")},
                              digest="x", until=date(2027, 12, 31)),
    )
    during = datetime(2026, 11, 13, 13, 27, tzinfo=timezone.utc)
    connection = Connection(database())

    async def connect(dsn):
        return connection

    said = Commands(SETTINGS, calendars=calendars, connect=connect, now=lambda: during).answer("/status")

    assert "US market: CLOSED today (A holiday)" in said
    assert said.splitlines()[-1] == "Next US release: CPI 14:30, IN ITS WINDOW NOW: no entry from 14:25 to 14:45"


def test_positions_says_each_open_position_without_its_id():
    said, _ = answer("/positions")

    assert said == (
        "1 open position at 14:32 (Europe/Paris)\n\n"
        "Long · Turbo Long NASDAQ 100 23500 BNP\n"
        "Bought 14:02: 220 at 4.52 €\n"
        "Now 4.61 € (+1.99 %), 3 s ago\n"
        "Best +2.40 %, worst −0.80 %"
    )


def test_a_position_without_a_live_value_or_a_worst_point_says_so():
    rows = database(**{
        commands_module.OPEN_POSITIONS_SQL: [open_position(bid=None, performance_percent=None, observed_at=None)],
        commands_module.WORST_SQL: UndefinedTableError("relation \"position_worst\" does not exist"),
    })

    said, _ = answer("/positions", rows)

    assert "No live value yet" in said and "worst not recorded" in said


def test_an_old_live_value_is_said_old_and_no_position_is_said_plainly():
    rows = database(**{commands_module.OPEN_POSITIONS_SQL: [open_position(observed_at=at(-5))]})

    assert "5 min ago (OLD)" in answer("/positions", rows)[0]
    assert answer("/positions", database(**{commands_module.OPEN_POSITIONS_SQL: []}))[0] == \
        "No open position at 14:32 (Europe/Paris)"


def test_day_lists_the_trades_closed_today_the_open_ones_and_the_count():
    said, connection = answer("/day")

    assert said.splitlines() == [
        "Today, Mon 9 Nov (Europe/Paris)",
        "Today: 2 trades closed, −0.61 % compounded, −1.30 €",
        "09:32 → 09:42  Long  +0.60 %  Take profit reached at 0.6 %",
        "11:12 → 11:32  Short  −1.20 %  Stop loss hit at -1.20 %",
        "Open: Long since 14:02, live +1.99 %",
        "Positions opened today: 3 of 8 a day at most",
    ]
    # Today is the trading timezone's day, whatever the database's.
    closed_args = next(args for sql, args in connection.ran if sql == commands_module.CLOSED_TODAY_SQL)
    assert closed_args == (datetime(2026, 11, 8, 23, 0, tzinfo=timezone.utc), datetime(2026, 11, 9, 23, 0, tzinfo=timezone.utc))


def test_signals_says_the_last_ones_with_what_became_of_them_and_no_id_or_key():
    said, _ = answer("/signals")

    lines = said.splitlines()
    assert lines[:3] == [
        "Last 3 signals (Europe/Paris)",
        "14:02:00 long us100, confidence 0.85: executed",
        "13:32:00 short us100: refused, no entry around CPI (14:30) from 14:25 to 14:45.",
    ]
    assert lines[3].startswith("13:02:00 long us100: failed, OpenAPIError: order … refused for AccountKey=")
    assert "5005123456" not in said and "Ab3dEfGh1jKlMn0pQ" not in said


def test_no_answer_holds_an_id_a_token_or_the_chat():
    for command in ("/status", "/positions", "/day", "/signals"):
        said, _ = answer(command)

        assert POSITION_ID not in said and TOKEN not in said and str(CHAT) not in said and "secret" not in said, command


def test_help_start_and_an_unknown_command_need_no_database():
    def refuse(dsn):
        raise AssertionError("the database was opened")

    commands = Commands(SETTINGS, calendars=CALENDARS, connect=refuse, now=lambda: NOW)

    assert commands.answer("/help") == HELP_TEXT == commands.answer("/start")
    assert commands.answer("/close").startswith("Unknown command /close.\n\nWATA answers these commands")


def test_a_database_that_cannot_be_read_is_said_without_details():
    async def unreachable(dsn):
        raise OSError(f"could not connect to {dsn}")

    said = Commands(SETTINGS, calendars=CALENDARS, connect=unreachable, now=lambda: NOW).answer("/status")

    assert said == "Could not read WATA's database (OSError): nothing was answered."


@pytest.mark.parametrize("text, command", [
    ("/status", "/status"), ("/Status@wata_bot extra", "/status"), ("  /day", "/day"), ("status", None), ("", None),
])
def test_a_command_is_its_first_word_without_the_bots_name(text, command):
    assert command_of(text) == command


def test_the_settings_are_read_from_the_configuration():
    values = {
        "postgresql.dsn": "postgresql://x", "trade.config.general.timezone": "America/New_York", "saxo_auth.env": "simulation",
        "trade.config.capital_feed.enabled": True, "trade.config.price_recorder.enabled": "yes",
        "trade.rules": [
            {"rule_type": "macro_news", "rule_config": {"events": ["CPI", "FOMC"], "minutes_before": 10}},
            {"rule_type": "risk_management", "rule_config": {"max_trades_per_day": 6}},
        ],
    }
    config = types.SimpleNamespace(get_config_value=lambda path, default=None: values.get(path, default))

    settings = CommandSettings.from_config(config)

    assert (settings.timezone, settings.saxo_environment, settings.capital_feed, settings.price_recorder) == (
        "America/New_York", "simulation", True, False)
    assert settings.news_rule == NewsRule(events=("FOMC", "CPI"), minutes_before=10)
    assert settings.max_trades_per_day == 6 and not settings.ta_engine


# ── to whom, how often ──

class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def message(text: str, chat: int = CHAT, sent_at: datetime = NOW) -> dict:
    return {"message_id": 1, "chat": {"id": chat, "type": "private"}, "date": int(sent_at.timestamp()), "text": text}


class Telegram:
    """The Bot API as the test scripts it: getUpdates gives each batch in turn, sendMessage takes everything."""

    def __init__(self, batches, refusal=None):
        self.batches = list(batches)
        self.refusal = refusal
        self.calls = []
        self.sent = []
        self._next_id = 100

    def __call__(self, url, data=None, headers=None, timeout=None):
        payload = json.loads(data)
        self.calls.append((url, payload))
        if url.endswith("/sendMessage"):
            self.sent.append(payload)
            return Response(200, {"ok": True})
        if self.refusal is not None:
            if isinstance(self.refusal, Exception):
                raise self.refusal
            return Response(*self.refusal)
        updates = []
        for item in self.batches.pop(0) if self.batches else []:
            self._next_id += 1
            updates.append({"update_id": self._next_id, "message": item})
        return Response(200, {"ok": True, "result": updates})


def poller(post, now=NOW.timestamp(), answer=lambda command: f"answer to {command}"):
    waits = []
    clock = {"now": now}
    found = CommandPoller(TOKEN, str(CHAT), answer, post=post, now=lambda: clock["now"], sleep=waits.append)
    return found, waits, clock


def test_only_the_bots_own_chat_is_answered_and_every_update_is_read_past():
    post = Telegram([[message("/status", chat=999), message("/day")], []])
    found, _, _ = poller(post)

    found.poll_once()
    found.poll_once()

    assert [sent["text"] for sent in post.sent] == ["answer to /day"]
    assert post.sent[0]["chat_id"] == str(CHAT)
    # The next getUpdates says both were read, the stranger's too.
    assert post.calls[-1][1]["offset"] == 103
    assert post.calls[0][1]["allowed_updates"] == ["message"]


def test_plain_text_and_a_command_sent_while_the_service_was_stopped_are_not_answered():
    post = Telegram([[message("hello"), message("/status", sent_at=NOW - timedelta(minutes=10))]])
    found, _, _ = poller(post)

    found.poll_once()

    assert post.sent == []


def test_over_six_commands_a_minute_one_message_says_so_and_the_rest_wait():
    post = Telegram([[message("/status")] * (ANSWERS_PER_MINUTE + 3), [message("/day")]])
    found, _, clock = poller(post)

    found.poll_once()
    clock["now"] += 61
    found.poll_once()

    texts = [sent["text"] for sent in post.sent]
    assert texts[:ANSWERS_PER_MINUTE] == ["answer to /status"] * ANSWERS_PER_MINUTE
    assert texts[ANSWERS_PER_MINUTE] == "Too many commands: the next ones are answered again in 60 s."
    assert texts[ANSWERS_PER_MINUTE + 1:] == ["answer to /day"]


def test_an_answer_too_long_for_telegram_is_cut():
    post = Telegram([[message("/day")]])
    found, _, _ = poller(post, answer=lambda command: "x" * 5000)

    found.poll_once()

    assert len(post.sent[0]["text"]) == MAX_ANSWER_CHARS and post.sent[0]["text"].endswith("…")


@pytest.mark.parametrize("refusal, wait", [
    ((409, {"ok": False, "description": "Conflict: terminated by other getUpdates request"}), FAILURE_WAITS[-1]),
    ((401, {"ok": False, "description": "Unauthorized"}), WRONG_BOT_WAIT_SECONDS),
    ((429, {"ok": False, "parameters": {"retry_after": 7}}), 7.0),
    ((502, {"ok": False}), FAILURE_WAITS[0]),
    (requests.ConnectionError(f"Max retries exceeded with url: /bot{TOKEN}/getUpdates"), FAILURE_WAITS[0]),
])
def test_a_failure_waits_and_is_said_without_the_token(refusal, wait, caplog):
    found, waits, _ = poller(Telegram([], refusal=refusal))

    with caplog.at_level(logging.INFO):
        found.poll_once()

    assert waits == [wait]
    assert TOKEN not in caplog.text


def test_failures_wait_longer_each_time_and_a_failure_is_said_once_in_a_while(caplog):
    found, waits, _ = poller(Telegram([], refusal=(500, {"ok": False, "description": "Internal"})))

    with caplog.at_level(logging.WARNING):
        for _ in range(4):
            found.poll_once()

    assert waits == [FAILURE_WAITS[0], FAILURE_WAITS[1], FAILURE_WAITS[2], FAILURE_WAITS[2]]
    assert caplog.text.count("Telegram refused getUpdates") == 1


def test_the_service_starts_the_poller_in_a_thread_of_its_own(monkeypatch):
    started = {}

    class Thread:
        def __init__(self, target, name, daemon):
            started.update(target=target, name=name, daemon=daemon)

        def start(self):
            started["running"] = True

    values = {"postgresql.dsn": "postgresql://x", "trade.rules": []}
    config = types.SimpleNamespace(get_config_value=lambda path, default=None: values.get(path, default))
    monkeypatch.setattr(updates.threading, "Thread", Thread)

    start_commands(config, TOKEN, CHAT)

    assert started["name"] == "telegram-commands" and started["daemon"] and started["running"]
    assert started["target"].__self__._chat_id == str(CHAT)


def test_the_service_reads_the_commands_only_when_switched_on():
    source = pathlib.Path("src/mq_telegram/__init__.py").read_text(encoding="utf-8")

    assert 'if config_manager.get_config_value("reporting.telegram_commands", False) is True:' in source
    assert source.index("start_commands(") > source.index('"reporting.telegram_commands"')
