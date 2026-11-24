"""
A signal rule's days set side by side (the rule page's "Live, paper and backtest", MCP ``rule_live``): what WATA really
traded on its signals once it is live, what its paper day made of the same signals, and what the backtest configuration
linked to it made of the same days, with that configuration's calibrated run (src/admin/live_check_service.py).

Every path keeps a signal's bar and its alert: the engine's ``ta_signal`` is unique on them (its alert_key is
"alertcondition:<title>"), and a Pine replay's signals and a paper run's keep ``bar_time`` and ``alert.title``. A
signal is known by both (``key_of``), a trade by its entry signal's. A signal the replay raised at another bar than
the engine is paired with it within the rule's tolerance (src/admin/ta_compare.py), and said to be at another bar.

The bridge says where a result went, in points: the sum of the trades' results (% of the turbo), which adds up exactly
from one track to the next, each trade in one bucket. The day's compounded result is shown beside it, never mixed in.

- From the backtest to paper (the signals): not raised by the engine, raised by the engine only, traded on one side
  only (a rule of WATA refused it on the other), the same signal at another bar, and the same signal filled otherwise
  (the delay, the account, the slippage).
- From paper to live (the execution): not traded live (refused, failed, never sent), traded live only, another turbo,
  and on the same turbo: the entry price, the moment of the exit (the price the live sale was decided on against
  paper's exit) and the exit's fill.

Pure, stdlib. The admin's only: nothing of it is in a backtest's code digest (src/backtest/figures.py ``_CODE``).
"""

import math
from datetime import date, datetime, timezone
from statistics import median

from src.admin.analysis import exit_group
from src.admin.signal_trace import ref_for
from src.admin.ta_compare import compare

ENTRY_ACTIONS = ("long", "short")
CLOSE_ACTIONS = ("close-position", "close-long", "close-short")
BACKTEST, CALIBRATED, PAPER, LIVE = "backtest", "calibrated", "paper", "live"
TRACKS = (BACKTEST, CALIBRATED, PAPER, LIVE)
# The script replayed on the recorded bars, and the engine's signals as they were raised live.
REPLAYED = (BACKTEST, CALIBRATED)
RAISED = (PAPER, LIVE)
# The live entries a value is measured on before the calibrated run takes it.
LEAST_MEASURES = 10
# The settings a calibrated run changes, in the order they are said.
CALIBRATED_SETTINGS = ("signal_delay_seconds", "execution", "capital")

# The bridge's buckets, in the order they are said.
TO_PAPER = (
    ("not_raised_live", "Not raised by the engine"),
    ("raised_only_live", "Raised by the engine only"),
    ("fate_differs", "Traded on one side only"),
    ("other_bar", "Same signal, another bar"),
    ("same_signal", "Same signal, other fills"),
)
TO_LIVE = (
    ("not_traded_live", "Not traded live"),
    ("traded_live_only", "Traded live only"),
    ("other_turbo", "Another turbo"),
    ("entry_price", "Entry price"),
    ("exit_moment", "Exit moment"),
    ("exit_fill", "Exit fill"),
    ("exit", "Exit, moment and fill"),
    ("unpriced", "Same turbo, prices not known"),
)
# A start of the engine this long after a bar's close makes it the reason the bar's alert was never sent.
ENGINE_START_WITHIN = 12 * 3600


def moment(value) -> float | None:
    """A moment as epoch seconds: a datetime, ISO text (UTC when it names no zone) or a number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
    try:
        found = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (found if found.tzinfo else found.replace(tzinfo=timezone.utc)).timestamp()


def iso(value) -> str | None:
    """A moment as a time of UTC, to the second."""
    found = moment(value)
    if found is None:
        return None
    return datetime.fromtimestamp(round(found), timezone.utc).isoformat().replace("+00:00", "Z")


def number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        found = float(value)
    except (TypeError, ValueError):
        return None
    return found if math.isfinite(found) else None


def _rounded(value: float | None, digits: int = 3) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def _seconds(later, earlier) -> float | None:
    first, second = moment(later), moment(earlier)
    return None if first is None or second is None else round(first - second, 1)


def key_of(bar_time, title) -> str | None:
    """A signal's key whatever path it took: its bar's open, to the second, and its alert's title."""
    at = moment(bar_time)
    if at is None or not title:
        return None
    return f"{int(round(at))}|{title}"


