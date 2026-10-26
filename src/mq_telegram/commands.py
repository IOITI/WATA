"""
What WATA's bot answers when the operator asks it (``reporting.telegram_commands``,
off unless true): /status, /positions, /day, /signals and /help.

It only reads. Each command opens one connection of its own, read-only
(``default_transaction_read_only``), runs SELECTs of the tables the services
write, and reads the calendars of ``src/trade/data``. Nothing here pauses,
resumes, closes, restarts, saves a setting or switches a rule, and nothing is
published to a queue: ``tests/test_telegram_commands.py`` holds what this file
and ``updates.py`` may import, and that every statement is a SELECT. An answer
names no account key, token, chat or id: the reasons the services wrote are
cleaned as log lines are, and long numbers are taken out.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytz

from src.heartbeat import HEARTBEAT_STALE_SECONDS
from src.logging_helper.redaction import redact
from src.trade.day_breakers import best_and_now
from src.trade.macro_calendar import DEFAULT_EVENTS, Calendar, NewsRule, read_calendar
from src.trade.market_calendar import MarketCalendar, new_york_day, read_market_calendar

STATUS, POSITIONS, DAY, SIGNALS, HELP = "/status", "/positions", "/day", "/signals", "/help"
COMMANDS = (STATUS, POSITIONS, DAY, SIGNALS, HELP)
# What a Telegram client sends when the chat is first opened.
START = "/start"

HELP_TEXT = (
    "WATA answers these commands, and only reads: nothing here pauses, resumes, closes, restarts or changes "
    "anything.\n"
    "/status: services, pause, Saxo login, prices, positions, today, next US release\n"
    "/positions: open positions and their live value\n"
    "/day: today's trades\n"
    "/signals: the last 5 signals and what became of them\n"
    "/help: this list"
)

# A connection is opened at each command, and a command never waits long for the database.
CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_SECONDS = 10
READ_ONLY = {"default_transaction_read_only": "on"}
LAST_SIGNALS = 5
# The recorder writes a line of coverage every ten seconds, the feed its state as often (every minute while
# idle), the engine at each tick: the admin takes them for stopped past these (src/admin/health.py, data.py).
RECORDER_SILENT_AFTER_SECONDS = 60 + 10
FEED_SILENT_AFTER_SECONDS = 90
TA_ENGINE_SILENT_AFTER_SECONDS = 180
# A live value older than this is said with its age: the monitor writes one every few seconds.
LIVE_VALUE_FRESH_SECONDS = 60
# How far ahead the next US release is looked for.
NEXT_RELEASE_DAYS = 45
REASON_CHARS = 140
NAME_CHARS = 60
REFUSAL_PREFIX = "Breaking trading rule: "

HEARTBEATS_SQL = "SELECT service, beat_at, version, figures FROM service_heartbeat"
RISK_STATE_SQL = "SELECT key, value_time FROM trade_risk_state WHERE key = ANY($1::text[])"
SAXO_LOGIN_SQL = (
    "SELECT refresh_expires_at, needs_authorization_since FROM saxo_token WHERE environment = $1"
)
CAPITAL_FEED_SQL = "SELECT updated_at, state FROM capital_feed_state WHERE id = 1"
RECORDER_SQL = "SELECT ts, bars FROM price_recorder_coverage ORDER BY ts DESC LIMIT 1"
TA_ENGINE_SQL = "SELECT updated_at, state FROM ta_engine_state WHERE id = 1"
OPEN_POSITIONS_SQL = """
SELECT p.position_id, p.action, p.instrument_name, p.position_amount, p.position_open_price,
       p.execution_time_open, p.position_max_performance_percent,
       v.bid, v.performance_percent, v.observed_at
FROM turbo_data_position p
LEFT JOIN position_live_value v ON v.position_id = p.position_id
WHERE p.position_status = 'Open'
ORDER BY p.execution_time_open, p.position_id
"""
WORST_SQL = "SELECT position_id, worst_percent FROM position_worst WHERE position_id = ANY($1::text[])"
CLOSED_TODAY_SQL = """
SELECT action, execution_time_open, execution_time_close, position_total_performance_percent,
       position_profit_loss, position_close_reason
