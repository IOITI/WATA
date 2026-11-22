"""A signal rule's days set side by side (src/admin/live_check.py): keys, tracks, pairing, the bridge, the checks."""

from datetime import date, datetime, timezone

import pytest

from src.admin.live_check import (
    BACKTEST, CALIBRATED, LIVE, PAPER, bridge, calibration, changes_of, day_comparison, gap_closed, key_of, live_fate,
    live_track, pair, run_track, same_strategy, same_turbo_split, summary, why_not_raised,
)

DAY = date(2026, 11, 24)


def utc(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, 24, hour, minute, second, tzinfo=timezone.utc)


def text(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def run_signal(number: int, at: datetime, title: str, action: str, *, fate="executed", reason=None, trade_id=None) -> dict:
    """A signal as a run's signals.json keeps it: a Pine replay's and a paper run's alike."""
    return {
        "id": number, "day": DAY.isoformat(), "source": "pine", "action": action, "indice": "us100", "confidence": None,
        "arrived_at": text(at), "decided_at": text(at), "fate": fate, "rule": None, "reason": reason,
        "trade_id": trade_id, "closed_trade_ids": [], "candidates": None,
        "bar_time": text(at), "bar_closed_at": text(at), "alert": {"type": "alertcondition", "title": title, "message": ""},
    }


def run_trade(number: int, signal_id: int, *, uic=62288559, entry=7.0, exit_=7.21, result=None, reason="Daily profit target") -> dict:
    performance = round((exit_ / entry - 1) * 100, 2) if result is None else result
    return {
        "id": number, "day": DAY.isoformat(), "direction": "long", "signal_id": signal_id,
        "turbo": {"uic": uic, "description": f"NASDAQ 100 Turbo {uic}"},
        "entry": {"decided_at": text(utc(9, 0, 3)), "filled_at": text(utc(9, 0, 4)), "price": entry},
        "exit": {"filled_at": text(utc(9, 20)), "bid": exit_, "reason": f"Performance ({reason})", "group": None},
        "units": 3, "performance_percent": performance, "max_performance_percent": 4.0, "min_performance_percent": -1.0,
        "flags": [],
    }


def engine_row(number: int, at: datetime, title: str, action: str, *, mode="live", outcome="sent", trader=None,
               detail=None) -> dict:
    """A row of ta_signal as AdminQueries.rule_signals reads it: datetimes, and what the trader made of it."""
    return {
        "id": number, "rule_id": "f13986be5e9b", "bar_time": at, "bar_closed_at": at.replace(second=10),
        "alert_key": f"alertcondition:{title}", "alert_title": title, "action": action, "mode": mode,
        "raised_at": at.replace(second=11), "outcome": outcome, "sent_at": at.replace(second=12),
        "signal_uuid": f"uuid-{number}" if outcome == "sent" else None, "detail": detail, "trader": trader,
    }


def position(position_id: str, *, uic=62288559, entry=7.05, exit_=7.18, result=None) -> dict:
    performance = round((exit_ / entry - 1) * 100, 2) if result is None else result
    return {
        "position_id": position_id, "action": "long", "instrument_uic": uic, "instrument_name": f"Turbo {uic}",
        "execution_time_open": utc(9, 0, 6), "execution_time_close": utc(9, 21),
        "position_open_price": entry, "position_close_price": exit_, "position_total_performance_percent": performance,
        "position_max_performance_percent": 3.8, "position_min_performance_percent": -1.2,
        "position_close_reason": "Performance (Daily profit target (3.0%) potentially met (3.1%))",
    }


# ── keys and tracks ──

def test_a_signal_has_one_key_whichever_path_it_took():
    at = utc(9, 0)
    replayed = run_signal(1, at, "Enter Long", "long")
    raised = engine_row(1, at, "Enter Long", "long")

    assert key_of(replayed["bar_time"], replayed["alert"]["title"]) == key_of(raised["bar_time"], raised["alert_title"])
    assert key_of(at, "Enter Long") != key_of(at, "Resume Long")
    assert key_of(None, "Enter Long") is None and key_of(at, None) is None


def test_a_runs_track_keys_its_signals_and_trades_and_leaves_the_scheduled_closes_out():
    signals = [
        run_signal(1, utc(9, 0), "Enter Long", "long", trade_id=1),
        run_signal(2, utc(9, 30), "Global Exit", "close-position", fate="closed"),
        # The scheduler's close of the day: no bar, no alert.
        {"id": 3, "action": "close-position", "source": "schedule", "fate": "ignored"},
        run_signal(4, utc(10, 0), "In Long", "ignore", fate="not_a_signal"),
    ]
    track = run_track(signals, [run_trade(1, 1)])

    assert sorted(track["signals"]) == [key_of(utc(9, 0), "Enter Long"), key_of(utc(9, 30), "Global Exit")]
    assert list(track["trades"]) == [key_of(utc(9, 0), "Enter Long")]
    trade = track["trades"][key_of(utc(9, 0), "Enter Long")]
    assert trade["exit_price"] == 7.21 and trade["exit_group"] == "Daily profit target" and trade["turbo"]["uic"] == 62288559


def test_what_became_of_a_signal_raised_live():
    assert live_fate(engine_row(1, utc(9, 0), "Enter Long", "long", mode="shadow", outcome="shadow")) == ("shadow", None)
    assert live_fate(engine_row(2, utc(9, 0), "Enter Long", "long", outcome="refused", detail="Not alone on us100")) == \
        ("not_sent", "Not alone on us100")
    assert live_fate(engine_row(3, utc(9, 0), "Enter Long", "long", trader={"outcome": "refused", "reason": "Entries paused",
                                                                           "decided_at": utc(9, 0, 13)})) == \
        ("refused", "Entries paused")
    assert live_fate(engine_row(4, utc(9, 0), "Enter Long", "long"))[0] == "not_journaled"


def test_a_live_track_reads_the_position_and_what_the_ledger_measured_of_it():
    rows = [engine_row(1, utc(9, 0), "Enter Long", "long",
                       trader={"outcome": "executed", "reason": None, "decided_at": utc(9, 0, 20), "position_id": "P1"})]
    ledger = [{"position_id": "P1", "price_decided": 7.2, "ask_decided": 7.0, "signal_to_received": 2.1,
               "received_to_order": 1.4, "entry_slippage_percent": 0.7, "exit_slippage_percent": 0.28}]
    track = live_track(rows, [position("P1")], ledger)

    key = key_of(utc(9, 0), "Enter Long")
    assert track["signals"][key]["fate"] == "executed"
    assert track["signals"][key]["engine"]["raised_seconds"] == 1.0
    trade = track["trades"][key]
    assert trade["decided_price"] == 7.2 and trade["order_seconds"] == 3.5 and trade["entry_price"] == 7.05


# ── pairing ──

def _tracks(**named) -> dict:
    return {name: {"signals": found.get("signals", {}), "trades": found.get("trades", {}), "unkeyed": []}
            for name, found in named.items()}


def test_a_signal_the_engine_raised_at_another_bar_is_paired_within_the_tolerance_and_said():
    backtest = run_track([run_signal(1, utc(9, 0), "Enter Long", "long")], [])
    live = live_track([engine_row(1, utc(9, 0, 50), "Enter Long", "long")], [], [])
    rows = pair({BACKTEST: backtest, LIVE: live}, 300)

    assert len(rows) == 1
    assert rows[0]["near"] == {"key": key_of(utc(9, 0, 50), "Enter Long"), "seconds": 50}
    apart = pair({BACKTEST: backtest, LIVE: live}, 30)
    assert len(apart) == 2 and all(row["near"] is None for row in apart)


def test_signals_of_other_actions_are_never_paired():
    backtest = run_track([run_signal(1, utc(9, 0), "Enter Long", "long")], [])
    live = live_track([engine_row(1, utc(9, 0, 50), "Enter Short", "short")], [], [])

    assert len(pair({BACKTEST: backtest, LIVE: live}, 300)) == 2


# ── the bridge ──

def _day():
    """
    Seven entries: the same signal on the same turbo everywhere (with the price the live sale was decided on), one
    live on another turbo, one the engine never raised, one only the engine raised, two paper refused that live
    traded, and one live refused.
    """
    keys = {name: utc(9 + number, 0) for number, name in enumerate(
        ["same", "other_turbo", "backtest_only", "engine_only", "paper_refused", "live_refused", "live_only"])}
    backtest_signals, backtest_trades, paper_signals, paper_trades, rows, positions, ledger = [], [], [], [], [], [], []
    number = 0
    for name, at in keys.items():
        number += 1
        if name != "engine_only":
            backtest_signals.append(run_signal(number, at, "Enter Long", "long"))
            backtest_trades.append(run_trade(number, number, entry=7.0, exit_=7.2))
        if name == "backtest_only":
            continue
        paper_fate = "refused" if name in ("paper_refused", "live_only") else "executed"
        paper_signals.append(run_signal(number, at, "Enter Long", "long", fate=paper_fate))
        if paper_fate == "executed":
            paper_trades.append(run_trade(number, number, entry=7.02, exit_=7.25))
        trader = {"outcome": "refused", "reason": "Entries paused", "decided_at": at, "position_id": None} \
            if name == "live_refused" else {"outcome": "executed", "reason": None, "decided_at": at, "position_id": f"P{number}"}
        rows.append(engine_row(number, at, "Enter Long", "long", trader=trader))
        if trader["position_id"]:
            uic = 61111111 if name == "other_turbo" else 62288559
            positions.append(position(f"P{number}", uic=uic, entry=7.06, exit_=7.19))
            ledger.append({"position_id": f"P{number}", "price_decided": 7.23})
    return {
        BACKTEST: run_track(backtest_signals, backtest_trades),
        PAPER: run_track(paper_signals, paper_trades),
        LIVE: live_track(rows, positions, ledger),
    }


def _points(segment: dict) -> dict:
    return {bucket["id"]: bucket["points"] for bucket in segment["buckets"]}


def test_the_bridge_adds_up_exactly_from_the_backtest_to_paper_and_from_paper_to_live():
    found = bridge(pair(_day(), 300), {BACKTEST, PAPER, LIVE})

    for segment in (found["to_paper"], found["to_live"]):
        moved = sum(bucket["points"] for bucket in segment["buckets"])
        assert segment["start"]["sum"] + moved == pytest.approx(segment["end"]["sum"], abs=1e-6)


def test_each_trade_lands_in_its_bucket():
    found = bridge(pair(_day(), 300), {BACKTEST, PAPER, LIVE})
    to_paper, to_live = found["to_paper"], found["to_live"]

    assert {bucket["id"]: bucket["trades"] for bucket in to_paper["buckets"]} == {
        "not_raised_live": 1, "raised_only_live": 1, "fate_differs": 2, "same_signal": 3,
    }
    assert {bucket["id"]: bucket["trades"] for bucket in to_live["buckets"]} == {
        "not_traded_live": 1, "traded_live_only": 2, "other_turbo": 1,
        "entry_price": 2, "exit_moment": 2, "exit_fill": 2,
    }
    assert to_paper["from"] == BACKTEST and to_live["to"] == LIVE


def test_without_live_that_day_only_the_signals_are_bridged():
    tracks = _day()
    tracks.pop(LIVE)

    found = bridge(pair(tracks, 300), {BACKTEST, PAPER})
    assert found["to_paper"] is not None and found["to_live"] is None and found["calibrated"] is None


def test_the_same_turbo_split_adds_up_and_says_what_it_does_not_know():
    paper = {"entry_price": 7.0, "exit_price": 7.21, "result": 3.0}
    live = {"entry_price": 7.07, "exit_price": 7.15, "result": 1.13, "decided_price": 7.19}

    parts = dict(same_turbo_split(paper, live))
    assert set(parts) == {"entry_price", "exit_moment", "exit_fill"}
    assert sum(parts.values()) == pytest.approx(live["result"] - paper["result"])
    assert parts["entry_price"] < 0 and parts["exit_moment"] < 0 and parts["exit_fill"] < 0

    assert set(dict(same_turbo_split(paper, {**live, "decided_price": None}))) == {"entry_price", "exit"}
    assert dict(same_turbo_split({**paper, "entry_price": None}, live)) == {"unpriced": pytest.approx(-1.87)}


def test_a_day_says_each_signal_and_why_the_engine_did_not_raise_one():
    tracks = _day()
    started = utc(10, 30).timestamp()
    found = day_comparison(DAY, tracks, 300, engine_starts=[started], kept_since=utc(0, 0).timestamp())

    alone = next(signal for signal in found["signals"] if signal["key"] == key_of(utc(11, 0), "Enter Long"))
    assert alone["why_not_raised"].startswith("Not known: the engine ran then")
    before_start = why_not_raised(text(utc(10, 0, 10)), [started], utc(0, 0).timestamp())
    assert before_start.startswith("The engine was not running: it started at 10:30 UTC")
    assert why_not_raised(text(utc(10, 0)), [], None).startswith("Not known: the engine's ticks")
    assert found["figures"][LIVE]["trades"] == 5 and found["figures"][BACKTEST]["trades"] == 6
    assert found["agreement"] == {"same_bar": 5, "other_bar": 0, "not_replayed": 1, "not_raised": 1}
    assert [trade["same_turbo"] for trade in found["trades"] if trade.get(LIVE) and trade.get(PAPER)] == [True, False, True]


def test_the_days_together_sum_each_segment_and_say_how_much_calibration_closes():
    tracks = _day()
    tracks[CALIBRATED] = tracks[BACKTEST]
    days = [day_comparison(DAY, tracks, 300), day_comparison(date(2026, 11, 25), tracks, 300)]
    found = summary(days)

    assert found["tracks"][LIVE]["days"] == 2 and found["tracks"][LIVE]["trades"] == 10
    assert found["to_live"]["days"] == 2 and found["to_paper"]["days"] == 2
    moved = sum(bucket["points"] for bucket in found["to_live"]["buckets"])
    assert found["to_live"]["start"]["sum"] + moved == pytest.approx(found["to_live"]["end"]["sum"], abs=0.01)
    assert found["agreement"]["raised"] == 12 and found["agreement"]["same_bar_percent"] == pytest.approx(83.3)
    # The calibrated run is the backtest here: it closes nothing of the gap.
    assert found["calibrated"]["closed_percent"] == 0.0


def test_the_share_of_the_gap_closed():
    assert gap_closed(10.0, 6.0, 2.0) == 50.0
    assert gap_closed(10.0, 2.0, 2.0) == 100.0
    assert gap_closed(10.0, 12.0, 2.0) == -25.0
    assert gap_closed(2.0, 3.0, 2.0) is None


# ── the same strategy ──

RULE = {
    "id": "f13986be5e9b", "asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "range", "ticks": 60},
    "script": {"id": "de1ec9a28466", "version": 1, "sha256": "ab" * 32},
    "libraries": [{"name": "KernelFunctions", "id": "1a2b3c4d5e6f", "version": 1, "sha256": "cd" * 32}],
    "inputs": [{"id": "in_18", "value": 1}, {"id": "in_23", "value": 20}, {"id": "in_36", "value": 1.25}],
    "alerts": [{"title": "Enter Long", "action": "long"}, {"title": "Global Exit", "action": "close-position"}],
    "history_days": 1,
}


def _request(**changed) -> dict:
    rule = {**RULE, "id": "replay00", "script": {"id": "8655b16df3ef", "version": 4, "sha256": "ab" * 32},
            "inputs": [{"id": "in_18", "value": 1.0}, {"id": "in_36", "value": 1.25}]}
    rule.update(changed)
    return {"kind": "run", "source": {"kind": "pine", "rule": rule}}


def test_a_copy_of_the_script_under_another_name_is_the_same_strategy():
    found = same_strategy(RULE, _request(), {"history_days": 1}, 1)

    assert found["same"] is True
    states = {check["id"]: check["state"] for check in found["checks"]}
    assert states == {"script": "pass", "libraries": "pass", "chart": "pass", "alerts": "pass", "history": "pass",
                      "inputs": "note"}
    inputs = next(check for check in found["checks"] if check["id"] == "inputs")
    assert "set on the rule only: in_23 20" in inputs["detail"]


@pytest.mark.parametrize("changed", [
    {"script": {"id": "8655b16df3ef", "version": 3, "sha256": "ef" * 32}},
    {"bars": {"type": "range", "ticks": 80}},
    {"inputs": [{"id": "in_18", "value": 2}, {"id": "in_36", "value": 1.25}]},
    {"alerts": [{"title": "Enter Long", "action": "short"}]},
    {"libraries": [{"name": "KernelFunctions", "id": "1a2b3c4d5e6f", "version": 2, "sha256": "ee" * 32}]},
])
def test_another_strategy_is_said_so(changed):
    assert same_strategy(RULE, _request(**changed), {"history_days": 1}, 1)["same"] is False


def test_other_days_of_history_and_another_source_are_another_strategy():
    assert same_strategy(RULE, _request(), {"history_days": 3}, 1)["same"] is False
    assert same_strategy(RULE, {"source": {"kind": "ta_signals"}}, {}, 1)["same"] is False


# ── the calibrated run ──

def test_the_signal_delay_is_measured_from_ten_live_entries():
    template = {"signal_delay_seconds": 3.0, "execution": "fixed", "capital": 100.0}
    few = calibration([{"order_seconds": 4.0}] * 9, samples_missing=[], balance=21.82, template=template)

    assert "signal_delay_seconds" not in few["values"]
    assert few["said"][0].startswith("The signal's delay keeps the backtest's 3.0 s: 9 of 10")
    assert few["values"] == {"execution": "measured", "capital": 21.82}

    enough = calibration([{"order_seconds": seconds} for seconds in (3.1, 3.5, 4.0, 4.2, 4.4, 4.8, 5.0, 5.5, 6.0, 9.0)],
                         samples_missing=["3 sale prices"], balance=None, template=template)
    assert enough["values"] == {"signal_delay_seconds": 4.6}
    assert enough["measured"]["signal_delay_seconds"]["count"] == 10
    assert any("3 sale prices" in said for said in enough["said"])
    assert any("100.0 €" in said for said in enough["said"])


def test_a_calibration_changes_only_what_differs_from_its_template():
    found = {"values": {"signal_delay_seconds": 3.0, "execution": "measured", "capital": 21.82},
             "template": {"signal_delay_seconds": 3.0, "execution": "fixed", "capital": 100.0}}

    assert changes_of(found) == {"execution": "measured", "capital": 21.82}