def _action(action: str | None) -> str | None:
    return "close-position" if action in CLOSE_ACTIONS else action


def compounded(results: list[float]) -> float:
    level = 1.0
    for result in results:
        level *= max(0.0, 1 + result / 100)
    return round((level - 1) * 100, 3)


# ── the tracks ──

def simulated_trade(trade: dict, key: str | None) -> dict:
    """A run's trade as the comparison reads it (src/backtest/simulator.py ``_sell``)."""
    entry, exit_ = trade.get("entry") or {}, trade.get("exit") or {}
    turbo = trade.get("turbo") or {}
    reason = exit_.get("reason")
    return {
        "key": key,
        "id": trade.get("id"),
        "direction": trade.get("direction"),
        "turbo": {"uic": turbo.get("uic"), "description": turbo.get("description")},
        "decided_at": iso(entry.get("decided_at")),
        "filled_at": iso(entry.get("filled_at")),
        "entry_price": number(entry.get("price")),
        "exit_at": iso(exit_.get("filled_at")),
        # The simulator writes the price a sale was filled at as the exit's bid.
        "exit_price": number(exit_.get("bid", exit_.get("price"))),
        "exit_reason": reason,
        "exit_group": exit_.get("group") or exit_group(reason),
        "result": number(trade.get("performance_percent")),
        "best": number(trade.get("max_performance_percent")),
        "worst": number(trade.get("min_performance_percent")),
        "units": trade.get("units"),
        "flags": list(trade.get("flags") or []),
    }


def run_track(signals: list[dict], trades: list[dict]) -> dict:
    """
    A run's signals and trades of a day, by key: those of a Pine replay or of a paper run (both keep the bar and the
    alert); the scheduler's closes and a journal's signals have neither and are left out.
    """
    found: dict[str, dict] = {}
    key_of_signal: dict = {}
    for record in signals:
        action = record.get("action")
        if action not in ENTRY_ACTIONS and action not in CLOSE_ACTIONS:
            continue
        alert = record.get("alert") or {}
        key = key_of(record.get("bar_time"), alert.get("title"))
        if key is None:
            continue
        key_of_signal[record.get("id")] = key
        if key in found:
            continue
        found[key] = {
            "key": key, "at": iso(record.get("bar_time")), "closed_at": iso(record.get("bar_closed_at")),
            "action": action, "title": alert.get("title"), "fate": record.get("fate"), "reason": record.get("reason"),
            "rule": record.get("rule"), "arrived_at": iso(record.get("arrived_at")),
            "decided_at": iso(record.get("decided_at")), "trade_id": record.get("trade_id"),
        }
    kept: dict[str, dict] = {}
    unkeyed = []
    for trade in trades:
        key = key_of_signal.get(trade.get("signal_id"))
        normalized = simulated_trade(trade, key)
        if key is None or key in kept:
            unkeyed.append(normalized)
        else:
            kept[key] = normalized
    return {"signals": found, "trades": kept, "unkeyed": unkeyed}


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return str(value.get("reason") or value.get("error") or value.get("detail") or "") or None
    return str(value)[:300] or None


def live_fate(row: dict) -> tuple[str, str | None]:
    """What became of a signal the engine raised: its fate and why, the trader's when it got it, else the engine's."""
    trader = row.get("trader") or {}
    if row.get("mode") != "live" or row.get("outcome") == "shadow":
        return "shadow", None
    if trader.get("outcome"):
        return str(trader["outcome"]), trader.get("reason")
    outcome = row.get("outcome")
    if outcome == "refused":
        return "not_sent", _text(row.get("detail")) or "Refused by the engine"
    if outcome == "failed":
        status = row.get("http_status")
        return "not_sent", _text(row.get("detail")) or (f"Sending failed ({status})" if status else "Sending failed")
    if outcome == "sent":
        return "not_journaled", "Sent, but the trader journaled nothing of it (a newer signal replaced it first)"
    return "unknown", None