FROM turbo_data_position
WHERE position_status = 'Closed' AND execution_time_close >= $1 AND execution_time_close < $2
ORDER BY execution_time_close, position_id
"""
OPENED_TODAY_SQL = (
    "SELECT COUNT(*) AS opened FROM turbo_data_position WHERE execution_time_open >= $1 AND execution_time_open < $2"
)
SIGNALS_SQL = """
SELECT action, indice, confidence, received_timestamp, decided_at, outcome, reason
FROM signal_journal
WHERE decided_at IS NOT NULL
ORDER BY decided_at DESC
LIMIT $1
"""
STATEMENTS = (
    HEARTBEATS_SQL, RISK_STATE_SQL, SAXO_LOGIN_SQL, CAPITAL_FEED_SQL, RECORDER_SQL, TA_ENGINE_SQL, OPEN_POSITIONS_SQL,
    WORST_SQL, CLOSED_TODAY_SQL, OPENED_TODAY_SQL, SIGNALS_SQL,
)

# The keys of trade_risk_state that say the pause (src/database/postgres.py, AsyncDbRiskStateManager).
PAUSED_SINCE_KEY = "entries_paused_since"
PAUSED_BY_GUARD_KEY = "entries_paused_by_account_guard"
_LONG_NUMBER = re.compile(r"\b\d{7,}\b")


def _rule_config(rules, rule_type: str) -> dict | None:
    for rule in rules if isinstance(rules, list) else []:
        if isinstance(rule, dict) and rule.get("rule_type") == rule_type:
            return rule.get("rule_config") if isinstance(rule.get("rule_config"), dict) else {}
    return None


@dataclass(frozen=True)
class CommandSettings:
    """What the answers need of config.json, read once at the service's start."""

    dsn: str
    timezone: str = "Europe/Paris"
    saxo_environment: str = "live"
    capital_feed: bool = False
    price_recorder: bool = False
    ta_engine: bool = False
    news_rule: NewsRule | None = None
    max_trades_per_day: int = 0

    @classmethod
    def from_config(cls, config_manager) -> "CommandSettings":
        value = config_manager.get_config_value
        rules = value("trade.rules", [])
        news = _rule_config(rules, "macro_news")
        try:
            news_rule = NewsRule.from_config(news) if news is not None else None
        except ValueError:
            news_rule = None
        risk = _rule_config(rules, "risk_management") or {}
        most = risk.get("max_trades_per_day")
        return cls(
            dsn=value("postgresql.dsn"),
            timezone=value("trade.config.general.timezone", "Europe/Paris") or "Europe/Paris",
            saxo_environment=value("saxo_auth.env", "live") or "live",
            capital_feed=value("trade.config.capital_feed.enabled", False) is True,
            price_recorder=value("trade.config.price_recorder.enabled", False) is True,
            ta_engine=value("trade.config.ta_engine.enabled", False) is True,
            news_rule=news_rule,
            max_trades_per_day=most if isinstance(most, int) and not isinstance(most, bool) and most > 0 else 0,
        )


@dataclass
class Calendars:
    """The US releases and market days, read once: a new calendar comes with a deploy, which restarts the service."""

    macro: Calendar | None = None
    market: MarketCalendar | None = None
    errors: list = field(default_factory=list)

    @classmethod
    def read(cls) -> "Calendars":
        found = cls()
        try:
            found.macro = read_calendar()
        except Exception as e:
            found.errors.append(f"US releases: {type(e).__name__}")
        try:
            found.market = read_market_calendar()
        except Exception as e:
            found.errors.append(f"US market days: {type(e).__name__}")
        return found


def command_of(text: str) -> str | None:
    """The command a message is, lower-cased and without the bot's name (``/status@wata_bot``); None for plain text."""
    words = (text or "").strip().split()
    if not words or not words[0].startswith("/"):
        return None
    return words[0].split("@", 1)[0].lower()


def cleaned(text, limit: int = REASON_CHARS) -> str:
    """What a service wrote, fit for the chat: secrets' shapes masked as in the logs, no long number, cut."""
    said = _LONG_NUMBER.sub("…", redact(str(text or "")).replace("\n", " ").strip())
    return said if len(said) <= limit else said[: limit - 1].rstrip() + "…"


