"""
What a run trades on, as the trader receives it:

- ``pine_signals``: a Pine script of the Technical Analysis pages, its alerts
  mapped to actions as a signal rule maps them, run by the TA replay
  (src/ta_engine/replay.py) as the TA engine runs a rule: day by day from its
  restart, on its bars from ``history_days`` before, an alert counting once
  its bar closed. A signal reaches the trader ``signal_delay_seconds`` after.
- ``journal_signals``: the signals WATA received from TradingView
  (``signal_journal``), as the trader received them, each with what the live
  trader did of it.
- ``closing_signals``: the scheduler's ``close-position`` at the day's close.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from src.backtest.clock import TradingClock, utc_iso
from src.backtest.settings import BacktestSettings
from src.backtest.simulator import CLOSE_ACTIONS, ENTRY_ACTIONS, SignalEvent
from src.ta_engine.replay import AssetRows, ReplaySession, engine_windows, restart_minute_of
from src.ta_engine.rules import EngineRule

# The scheduler's own close names no indice: it is the run's close of the day instead.
SCHEDULER_INDICES = (None, "", "n/a")
NOT_A_SIGNAL = "not_a_signal"


@dataclass
class PineResult:
    events: list[SignalEvent] = field(default_factory=list)
    # The alerts that make no signal (a message that is not one): kept with the signals, as such.
    not_signals: list[dict] = field(default_factory=list)
    # By local day: the alerts the rule ignores, and the bars of the strategy.
    ignored: Counter = field(default_factory=Counter)
    strategy_bars: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)
    versions: dict = field(default_factory=dict)
    milliseconds: int = 0
    # Days of the engine read back from the cache, and run.
    cached_windows: int = 0
    run_windows: int = 0


async def pine_signals(
    rule: dict,
    engine_rule: EngineRule,
    rows: AssetRows,
    settings: BacktestSettings,
    clock: TradingClock,
    start: int,
    end: int,
    *,
    node: str = "node",
    command: list[str] | None = None,
    first_id: int = 1,
    progress=None,
    cache: str | None = None,
) -> PineResult:
    """
    The signals the rule raises on the days of [start, end), local days of
    the trading timezone. With a ``cache`` folder, a day the runner answered
    before is read back (src/ta_engine/replay.py, ``ReplaySession``).
    """
    session = ReplaySession(
        rule, engine_rule, rows, node=node, history_days=settings.history_days,
        restart_minute=restart_minute_of(settings.restart_time), command=command, cache=cache,
    )
    result = PineResult()
    number = first_id
    windows = engine_windows(start, end, session.restart_minute)
    await session.start()
    try:
        result.versions = dict(session.versions)
        for place, (begin, finish) in enumerate(windows):
            if progress is not None:
                progress(place, len(windows), clock.day_of(min(max(begin, start), end - 1)))
            window = await session.window(begin, finish, period=(start, end))
            result.milliseconds += window.milliseconds or 0
            result.warnings += [warning for warning in window.warnings if warning not in result.warnings]
            for bar in window.strategy_bars:
                closed = bar[6] / 1000
                if begin < closed <= finish and start * 1000 <= bar[0] < end * 1000:
                    result.strategy_bars[clock.day_of(bar[0] / 1000).isoformat()] += 1
            for replayed in window.alerts:
                bar, alert, candidate = replayed.bar, replayed.alert, replayed.candidate
                closed_at = bar[6] / 1000
                day = clock.day_of(closed_at).isoformat()
                if candidate is None:
                    result.ignored[day] += 1
                    continue
                detail = {
                    "bar_time": utc_iso(bar[0] / 1000),
                    "bar_closed_at": utc_iso(closed_at),
                    "alert": {"type": alert.get("type"), "title": alert.get("title"), "message": candidate.message},
                    "note": candidate.detail,
                }
                if candidate.action is None:
                    result.not_signals.append({
                        "id": number, "day": day, "source": "pine", "action": None, "indice": rule["indice"],
                        "confidence": None, "arrived_at": None, "decided_at": None, "fate": NOT_A_SIGNAL,
                        "rule": NOT_A_SIGNAL, "reason": candidate.detail or "The alert's message is not a signal",
                        "trade_id": None, "closed_trade_ids": [], "candidates": None, **detail,
                    })
                    number += 1
                    continue
                result.events.append(SignalEvent(
                    id=number, source="pine", action=candidate.action,
                    arrived_at=closed_at + settings.signal_delay_seconds, alert_at=closed_at,
                    indice=rule["indice"], confidence=candidate.confidence, detail=detail,
                ))
                number += 1
    finally:
        await session.stop()
    result.cached_windows, result.run_windows = session.cached_windows, session.run_windows
    return result


def _moment(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def journal_signals(rows: list[dict], indice: str, settings: BacktestSettings, start: int, end: int,
                    *, first_id: int = 1) -> list[SignalEvent]:
    """
    The signals of the journal received in [start, end), as the trader got
    them. The scheduler's closes are left out (the run closes each day at its
    own ``close_position_time``), as are the entries for another indice.
    """
    events = []
    number = first_id
    ordered = sorted(rows, key=lambda row: _moment(row.get("received_timestamp")) or _moment(row.get("decided_at")) or 0)
    for row in ordered:
        action = row.get("action")
        if action not in ENTRY_ACTIONS and action not in CLOSE_ACTIONS:
            continue
        row_indice = row.get("indice")
        if action == "close-position" and row_indice in SCHEDULER_INDICES:
            continue
        if action in ENTRY_ACTIONS and row_indice != indice:
            continue
        received = _moment(row.get("received_timestamp")) or _moment(row.get("signal_timestamp")) or _moment(row.get("decided_at"))
        if received is None or not start <= received < end:
            continue
        confidence = row.get("confidence")
        events.append(SignalEvent(
            id=number, source="journal", action=action, arrived_at=received + settings.signal_delay_seconds,
            alert_at=received, indice=indice if row_indice in SCHEDULER_INDICES else row_indice,
            confidence=float(confidence) if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else None,
            detail={
                "signal_uuid": row.get("signal_uuid"),
                "signal_time": utc_iso(_moment(row.get("signal_timestamp"))),
                "received_at": utc_iso(received),
                "live_outcome": row.get("outcome"),
                "live_reason": row.get("reason"),
            },
        ))
        number += 1
    return events


def raised_signals(rows: list[dict], indice: str, settings: BacktestSettings, start: int, end: int,
                   *, first_id: int = 1) -> list[SignalEvent]:
    """
    The signals a rule raised live (``ta_signal``: a shadow rule's are only
    journaled), as the trader would have got them: from the moment the engine
    raised one, ``signal_delay_seconds`` after. What a rule would have made of
    its own days, without replaying its script: paper trading.
    """
    events = []
    number = first_id
    for row in sorted(rows, key=lambda found: (_moment(found.get("raised_at")) or 0, found.get("id") or 0)):
        action = row.get("action")
        if action not in ENTRY_ACTIONS and action not in CLOSE_ACTIONS:
            continue
        raised = _moment(row.get("raised_at"))
        if raised is None or not start <= raised < end:
            continue
        confidence = row.get("confidence")
        closed_at = _moment(row.get("bar_closed_at"))
        events.append(SignalEvent(
            id=number, source="pine", action=action, arrived_at=raised + settings.signal_delay_seconds,
            alert_at=closed_at if closed_at is not None else raised, indice=indice,
            confidence=float(confidence) if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else None,
            detail={
                "bar_time": utc_iso(_moment(row.get("bar_time"))),
                "bar_closed_at": utc_iso(closed_at),
                "raised_at": utc_iso(raised),
                "alert": {"type": "raised", "title": row.get("alert_title"), "message": row.get("alert_message")},
                "live_mode": row.get("mode"),
            },
        ))
        number += 1
    return events


def closing_signals(days: list[date], settings: BacktestSettings, clock: TradingClock, *, first_id: int) -> list[SignalEvent]:
    """The scheduler's ``close-position`` at the close of each day, a moment after its minute begins."""
    events = []
    for offset, day in enumerate(days):
        moment = clock.epoch(day, settings.close_position_time) + 0.5
        events.append(SignalEvent(
            id=first_id + offset, source="schedule", action="close-position", arrived_at=moment, alert_at=moment,
            indice="n/a",
        ))
    return events


def by_day(events: list[SignalEvent], clock: TradingClock) -> dict[str, list[SignalEvent]]:
    days: dict[str, list[SignalEvent]] = defaultdict(list)
    for event in events:
        days[clock.day_of(event.arrived_at).isoformat()].append(event)
    return days