def real_trade(position: dict, ledger: dict | None, key: str | None) -> dict:
    """A real position as the comparison reads it, with what the execution ledger measured of it."""
    ledger = ledger or {}
    reason = position.get("position_close_reason")
    to_order = [ledger.get("signal_to_received"), ledger.get("received_to_order")]
    return {
        "key": key,
        "id": str(position.get("position_id")),
        "direction": position.get("action"),
        "turbo": {"uic": position.get("instrument_uic"), "description": position.get("instrument_name")},
        "decided_at": None,
        "filled_at": iso(position.get("execution_time_open")),
        "entry_price": number(position.get("position_open_price")),
        "exit_at": iso(position.get("execution_time_close")),
        "exit_price": number(position.get("position_close_price")),
        "exit_reason": reason,
        "exit_group": exit_group(reason),
        "result": number(position.get("position_total_performance_percent")),
        "best": number(position.get("position_max_performance_percent")),
        "worst": number(position.get("position_min_performance_percent")),
        "units": None,
        "flags": [],
        # The price the sale was decided on (Saxo's bid, or the index's estimate between quotes), the ask the order
        # was sized on, and the seconds from the bar's close to the purchase leaving (the engine's signal's way to
        # WATA, then WATA's decision): what a Pine backtest's signal delay stands for.
        "decided_price": number(ledger.get("price_decided")),
        "decided_on": ledger.get("decided_on"),
        "ask_decided": number(ledger.get("ask_decided")),
        "order_seconds": _rounded(sum(to_order), 1) if all(value is not None for value in to_order) else None,
        "entry_slippage_percent": number(ledger.get("entry_slippage_percent")),
        "exit_slippage_percent": number(ledger.get("exit_slippage_percent")),
        "delay_percent": number(ledger.get("delay_percent")),
    }


def live_track(rows: list[dict], positions: list[dict], ledger_trades: list[dict]) -> dict:
    """
    The signals a rule raised on a day (``ta_signal`` with what the trader made of them: ``AdminQueries.rule_signals``)
    and the real positions they opened, by key.
    """
    by_position = {str(found["position_id"]): found for found in positions if found.get("position_id") is not None}
    ledger = {str(found["position_id"]): found for found in ledger_trades if found.get("position_id") is not None}
    signals: dict[str, dict] = {}
    trades: dict[str, dict] = {}
    for row in sorted(rows, key=lambda found: (moment(found.get("raised_at")) or 0, found.get("id") or 0)):
        key = key_of(row.get("bar_time"), row.get("alert_title"))
        if key is None or key in signals:
            continue
        trader = row.get("trader") or {}
        position_id = str(trader["position_id"]) if trader.get("position_id") is not None else None
        fate, reason = live_fate(row)
        signals[key] = {
            "key": key, "at": iso(row.get("bar_time")), "closed_at": iso(row.get("bar_closed_at")),
            "action": row.get("action"), "title": row.get("alert_title"), "mode": row.get("mode"),
            "fate": fate, "reason": reason,
            "engine": {"outcome": row.get("outcome"), "raised_seconds": _seconds(row.get("raised_at"), row.get("bar_closed_at")),
                       "sent_seconds": _seconds(row.get("sent_at"), row.get("bar_closed_at"))},
            "trader": {"outcome": trader.get("outcome"), "reason": trader.get("reason"),
                       "decided_seconds": _seconds(trader.get("decided_at"), row.get("bar_closed_at"))} if trader else None,
            "position_id": position_id,
            # The address of its own page (src/admin/signal_trace.py).
            "ref": ref_for(row.get("signal_uuid"), row.get("id")),
        }
        if row.get("action") in ENTRY_ACTIONS and position_id in by_position:
            trades[key] = real_trade(by_position[position_id], ledger.get(position_id), key)
    return {"signals": signals, "trades": trades, "unkeyed": []}


# ── a day ──

def _signal_at(signal: dict) -> datetime | None:
    found = moment(signal.get("at"))
    return datetime.fromtimestamp(found, timezone.utc) if found is not None else None