def _moment(value) -> datetime | None:
    """A time from a row or a heartbeat's figures (ISO text there), aware; None when there is none."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


class Clock:
    """Times as the operator reads them, in the trading timezone."""

    def __init__(self, timezone_name: str, now: datetime):
        self.zone = pytz.timezone(timezone_name)
        self.now = now.astimezone(self.zone)
        self.name = timezone_name

    def at(self, value, seconds: bool = False) -> str:
        moment = _moment(value)
        if moment is None:
            return "?"
        local = moment.astimezone(self.zone)
        clock = local.strftime("%H:%M:%S" if seconds else "%H:%M")
        return clock if local.date() == self.now.date() else f"{local:%a} {local.day} {local:%b} {clock}"

    def beside(self, epoch: float, other: float) -> str:
        """The time of ``epoch``, its hour alone when it falls on the day of ``other``."""
        local, beside = (datetime.fromtimestamp(moment, self.zone) for moment in (epoch, other))
        return local.strftime("%H:%M") if local.date() == beside.date() else self.at(local)

    def ago(self, value) -> str:
        moment = _moment(value)
        if moment is None:
            return "?"
        seconds = max(0, int((self.now - moment).total_seconds()))
        if seconds < 120:
            return f"{seconds} s ago"
        if seconds < 7200:
            return f"{seconds // 60} min ago"
        if seconds < 172800:
            return f"{seconds // 3600} h ago"
        return f"{seconds // 86400} days ago"

    def age(self, value) -> float | None:
        moment = _moment(value)
        return None if moment is None else (self.now - moment).total_seconds()

    def day_bounds(self) -> tuple[datetime, datetime]:
        """Today in the trading timezone, as the moments it starts and ends."""
        start = self.zone.localize(datetime(self.now.year, self.now.month, self.now.day))
        following = self.now.date() + timedelta(days=1)
        return start, self.zone.localize(datetime(following.year, following.month, following.day))


def _percent(value) -> str:
    if value is None:
        return "?"
    return f"{value:+.2f} %".replace("-", "−")


def _euros(value) -> str:
    if value is None:
        return "?"
    return f"{value:+.2f} €".replace("-", "−")


def _price(value) -> str:
    return "?" if value is None else f"{value:.2f} €"


def _side(action) -> str:
    return str(action or "?").capitalize()


def _count(number: int, word: str) -> str:
    return f"{number} {word}" if number == 1 else f"{number} {word}s"


def _figures(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


# ── what each part of /status says ──

def trader_line(beat: dict | None, clock: Clock) -> str:
    if beat is None:
        return "Trader: no heartbeat in the database"
    age = clock.age(beat.get("beat_at"))
    if age is None or age > HEARTBEAT_STALE_SECONDS:
        return f"Trader: DOWN, last heartbeat {clock.ago(beat.get('beat_at'))}"
    figures = _figures(beat.get("figures"))
    version = f" (v{beat['version']})" if beat.get("version") else ""
    notes = []
    login = figures.get("saxo_login") or {}
    if login.get("renewal_failing_since"):
        notes.append(f"Saxo refuses to renew the login since {clock.at(login['renewal_failing_since'])}: no new position")
    if figures.get("poll_failing_since"):
        notes.append(f"cannot reach the web server since {clock.at(figures['poll_failing_since'])}")
    if figures.get("signals_silent"):
        notes.append(f"no signal since {clock.at(figures.get('last_signal_at'))}")
    session = figures.get("saxo_session") or {}
    level = session.get("trade_level")
    if level and level != "FullTradingAndChat":
        notes.append(f"Saxo serves the session at {level}: delayed prices")
    if notes:
        return f"Trader: WARNING{version}, " + "; ".join(notes)
    last = figures.get("last_signal_at")
    return f"Trader: running{version}" + (f", last signal {clock.at(last)}" if last else "")


def monitor_line(beat: dict | None, clock: Clock) -> str:
    if beat is None:
        return "Position monitor: no heartbeat in the database"
    age = clock.age(beat.get("beat_at"))
    if age is None or age > HEARTBEAT_STALE_SECONDS:
        return f"Position monitor: DOWN, last heartbeat {clock.ago(beat.get('beat_at'))}"
    figures = _figures(beat.get("figures"))
    intents = figures.get("close_intents") or []
    if intents:
        first = intents[0] if isinstance(intents[0], dict) else {}
        attempts = first.get("attempts") or 0
        more = f", and {_count(len(intents) - 1, 'other position')}" if len(intents) > 1 else ""
        return (f"Position monitor: WARNING, selling a position since {clock.at(first.get('since'))} "
                f"({_count(attempts, 'failed attempt')}){more}")
    if not figures.get("stream_connected"):
        return "Position monitor: WARNING, not connected to Saxo's position stream"
    if figures.get("rest_fallback_since"):
        return (f"Position monitor: WARNING, the position stream is silent: positions checked through REST since "
                f"{clock.at(figures['rest_fallback_since'])}")
    listed = figures.get("positions_listed") or 0
    return f"Position monitor: streaming, {_count(listed, 'position')} at Saxo" if listed else \
        "Position monitor: streaming, no position at Saxo"


def pause_line(state: dict, clock: Clock) -> str:
    since = state.get(PAUSED_SINCE_KEY)
    if since is None:
        return "Entries: taken (not paused)"
    who = "by the account guard" if state.get(PAUSED_BY_GUARD_KEY) is not None else "from the admin"
    return f"Entries: PAUSED {who} since {clock.at(since)}"


def saxo_line(row: dict | None, clock: Clock) -> str:
    if row is None:
        return "Saxo login: none in the database"
    if row.get("needs_authorization_since") is not None:
        return (f"Saxo login: NEEDS AUTHORISING since {clock.at(row['needs_authorization_since'])} "
                "(the admin's Saxo page)")
    expires = _moment(row.get("refresh_expires_at"))
    if expires is None:
        return "Saxo login: no refresh token"
    if expires <= clock.now:
        return f"Saxo login: EXPIRED at {clock.at(expires)} (the admin's Saxo page)"
    return f"Saxo login: renewable until {clock.at(expires)}"


def feed_line(name: str, updated_at, silent_after: float, clock: Clock, running: str) -> str:
    age = clock.age(updated_at)
    if age is None:
        return f"{name}: never ran"
    if age > silent_after:
        return f"{name}: SILENT, last sign {clock.ago(updated_at)}"
    return f"{name}: {running}, {clock.ago(updated_at)}"


def positions_line(positions: list[dict]) -> str:
    if not positions:
        return "Positions: none open"
    results = ", ".join(_percent(position.get("performance_percent")) for position in positions)
    return f"Positions: {len(positions)} open, live {results}"


def today_line(closed: list[dict]) -> str:
    if not closed:
        return "Today: no trade closed"
    _, now = best_and_now(row.get("position_total_performance_percent") for row in closed)
    money = sum(row.get("position_profit_loss") or 0 for row in closed)
    return f"Today: {_count(len(closed), 'trade')} closed, {_percent(now)} compounded, {_euros(money)}"


def market_line(calendars: Calendars, now: datetime) -> str | None:
    """The US market's day when it is not an ordinary one; None otherwise."""
    if calendars.market is None:
        return None
    day = new_york_day(now.timestamp())
    closed = calendars.market.closed_on(day)
    if closed is not None:
        return f"US market: CLOSED today ({closed.name})"
    early = calendars.market.early_close_on(day)
    if early is not None:
        return f"US market: closes early today, at {early.close_new_york} New York ({early.name})"
    return None


