"""
One signal's way to WATA and what it gave: ``GET /api/signals/{ref}``, the page of one signal.

What is read is what the services recorded, each on the server's clock but Saxo's fills: the TA engine's row of the
signal (``ta_signal``: its bar, the tick that raised it, the web server's answer) and the run that raised it
(``ta_rule_run``, kept ``monitoring_days``), the trader's journal row (``signal_journal``, the web server's moment of
reception to the millisecond) with TradingView's alert (``signal_alert_time``), the order it gave with what became of
it, the requests sent to Saxo for it, and the sales the trader sent while it handled it. Nothing of the trader's own
steps between a signal's reception and its order leaving is recorded: that stretch is one.

A signal is addressed by the ``signal_uuid`` the web server stored it under, or, for one the web server never
answered (refused by the engine, in shadow, or sent without an answer), by its row of the engine: ``engine-<id>``.
The ref is matched here before any query is built.
"""

import re

UUID_REF = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ENGINE_REF = re.compile(r"^engine-([1-9][0-9]{0,17})$")
# The start of the engine's journal text for a signal the web server took at a later attempt (SENT_AGAIN of
# src/ta_engine/engine.py, held to it by tests/test_admin_signal_trace.py).
SENT_AGAIN_START = "Sent at attempt "
_ATTEMPT = re.compile(re.escape(SENT_AGAIN_START) + r"([0-9]+)")
# Who sent the sales the trader made while it handled a signal (order_request.recorded_by): the trader handles
# one signal at a time, and the position monitor's sales, its retries of the trader's included, are its own.
TRADER = "trader"
# What a run of the engine left behind once it is pruned (trade.config.ta_engine.monitoring_days, 7 by default).
RUN_NOT_KEPT = "The engine keeps its runs' measures a few days (monitoring_days): this one is no longer kept"


def ref_of(ref: str) -> tuple[str, str | int] | None:
    """``("uuid", <signal_uuid>)`` or ``("engine", <ta_signal id>)`` for a ref of either form, None otherwise."""
    if not isinstance(ref, str):
        return None
    if UUID_REF.match(ref):
        return "uuid", ref
    found = ENGINE_REF.match(ref)
    if found:
        return "engine", int(found.group(1))
    return None


def ref_for(signal_uuid: str | None, ta_signal_id: int | None) -> str | None:
    """The address of a signal: its signal_uuid when the web server gave one, else its row of the engine."""
    if signal_uuid and UUID_REF.match(str(signal_uuid)):
        return str(signal_uuid)
    if ta_signal_id is not None:
        return f"engine-{int(ta_signal_id)}"
    return None


def attempts_of(detail) -> int:
    """How many times the engine sent it before the web server answered: 1 unless its journal says otherwise."""
    found = _ATTEMPT.search(str(detail or ""))
    return int(found.group(1)) if found else 1


def source_of(engine: dict | None) -> dict:
    """Where the signal came from: one of the TA engine's rules, or TradingView's alerts and the signals sent by hand,
    which the journal does not tell apart."""
    # Read here: the breakdowns' module brings the backtests' with it, which the Engine monitor's need not load.
    from src.admin.live_breakdowns import TRADINGVIEW_OR_HAND

    if engine:
        return {
            "kind": "engine", "label": f"Rule: {engine.get('rule_name') or engine.get('rule_id')}",
            "rule_id": engine.get("rule_id"), "rule_name": engine.get("rule_name"), "mode": engine.get("mode"),
        }
    return {"kind": "received", "label": TRADINGVIEW_OR_HAND, "rule_id": None, "rule_name": None, "mode": None}


def fate_of(engine: dict | None, journal: dict | None) -> dict:
    """What became of it: the trader's outcome and reason once it journaled the signal, else why it never did."""
    # Read here: the live check names each live signal by its ref (ref_for), so it imports this module.
    from src.admin.live_check import live_fate

    if engine:
        key, reason = live_fate({**engine, "trader": journal})
    elif journal:
        key, reason = str(journal.get("outcome") or "unknown"), journal.get("reason")
    else:
        key, reason = "unknown", None
    return {"key": key, "reason": reason}


def trace_answer(parts: dict) -> dict | None:
    """
    The answer of one signal from what ``AdminQueries.signal_trace`` read: None when neither the engine nor the trader
    knows of it. ``run`` is the engine's run that raised it; ``run_missing`` says why there is none for one of the
    engine's signals.
    """
    engine, journal = parts.get("engine"), parts.get("journal")
    if engine is None and journal is None:
        return None
    run = parts.get("run")
    signal_uuid = (journal or {}).get("signal_uuid") or (engine or {}).get("signal_uuid")
    action = (journal or {}).get("action") or (engine or {}).get("action")
    indice = (journal or {}).get("indice") or (engine or {}).get("indice")
    return {
        "ref": ref_for(signal_uuid, (engine or {}).get("id")),
        "signal_uuid": signal_uuid,
        "action": action,
        "indice": indice,
        "source": source_of(engine),
        "fate": fate_of(engine, journal),
        "engine": {**engine, "attempts": attempts_of(engine.get("detail"))} if engine else None,
        "run": run,
        "run_missing": RUN_NOT_KEPT if engine is not None and run is None else None,
        "journal": journal,
        "orders": parts.get("orders") or [],
        "requests": parts.get("requests") or [],
        "sold_for_it": parts.get("sold_for_it") or [],
    }