def pair(tracks: dict[str, dict], tolerance_seconds: int) -> list[dict]:
    """
    One row per signal, whichever tracks have it, in time order: exactly by key, then a signal only the replay has
    with one only the engine has, of the same action, within the tolerance (``near``: the engine's key and how many
    seconds later its bar opened).
    """
    keys = sorted({key for track in tracks.values() for key in track["signals"]}, key=lambda key: int(key.split("|", 1)[0]))
    rows = {key: {"key": key, "near": None,
                  "signals": {name: track["signals"].get(key) for name, track in tracks.items()},
                  "trades": {name: track["trades"].get(key) for name, track in tracks.items()}} for key in keys}

    def first_of(row, names):
        return next((row["signals"][name] for name in names if row["signals"].get(name)), None)

    replayed_only = [row for row in rows.values() if first_of(row, REPLAYED) and not first_of(row, RAISED)]
    raised_only = [row for row in rows.values() if first_of(row, RAISED) and not first_of(row, REPLAYED)]
    if replayed_only and raised_only and tolerance_seconds > 0:
        ours = [{"bar_time": _signal_at(first_of(row, REPLAYED)), "action": _action(first_of(row, REPLAYED)["action"]),
                 "row": row} for row in replayed_only]
        theirs = [{"signal_timestamp": _signal_at(first_of(row, RAISED)), "action": _action(first_of(row, RAISED)["action"]),
                   "row": row} for row in raised_only]
        paired = compare([found for found in ours if found["bar_time"]], [found for found in theirs if found["signal_timestamp"]],
                         tolerance_seconds)
        for found in paired["rows"]:
            if not found["wata"] or not found["tradingview"]:
                continue
            mine, other = found["wata"]["row"], found["tradingview"]["row"]
            for name in RAISED:
                mine["signals"][name] = other["signals"].get(name)
                mine["trades"][name] = other["trades"].get(name)
            mine["near"] = {"key": other["key"], "seconds": found["delta_seconds"]}
            rows.pop(other["key"], None)
    ordered = sorted(rows.values(), key=lambda row: int(row["key"].split("|", 1)[0]))
    for row in ordered:
        signal = next((found for found in row["signals"].values() if found), {})
        row.update(at=signal.get("at"), closed_at=signal.get("closed_at"), action=signal.get("action"),
                   title=signal.get("title"))
    return ordered


def _add(buckets: dict, bucket: str, points: float | None, key: str) -> None:
    if points is None:
        return
    entry = buckets.setdefault(bucket, {"points": 0.0, "trades": 0, "keys": []})
    entry["points"] += points
    entry["trades"] += 1
    entry["keys"].append(key)


def _result(trade: dict | None) -> float | None:
    return trade.get("result") if trade else None


def same_turbo_split(paper: dict, live: dict) -> list[tuple[str, float]]:
    """
    How a live trade's result differs from paper's on the same turbo, in points, the parts adding up to the whole:
    the entry price (paper's exit kept), the moment of the exit (the price the live sale was decided on against
    paper's exit) and the exit's fill (what Saxo gave against that price). Without the decided price, the exit is one.
    """
    whole = live["result"] - paper["result"]
    e_p, x_p, e_l = paper.get("entry_price"), paper.get("exit_price"), live.get("entry_price")
    if not e_p or not e_l or x_p is None:
        return [("unpriced", whole)]
    entry = 100 * x_p * (1 / e_l - 1 / e_p)
    decided = live.get("decided_price")
    if decided is None:
        return [("entry_price", entry), ("exit", whole - entry)]
    exit_moment = 100 * (decided - x_p) / e_l
    return [("entry_price", entry), ("exit_moment", exit_moment), ("exit_fill", whole - entry - exit_moment)]