def release_line(calendars: Calendars, rule: NewsRule | None, clock: Clock) -> str:
    """The next US release the rule names, and the window it keeps; without a rule, the next of the default ones."""
    if calendars.macro is None:
        return "Next US release: the calendar could not be read"
    kept = rule or NewsRule()
    events = kept.events if rule is not None else DEFAULT_EVENTS
    moment = clock.now.timestamp()
    first = new_york_day(moment) - timedelta(days=1)
    for offset in range(NEXT_RELEASE_DAYS + 2):
        for event in sorted(calendars.macro.on(first + timedelta(days=offset)), key=lambda found: found.moment()):
            if event.event not in events:
                continue
            window = kept.window(event)
            if window.end <= moment:
                continue
            released = clock.at(datetime.fromtimestamp(event.moment(), timezone.utc))
            if rule is None:
                return f"Next US release: {event.event} {released} (no window kept: no macro_news rule)"
            span = f"no entry from {clock.beside(window.start, event.moment())} to {clock.beside(window.end, event.moment())}"
            if window.start <= moment:
                return f"Next US release: {event.event} {released}, IN ITS WINDOW NOW: {span}"
            return f"Next US release: {event.event} {released}, {span}"
    until = calendars.macro.known_until(events)
    ends = f": the calendar ends on {until.isoformat()}" if until and until < clock.now.date() + timedelta(
        days=NEXT_RELEASE_DAYS) else ""
    return f"Next US release: none in the next {NEXT_RELEASE_DAYS} days{ends}"