def bridge(rows: list[dict], present: set[str]) -> dict:
    """
    Where the day's result went, in points of summed trade results, from the backtest to paper and from paper to live
    (each only when both tracks are there that day), each trade's difference in one bucket; and the calibrated run
    beside the backtest and live.
    """
    entries = [row for row in rows if row.get("action") in ENTRY_ACTIONS]
    out: dict = {"to_paper": None, "to_live": None, "calibrated": None}
    if BACKTEST in present and PAPER in present:
        buckets: dict = {}
        for row in entries:
            backtest, paper = _result(row["trades"].get(BACKTEST)), _result(row["trades"].get(PAPER))
            replayed = any(row["signals"].get(name) for name in REPLAYED)
            raised = any(row["signals"].get(name) for name in RAISED)
            if backtest is not None and paper is not None:
                _add(buckets, "other_bar" if row.get("near") else "same_signal", paper - backtest, row["key"])
            elif backtest is not None:
                _add(buckets, "fate_differs" if raised else "not_raised_live", -backtest, row["key"])
            elif paper is not None:
                _add(buckets, "fate_differs" if replayed else "raised_only_live", paper, row["key"])
        out["to_paper"] = _segment(entries, BACKTEST, PAPER, buckets, TO_PAPER)
    if PAPER in present and LIVE in present:
        buckets = {}
        for row in entries:
            paper, live = row["trades"].get(PAPER), row["trades"].get(LIVE)
            if _result(paper) is not None and _result(live) is not None:
                if paper["turbo"].get("uic") is not None and paper["turbo"].get("uic") == live["turbo"].get("uic"):
                    for bucket, points in same_turbo_split(paper, live):
                        _add(buckets, bucket, points, row["key"])
                else:
                    _add(buckets, "other_turbo", _result(live) - _result(paper), row["key"])
            elif _result(paper) is not None:
                _add(buckets, "not_traded_live", -_result(paper), row["key"])
            elif _result(live) is not None:
                _add(buckets, "traded_live_only", _result(live), row["key"])
        out["to_live"] = _segment(entries, PAPER, LIVE, buckets, TO_LIVE)
    if CALIBRATED in present and LIVE in present and BACKTEST in present:
        out["calibrated"] = {name: _summed(entries, name) for name in (BACKTEST, CALIBRATED, LIVE)}
    return out


def _summed(entries: list[dict], name: str) -> dict:
    results = [row["trades"][name]["result"] for row in entries
               if row["trades"].get(name) and row["trades"][name].get("result") is not None]
    return {"trades": len(results), "sum": round(sum(results), 3), "compounded": compounded(results)}


def _segment(entries: list[dict], start: str, end: str, buckets: dict, order) -> dict:
    begin, finish = _summed(entries, start), _summed(entries, end)
    return {
        "from": start, "to": end, "start": begin, "end": finish,
        "buckets": [{"id": bucket, "label": label, "points": round(buckets[bucket]["points"], 3),
                     "trades": buckets[bucket]["trades"], "keys": buckets[bucket]["keys"]}
                    for bucket, label in order if bucket in buckets],
    }


def why_not_raised(closed_at, engine_starts: list[float], kept_since: float | None) -> str:
    """
    Why the engine raised nothing at a bar the replay alerted on: it started again after the bar (its alerts of bars
    in a load's history are never sent), else not known.
    """
    closed = moment(closed_at)
    if closed is None:
        return "Not known"
    later = sorted(start for start in engine_starts if 0 < start - closed <= ENGINE_START_WITHIN)
    if later:
        started = datetime.fromtimestamp(later[0], timezone.utc)
        return f"The engine was not running: it started at {started:%H:%M} UTC, and never sends a bar's alert it loaded as history"
    if kept_since is None or closed < kept_since:
        return "Not known: the engine's ticks of that day are no longer kept"
    return "Not known: the engine ran then"


def day_comparison(day: date, tracks: dict[str, dict], tolerance_seconds: int, *, engine_starts: list[float] = (),
                   kept_since: float | None = None) -> dict:
    """One day: each signal and each trade of the tracks there that day, and the bridge between them."""
    rows = pair(tracks, tolerance_seconds)
    present = set(tracks)
    signals, trades = [], []
    for row in rows:
        live_signal = row["signals"].get(LIVE) or row["signals"].get(PAPER)
        replayed = next((row["signals"][name] for name in REPLAYED if row["signals"].get(name)), None)
        signals.append({
            "key": row["key"], "at": row["at"], "closed_at": row["closed_at"], "action": row["action"],
            "title": row["title"], "near": row["near"],
            **{name: _signal_said(row["signals"].get(name)) for name in TRACKS if name in present},
            # A signal the script raises on the recorded bars that the engine never raised live.
            "why_not_raised": why_not_raised(replayed.get("closed_at"), list(engine_starts), kept_since)
            if replayed and not live_signal and (PAPER in present or LIVE in present) else None,
        })
        if row["action"] in ENTRY_ACTIONS and any(row["trades"].get(name) for name in present):
            paper, live = row["trades"].get(PAPER), row["trades"].get(LIVE)
            trades.append({
                "key": row["key"], "at": row["at"], "action": row["action"], "title": row["title"], "near": row["near"],
                **{name: row["trades"].get(name) for name in TRACKS if name in present},
                "same_turbo": paper["turbo"].get("uic") == live["turbo"].get("uic") if paper and live else None,
            })
    figures = {}
    for name in TRACKS:
        if name not in present:
            continue
        results = [trade[name]["result"] for trade in trades if trade.get(name) and trade[name].get("result") is not None]
        entries = [row for row in rows if row["action"] in ENTRY_ACTIONS and row["signals"].get(name)]
        figures[name] = {"trades": len(results), "sum": round(sum(results), 3), "compounded": compounded(results),
                         "entry_signals": len(entries), "unkeyed": len(tracks[name].get("unkeyed") or [])}
    return {"day": day.isoformat(), "present": [name for name in TRACKS if name in present],
            "tolerance_seconds": tolerance_seconds, "figures": figures, "signals": signals, "trades": trades,
            "bridge": bridge(rows, present), "agreement": agreement(rows, present)}


def _signal_said(signal: dict | None) -> dict | None:
    if signal is None:
        return None
    return {key: signal.get(key) for key in ("fate", "reason", "rule", "engine", "trader", "position_id", "mode")
            if signal.get(key) is not None}


def agreement(rows: list[dict], present: set[str]) -> dict | None:
    """
    How far the replay raised the engine's entry signals: at the same bar, at another bar within the tolerance, or
    not at all; and the replay's entries the engine never raised. None unless both sides are there.
    """
    if not any(name in present for name in REPLAYED) or not any(name in present for name in RAISED):
        return None
    same = near = not_replayed = not_raised = 0
    for row in rows:
        if row.get("action") not in ENTRY_ACTIONS:
            continue
        raised = any(row["signals"].get(name) for name in RAISED)
        replayed = any(row["signals"].get(name) for name in REPLAYED)
        if raised and replayed:
            near += 1 if row.get("near") else 0
            same += 0 if row.get("near") else 1
        elif raised:
            not_replayed += 1
        elif replayed:
            not_raised += 1
    return {"same_bar": same, "other_bar": near, "not_replayed": not_replayed, "not_raised": not_raised}


# ── over the days ──

def _added(total: dict | None, part: dict | None) -> dict | None:
    if part is None:
        return total
    if total is None:
        return {"from": part["from"], "to": part["to"], "days": 1,
                "start": {"trades": part["start"]["trades"], "sum": part["start"]["sum"]},
                "end": {"trades": part["end"]["trades"], "sum": part["end"]["sum"]},
                "buckets": {bucket["id"]: {"label": bucket["label"], "points": bucket["points"], "trades": bucket["trades"]}
                            for bucket in part["buckets"]}}
    total["days"] += 1
    for side in ("start", "end"):
        total[side]["trades"] += part[side]["trades"]
        total[side]["sum"] = round(total[side]["sum"] + part[side]["sum"], 3)
    for bucket in part["buckets"]:
        entry = total["buckets"].setdefault(bucket["id"], {"label": bucket["label"], "points": 0.0, "trades": 0})
        entry["points"] = round(entry["points"] + bucket["points"], 3)
        entry["trades"] += bucket["trades"]
    return total


def _ordered(total: dict | None, order) -> dict | None:
    if total is None:
        return None
    buckets = total.pop("buckets")
    total["buckets"] = [{"id": bucket, **buckets[bucket]} for bucket, _ in order if bucket in buckets]
    return total