# ── /positions, /day, /signals ──

def position_lines(position: dict, worst: float | None, clock: Clock) -> list[str]:
    name = cleaned(position.get("instrument_name") or "", NAME_CHARS)
    lines = [f"{_side(position.get('action'))}" + (f" · {name}" if name else "")]
    lines.append(f"Bought {clock.at(position.get('execution_time_open'))}: "
                 f"{position.get('position_amount') or '?'} at {_price(position.get('position_open_price'))}")
    if position.get("bid") is None:
        lines.append("No live value yet")
    else:
        age = clock.age(position.get("observed_at"))
        seen = clock.ago(position.get("observed_at"))
        stale = " (OLD)" if age is not None and age > LIVE_VALUE_FRESH_SECONDS else ""
        lines.append(f"Now {_price(position['bid'])} ({_percent(position.get('performance_percent'))}), {seen}{stale}")
    lines.append(f"Best {_percent(position.get('position_max_performance_percent'))}, "
                 f"worst {_percent(worst) if worst is not None else 'not recorded'}")
    return lines


def closed_line(row: dict, clock: Clock) -> str:
    reason = cleaned(row.get("position_close_reason") or "", 60)
    return (f"{clock.at(row.get('execution_time_open'))} → {clock.at(row.get('execution_time_close'))}  "
            f"{_side(row.get('action'))}  {_percent(row.get('position_total_performance_percent'))}"
            + (f"  {reason}" if reason else ""))


def signal_line(row: dict, clock: Clock) -> str:
    confidence = row.get("confidence")
    said = f", confidence {confidence:.2f}" if isinstance(confidence, (int, float)) else ""
    reason = str(row.get("reason") or "")
    reason = reason[len(REFUSAL_PREFIX):] if reason.startswith(REFUSAL_PREFIX) else reason
    reason = cleaned(reason)
    when = row.get("received_timestamp") or row.get("decided_at")
    return (f"{clock.at(when, seconds=True)} {row.get('action') or '?'} {row.get('indice') or '?'}{said}: "
            f"{row.get('outcome') or '?'}" + (f", {reason}" if reason else ""))


def _missing_table(error: Exception) -> bool:
    return type(error).__name__ == "UndefinedTableError"


async def _connect(dsn: str):
    """One connection, read-only for every transaction it runs: a write on it is refused by PostgreSQL itself."""
    import asyncpg

    return await asyncpg.connect(
        dsn, timeout=CONNECT_TIMEOUT_SECONDS, command_timeout=STATEMENT_TIMEOUT_SECONDS, server_settings=dict(READ_ONLY),
    )


class Reader:
    """The SELECTs of one command, on its connection; a table a service has not made yet reads as empty."""

    def __init__(self, connection):
        self._connection = connection

    async def rows(self, sql: str, *args) -> list[dict]:
        if sql not in STATEMENTS:
            raise ValueError("Only the statements of STATEMENTS are run")
        try:
            return [dict(row) for row in await self._connection.fetch(sql, *args)]
        except Exception as e:
            if _missing_table(e):
                return []
            raise

    async def row(self, sql: str, *args) -> dict | None:
        found = await self.rows(sql, *args)
        return found[0] if found else None