def summary(days: list[dict]) -> dict:
    """
    The days together: each track's trades and results over the days it is there, the bridge's segments summed over
    the days both their tracks are there, how far the replay raised the engine's signals, and how much of the gap
    between the backtest and live the calibrated run closes.
    """
    tracks = {}
    for name in TRACKS:
        found = [day for day in days if name in day["figures"]]
        if not found:
            continue
        level = 1.0
        for day in found:
            level *= max(0.0, 1 + day["figures"][name]["compounded"] / 100)
        trades = sum(day["figures"][name]["trades"] for day in found)
        total = round(sum(day["figures"][name]["sum"] for day in found), 3)
        tracks[name] = {"days": len(found), "trades": trades, "sum": total,
                        "mean": round(total / trades, 3) if trades else None,
                        "compounded": round((level - 1) * 100, 3)}
    to_paper = to_live = None
    calibrated = None
    agreed = None
    for day in days:
        to_paper = _added(to_paper, day["bridge"]["to_paper"])
        to_live = _added(to_live, day["bridge"]["to_live"])
        part = day["bridge"]["calibrated"]
        if part is not None:
            calibrated = calibrated or {"days": 0, **{name: {"trades": 0, "sum": 0.0} for name in (BACKTEST, CALIBRATED, LIVE)}}
            calibrated["days"] += 1
            for name in (BACKTEST, CALIBRATED, LIVE):
                calibrated[name]["trades"] += part[name]["trades"]
                calibrated[name]["sum"] = round(calibrated[name]["sum"] + part[name]["sum"], 3)
        if day.get("agreement") is not None:
            agreed = agreed or {"days": 0, "same_bar": 0, "other_bar": 0, "not_replayed": 0, "not_raised": 0}
            agreed["days"] += 1
            for name in ("same_bar", "other_bar", "not_replayed", "not_raised"):
                agreed[name] += day["agreement"][name]
    if agreed is not None:
        raised = agreed["same_bar"] + agreed["other_bar"] + agreed["not_replayed"]
        agreed["raised"] = raised
        agreed["same_bar_percent"] = round(100 * agreed["same_bar"] / raised, 1) if raised else None
    if calibrated is not None:
        calibrated["closed_percent"] = gap_closed(calibrated[BACKTEST]["sum"], calibrated[CALIBRATED]["sum"],
                                                  calibrated[LIVE]["sum"])
    return {"tracks": tracks, "to_paper": _ordered(to_paper, TO_PAPER), "to_live": _ordered(to_live, TO_LIVE),
            "calibrated": calibrated, "agreement": agreed}


def gap_closed(backtest: float, calibrated: float, live: float) -> float | None:
    """The share of the gap between the backtest and live that the calibrated run closes; None without a gap."""
    gap = live - backtest
    if abs(gap) < 0.01:
        return None
    return round(100 * (1 - abs(live - calibrated) / abs(gap)), 1)


# ── is it the same strategy ──

def _canonical(value):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _by(items, key: str, value: str) -> dict:
    if isinstance(items, dict):
        return {str(name): _canonical(found) for name, found in items.items()}
    return {str(item.get(key)): _canonical(item.get(value)) for item in items or [] if isinstance(item, dict) and item.get(key)}


def same_strategy(rule: dict, request: dict, settings: dict, engine_history_days: int) -> dict:
    """
    Whether a Pine run plays the rule's strategy: the same script and libraries by their sha256 (not their ids: a
    copy of a script under another name is the same strategy), asset, side and bars, alerts and history; the inputs
    key by key, one set on one side only named (it is the same only if it is the script's default there).
    """
    source = request.get("source") or {}
    ran = source.get("rule") or {}
    checks = []

    def check(check_id: str, passed: bool | None, detail: str) -> None:
        checks.append({"id": check_id, "state": "pass" if passed else "note" if passed is None else "fail", "detail": detail})

    if source.get("kind") != "pine":
        check("source", False, "The run is not a replay of a Pine script")
        return {"same": False, "checks": checks}
    script, mine = ran.get("script") or {}, rule.get("script") or {}
    check("script", bool(script.get("sha256")) and script.get("sha256") == mine.get("sha256"),
          f"Script {script.get('id')} v{script.get('version')} against the rule's {mine.get('id')} v{mine.get('version')}"
          + (": the same source" if script.get("sha256") == mine.get("sha256") else ": another source"))
    libraries, rule_libraries = _by(ran.get("libraries"), "name", "sha256"), _by(rule.get("libraries"), "name", "sha256")
    if libraries == rule_libraries:
        check("libraries", True, "The same libraries" if libraries else "No library on either side")
    else:
        apart = sorted(set(libraries) ^ set(rule_libraries))
        other = sorted(name for name in set(libraries) & set(rule_libraries) if libraries[name] != rule_libraries[name])
        check("libraries", False, "Libraries differ: " + "; ".join(
            ([f"imported on one side only: {', '.join(apart)}"] if apart else [])
            + ([f"another version of {', '.join(other)}"] if other else [])))
    chart_same = all(_canonical(ran.get(part)) == _canonical(rule.get(part)) for part in ("asset", "side", "bars"))
    check("chart", chart_same, "The same asset, side and bars" if chart_same else "Another asset, side or bars")
    alerts, rule_alerts = _by(ran.get("alerts"), "title", "action"), _by(rule.get("alerts"), "title", "action")
    check("alerts", alerts == rule_alerts, "The same alerts, mapped alike" if alerts == rule_alerts else "Alerts mapped otherwise")
    history = rule.get("history_days", engine_history_days)
    ran_history = settings.get("history_days")
    same_history = ran_history is None or _canonical(ran_history) == _canonical(history)
    check("history", same_history,
          f"{history} day(s) of history before each engine day, as the rule" if same_history else
          f"{ran_history} day(s) of history before each engine day, against the rule's {history}")
    inputs, rule_inputs = _by(ran.get("inputs"), "id", "value"), _by(rule.get("inputs"), "id", "value")
    differ = sorted(name for name in set(inputs) & set(rule_inputs) if inputs[name] != rule_inputs[name])
    rule_only = sorted(set(rule_inputs) - set(inputs))
    run_only = sorted(set(inputs) - set(rule_inputs))
    if differ:
        check("inputs", False, "Inputs differ: " + ", ".join(f"{name} {inputs[name]} against {rule_inputs[name]}" for name in differ))
    elif rule_only or run_only:
        said = []
        if rule_only:
            said.append("set on the rule only: " + ", ".join(f"{name} {rule_inputs[name]}" for name in rule_only))
        if run_only:
            said.append("set on the backtest only: " + ", ".join(f"{name} {inputs[name]}" for name in run_only))
        check("inputs", None, "Inputs " + "; ".join(said) + " (the same only where it is the script's default)")
    else:
        check("inputs", True, "The same inputs")
    same = all(found["state"] != "fail" for found in checks)
    return {"same": same, "checks": checks}


# ── the calibrated run ──

def _figures(values: list[float]) -> dict:
    kept = sorted(value for value in values if value is not None)
    if not kept:
        return {"count": 0, "median": None, "p90": None}
    return {"count": len(kept), "median": round(median(kept), 3),
            "p90": round(kept[min(len(kept) - 1, math.ceil(0.9 * len(kept)) - 1)], 3)}


def calibration(live_trades: list[dict], *, samples_missing: list[str], balance: float | None, template: dict) -> dict:
    """
    What the calibrated run changes of its template, measured on the rule's live trades and WATA's orders: the signal's
    delay (from the bar's close to the purchase leaving, from LEAST_MEASURES entries on), fills drawn from WATA's own
    orders (when enough were measured), the account WATA holds. What it keeps, and why, is said.
    """
    delays = _figures([trade.get("order_seconds") for trade in live_trades])
    values: dict = {}
    said = []
    if delays["count"] >= LEAST_MEASURES:
        values["signal_delay_seconds"] = round(max(0.0, delays["median"]), 1)
    else:
        said.append(f"The signal's delay keeps the backtest's {template.get('signal_delay_seconds')} s: "
                    f"{delays['count']} of {LEAST_MEASURES} live entries measured")
    if samples_missing:
        said.append(f"The fills stay {template.get('execution', 'fixed')}: too few of WATA's orders were measured "
                    f"({', '.join(samples_missing)})")
    else:
        values["execution"] = "measured"
    if balance is not None and balance > 0:
        values["capital"] = round(balance, 2)
    else:
        said.append(f"The account stays the backtest's {template.get('capital')} €: WATA's is not known")
    return {
        "values": values,
        "template": {name: template.get(name) for name in CALIBRATED_SETTINGS},
        "measured": {
            "signal_delay_seconds": delays,
            "entry_slippage_percent": _figures([trade.get("entry_slippage_percent") for trade in live_trades]),
            "exit_slippage_percent": _figures([trade.get("exit_slippage_percent") for trade in live_trades]),
            "trades": len(live_trades),
        },
        "said": said,
    }


def changes_of(found: dict) -> dict:
    """A calibration's values as the settings a run changes, only those that differ from its template's."""
    return {name: value for name, value in (found.get("values") or {}).items()
            if (found.get("template") or {}).get(name) != value}