class Commands:
    """Each command's answer, read from PostgreSQL at the moment it is asked."""

    def __init__(self, settings: CommandSettings, calendars: Calendars | None = None, connect=None, now=None):
        self.settings = settings
        self.calendars = calendars if calendars is not None else Calendars.read()
        self._connect = connect or _connect
        self._now = now or (lambda: datetime.now(timezone.utc))

    def answer(self, command: str) -> str:
        """The answer to ``command`` (``command_of``'s), in one message; never raises."""
        if command in (HELP, START):
            return HELP_TEXT
        if command not in COMMANDS:
            return f"Unknown command {cleaned(command, 40)}.\n\n{HELP_TEXT}"
        try:
            return asyncio.run(asyncio.wait_for(self._read(command), STATEMENT_TIMEOUT_SECONDS + CONNECT_TIMEOUT_SECONDS))
        except Exception as e:
            return f"Could not read WATA's database ({type(e).__name__}): nothing was answered."

    async def _read(self, command: str) -> str:
        connection = await self._connect(self.settings.dsn)
        try:
            reader = Reader(connection)
            clock = Clock(self.settings.timezone, self._now())
            said = {STATUS: self._status, POSITIONS: self._positions, DAY: self._day, SIGNALS: self._signals}
            return await said[command](reader, clock)
        finally:
            await connection.close()

    async def _open_positions(self, reader: Reader) -> tuple[list[dict], dict]:
        positions = await reader.rows(OPEN_POSITIONS_SQL)
        if not positions:
            return [], {}
        worst = await reader.rows(WORST_SQL, [position["position_id"] for position in positions])
        return positions, {row["position_id"]: row["worst_percent"] for row in worst}

    async def _status(self, reader: Reader, clock: Clock) -> str:
        settings = self.settings
        beats = {row["service"]: row for row in await reader.rows(HEARTBEATS_SQL)}
        state = {row["key"]: row["value_time"]
                 for row in await reader.rows(RISK_STATE_SQL, [PAUSED_SINCE_KEY, PAUSED_BY_GUARD_KEY])}
        lines = [
            f"WATA at {clock.at(clock.now)} ({clock.name})",
            trader_line(beats.get("trader"), clock),
            monitor_line(beats.get("position_monitor"), clock),
            pause_line(state, clock),
            saxo_line(await reader.row(SAXO_LOGIN_SQL, settings.saxo_environment), clock),
        ]
        if settings.capital_feed:
            feed = await reader.row(CAPITAL_FEED_SQL) or {}
            lines.append(feed_line("Capital.com feed", feed.get("updated_at"), FEED_SILENT_AFTER_SECONDS, clock,
                                   str(feed.get("state") or "running")))
        if settings.price_recorder:
            coverage = await reader.row(RECORDER_SQL) or {}
            running = f"recording {_count(coverage.get('bars') or 0, 'instrument')}"
            lines.append(feed_line("Price recorder", coverage.get("ts"), RECORDER_SILENT_AFTER_SECONDS, clock, running))
        if settings.ta_engine:
            engine = await reader.row(TA_ENGINE_SQL) or {}
            lines.append(feed_line("TA engine", engine.get("updated_at"), TA_ENGINE_SILENT_AFTER_SECONDS, clock,
                                   str(engine.get("state") or "running")))
        positions, _ = await self._open_positions(reader)
        lines.append(positions_line(positions))
        lines.append(today_line(await reader.rows(CLOSED_TODAY_SQL, *clock.day_bounds())))
        market = market_line(self.calendars, clock.now)
        if market:
            lines.append(market)
        lines.append(release_line(self.calendars, settings.news_rule, clock))
        lines.extend(f"Could not read the calendar of {error}" for error in self.calendars.errors)
        return "\n".join(lines)

    async def _positions(self, reader: Reader, clock: Clock) -> str:
        positions, worst = await self._open_positions(reader)
        if not positions:
            return f"No open position at {clock.at(clock.now)} ({clock.name})"
        blocks = [f"{_count(len(positions), 'open position')} at {clock.at(clock.now)} ({clock.name})"]
        for position in positions:
            blocks.append("\n".join(position_lines(position, worst.get(position["position_id"]), clock)))
        return "\n\n".join(blocks)

    async def _day(self, reader: Reader, clock: Clock) -> str:
        start, end = clock.day_bounds()
        closed = await reader.rows(CLOSED_TODAY_SQL, start, end)
        opened = (await reader.row(OPENED_TODAY_SQL, start, end) or {}).get("opened") or 0
        positions, _ = await self._open_positions(reader)
        lines = [f"Today, {clock.now:%a} {clock.now.day} {clock.now:%b} ({clock.name})", today_line(closed)]
        lines.extend(closed_line(row, clock) for row in closed)
        for position in positions:
            lines.append(f"Open: {_side(position.get('action'))} since {clock.at(position.get('execution_time_open'))}, "
                         f"live {_percent(position.get('performance_percent'))}")
        most = self.settings.max_trades_per_day
        lines.append(f"Positions opened today: {opened}" + (f" of {most} a day at most" if most else ""))
        return "\n".join(lines)

    async def _signals(self, reader: Reader, clock: Clock) -> str:
        rows = await reader.rows(SIGNALS_SQL, LAST_SIGNALS)
        if not rows:
            return "No signal in the journal"
        return "\n".join([f"Last {_count(len(rows), 'signal')} ({clock.name})"] + [signal_line(row, clock) for row in rows])
