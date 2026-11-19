"""
Forward checks (src/admin/forward_checks.py): a kept conclusion's candidate judged each evening on the days after it,
which no run of its family had seen. When one starts or is refused, over which days its run is asked and when, how it
is judged, said and ended, and what the journal keeps of it.
"""

import asyncio
import functools
import json
import sys
import types
from datetime import date, datetime, timezone

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.admin import edge_watch as ew  # noqa: E402
from src.admin import forward_checks as fc  # noqa: E402
from src.admin.errors import NotPossible  # noqa: E402
from src.admin.forward_checks import ForwardChecks, UnknownCheck, said_line  # noqa: E402
from src.admin.processes import DONE, FAILED, INTERRUPTED  # noqa: E402
from tests.test_admin_backtests import (  # noqa: E402, F401
    FakeLauncher,
    Ticking,
    config,
    folder_of,
    pine,
    scripts,
    service,
)

# Thursday 22 October 2026, 23:00 in Paris: the example's market (8 to 22) closed, the day's hours over.
THURSDAY_EVENING = datetime(2026, 10, 22, 21, 0, tzinfo=timezone.utc).timestamp()
FRIDAY_EVENING = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc).timestamp()
# Thursday 22 October, 15:00 in Paris: the market open.
THURSDAY_AFTERNOON = datetime(2026, 10, 22, 13, 0, tzinfo=timezone.utc).timestamp()
DAYS = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09",
        "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
# Two trades a day, each day its own: a mean of +0.86 % a trade; random entries lost about 0.5 % a trade.
RESULTS = [3.5, -1.2, 2.1, -0.8, 4.0, -2.0, 1.5, 0.2, -1.5, 2.8]
WINNING = [3.0, -1.0]
RANDOM = {"repetitions": 20, "totals": [-10.0 + index * 0.1 for index in range(20)], "trades": [20] * 20}


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def fewer_paths(monkeypatch):
    # Edge Watch's model drawn on fewer paths: the same shape, in a tenth of the time.
    monkeypatch.setattr(ew, "build_model", functools.partial(ew.build_model, paths=300, h1_paths=100))


def finished(tmp_path, run_id: str, days: list[str], per_day: list[float] | None = None, *,
             baseline: dict | None = None, state: str = DONE) -> None:
    """
    A run's results written as a run writes them: its days, its trades, its random entries, its summary. Without
    ``per_day``, each day trades two of ``RESULTS`` in turn.
    """
    folder = folder_of(tmp_path, run_id)
    trades, number = [], 0
    for place, day in enumerate(days):
        results = per_day if per_day is not None else [RESULTS[(2 * place) % 10], RESULTS[(2 * place + 3) % 10]]
        for index, result in enumerate(results):
            number += 1
            trades.append({"id": number, "day": day, "performance_percent": result,
                           "exit": {"filled_at": f"{day}T{10 + index:02d}:00:00Z"}, "entry": {}})
    (folder / "trades.json").write_text(json.dumps(trades))
    by_day = {day: [trade["performance_percent"] for trade in trades if trade["day"] == day] for day in days}
    (folder / "days.json").write_text(json.dumps(
        [{"day": day, "status": "tested", "day_percent": sum(by_day[day]), "trades": len(by_day[day])} for day in days]))
    if baseline is not None:
        (folder / "baseline.json").write_text(json.dumps(baseline))
    (folder / "summary.json").write_text(json.dumps({
        "made_with": {"code": "x", "version": "0.95.0"}, "figures": {"total_percent": 2.0, "trades": len(trades), "days": len(days)},
        "days": {"tested": len(days)},
        "curve": [{"day": day, "percent": sum(by_day[day]), "cumulative": 0} for day in days],
    }))
    (folder / "status.json").write_text(json.dumps({"state": state, "started_at": "2026-10-19T20:00:00Z",
                                                    "finished_at": "2026-10-19T20:05:00Z"}))


def asked(setup, body: dict, experiment: str) -> str:
    async def ask():
        return setup.backtests.create(body, made_by="claude", made_via="agent:abc123", experiment=experiment)

    run_id = asyncio.run(ask())["id"]
    setup.backtests._waiting.clear()
    setup.journal.record_run(experiment, run_id, kind="run", trials=1)
    return run_id


@pytest.fixture
def setup(tmp_path, scripts, config):  # noqa: F811
    backtests = service(tmp_path, scripts, config, FakeLauncher(ends_with=None), clock=Ticking())
    clock, said, published = Clock(THURSDAY_EVENING), [], []

    async def notify(text):
        said.append(text)

    checks = ForwardChecks(root=str(tmp_path / "backtests"), backtests=backtests, queries=None, config_path=config,
                           notify=notify, publish=lambda event, **data: published.append((event, data)), clock=clock)
    journal = backtests.journal
    experiment = journal.new("Open block", "The open's whipsaw loses", "kernel", made_via="agent:abc123")["experiment"]
    setup = types.SimpleNamespace(tmp_path=tmp_path, backtests=backtests, checks=checks, clock=clock, said=said,
                                  published=published, journal=journal, experiment=experiment, scripts=scripts)
    setup.candidate = asked(setup, pine(scripts, **{"from": "2026-10-05", "to": "2026-10-16"}), experiment)
    finished(tmp_path, setup.candidate, DAYS, baseline=RANDOM)
    journal.conclude(experiment, "kept", "Rests on 10 days", candidate=setup.candidate)
    return setup


def forward_runs(setup) -> list[str]:
    return [run_id for run_id in setup.backtests.run_ids() if setup.backtests.request_of(run_id).get("forward_of")]


def caught_up(setup, now: float | None = None) -> int:
    return asyncio.run(setup.checks.catch_up(now))


# ── starting one ──


def test_a_kept_candidate_is_judged_from_the_first_weekday_no_run_of_its_family_had_seen(setup):
    # Another run of the family went to Tuesday the 20th: the check starts on Wednesday the 21st.
    other = asked(setup, pine(setup.scripts, **{"from": "2026-10-12", "to": "2026-10-20"}), setup.experiment)
    finished(setup.tmp_path, other, ["2026-10-19", "2026-10-20"], WINNING)

    started = setup.checks.start(setup.experiment, setup.candidate, by="agent:abc123")

    assert fc.CHECK_ID.fullmatch(started["id"])
    assert (started["state"], started["start"], started["candidate"], started["by"]) == (
        "active", "2026-10-21", setup.candidate, "agent:abc123")
    forward = setup.journal.get(setup.experiment)["forward"]
    assert (forward["state"], forward["check"], forward["start"]) == ("started", started["id"], "2026-10-21")
    assert setup.published[-1][0] == "forward_check_changed"


def test_a_candidate_whose_family_saw_nothing_after_it_starts_the_next_weekday(setup):
    started = setup.checks.start(setup.experiment, setup.candidate)

    # Its last day was Friday the 16th.
    assert started["start"] == "2026-10-19"


@pytest.mark.parametrize("why", ["not concluded", "not its run", "a stress", "not finished", "too few days",
                                 "no edge", "twice"])
def test_a_forward_check_is_refused_in_words(setup, why):
    experiment, candidate = setup.experiment, setup.candidate
    if why == "not concluded":
        experiment = setup.journal.new("B", "b", "kernel")["experiment"]
        candidate = asked(setup, pine(setup.scripts), experiment)
        finished(setup.tmp_path, candidate, DAYS, baseline=RANDOM)
    elif why == "not its run":
        other = setup.journal.new("B", "b", "kernel")["experiment"]
        candidate = asked(setup, pine(setup.scripts), other)
        finished(setup.tmp_path, candidate, DAYS, baseline=RANDOM)
    elif why == "a stress":
        folder = folder_of(setup.tmp_path, candidate)
        request = json.loads((folder / "request.json").read_text())
        (folder / "request.json").write_text(json.dumps({**request, "stress_of": "20261001-100000-aaaaaa"}))
    elif why == "not finished":
        finished(setup.tmp_path, candidate, DAYS, baseline=RANDOM, state=FAILED)
    elif why == "too few days":
        finished(setup.tmp_path, candidate, DAYS[:4], baseline=RANDOM)
    elif why == "no edge":
        finished(setup.tmp_path, candidate, DAYS, baseline={"repetitions": 2, "totals": [80.0, 90.0], "trades": [20, 20]})
    elif why == "twice":
        setup.checks.start(experiment, candidate)

    with pytest.raises(NotPossible) as refused:
        setup.checks.start(experiment, candidate)

    said = {"not concluded": "concluded", "not its run": "not one of", "a stress": "stress", "not finished": "finished",
            "too few days": "cannot be judged", "no edge": "no more than random", "twice": "already"}[why]
    assert said in str(refused.value)


def test_ten_checks_run_at_once_at_most(setup, monkeypatch):
    monkeypatch.setattr(fc, "MAX_ACTIVE", 1)
    other = setup.journal.new("B", "b", "kernel")["experiment"]
    candidate = asked(setup, pine(setup.scripts), other)
    finished(setup.tmp_path, candidate, DAYS, baseline=RANDOM)
    setup.journal.conclude(other, "kept", "n", candidate=candidate)
    setup.checks.start(setup.experiment, setup.candidate)

    with pytest.raises(NotPossible, match="1 forward checks run already"):
        setup.checks.start(other, candidate)


# ── asking its run ──


def test_its_run_is_the_candidate_again_over_the_days_after_it_without_random_entries(setup):
    started = setup.checks.start(setup.experiment, setup.candidate)

    assert caught_up(setup) == 1

    [run_id] = forward_runs(setup)
    request = setup.backtests.request_of(run_id)
    assert (request["from"], request["to"]) == ("2026-10-19", "2026-10-22")
    assert request["forward"] == {"check": started["id"]} and request["forward_of"] == setup.candidate
    assert request["baseline"]["repetitions"] == 0
    assert "nightly" not in request and "experiment" not in request and "made_by" not in request
    # Its candidate's configuration, never a nightly template, never judged nor made again.
    assert setup.backtests.configuration_of(run_id) == setup.backtests.configuration_of(setup.candidate)
    # Asked once for those days.
    setup.backtests._waiting.clear()
    assert caught_up(setup) == 0


@pytest.mark.parametrize("held_back", ["market open", "a run waits", "nightly due", "lock box ahead"])
def test_its_run_waits_for_the_close_an_empty_line_the_nightly_runs_and_the_lock_box(setup, held_back):
    setup.checks.start(setup.experiment, setup.candidate)
    now = THURSDAY_EVENING
    if held_back == "market open":
        now = THURSDAY_AFTERNOON
    elif held_back == "a run waits":
        setup.backtests._waiting.append("20261022-100000-aaaaaa")
    elif held_back == "nightly due":
        setup.checks._nightly = types.SimpleNamespace(due=lambda now: ["a configuration"])
    elif held_back == "lock box ahead":
        setup.journal.set_lockbox(date(2026, 10, 19))

    assert caught_up(setup, now) == 0
    assert forward_runs(setup) == []


def test_lock_box_days_are_left_out_of_its_window(setup):
    setup.checks.start(setup.experiment, setup.candidate)
    setup.journal.set_lockbox(date(2026, 10, 22), date(2026, 10, 30))

    assert caught_up(setup) == 1

    [run_id] = forward_runs(setup)
    assert setup.backtests.request_of(run_id)["to"] == "2026-10-21"


def test_a_run_cut_by_a_restart_is_asked_again(setup):
    setup.checks.start(setup.experiment, setup.candidate)
    caught_up(setup)
    [run_id] = forward_runs(setup)
    setup.backtests._waiting.clear()
    (folder_of(setup.tmp_path, run_id) / "status.json").write_text(json.dumps({"state": INTERRUPTED}))

    assert caught_up(setup) == 1
    assert len(forward_runs(setup)) == 2


# ── judging it ──


def settle(setup) -> None:
    """Settled on the event loop, as the admin settles it: what it says on Telegram is said."""
    async def on_the_loop():
        setup.checks.settle()
        await asyncio.gather(*list(setup.checks._telling))

    asyncio.run(on_the_loop())


def settled(setup, per_day: list[float] | None, days: list[str]) -> dict:
    [run_id] = [run for run in forward_runs(setup) if setup.backtests.state(run)["state"] != DONE][:1] or forward_runs(setup)[:1]
    setup.backtests._waiting.clear()
    finished(setup.tmp_path, run_id, days, per_day)
    settle(setup)
    return setup.checks.check(setup.checks.checks()[0]["id"])


def test_trades_like_its_candidates_are_as_expected_and_said_nowhere(setup):
    setup.checks.start(setup.experiment, setup.candidate)
    caught_up(setup)

    # The candidate's own kind of days again, a week of them.
    found = settled(setup, None, ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26"])

    judged = found["judgement"]
    assert judged["n"] == 12 and judged["verdict"] in (ew.AS_EXPECTED, ew.BETTER_THAN_EXPECTED)
    assert found["state"] == "active" and setup.said == []
    assert found["history"][0]["n"] == 12 and found["path"][-1]["n"] == 12
    assert found["cone"] and found["model"]["usable"]
    assert setup.checks.of_experiments()[setup.experiment]["trades"] == 12


def test_a_candidate_whose_later_trades_lose_is_broken_said_and_ended(setup):
    started = setup.checks.start(setup.experiment, setup.candidate)
    caught_up(setup)

    found = settled(setup, [-5.0] * 4, ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"])

    assert found["judgement"]["verdict"] == ew.BROKEN
    assert (found["state"], found["why"]) == ("ended", "broken")
    assert any("broken after 16 trade(s)" in line for line in setup.said)
    assert any("ended (judged broken)" in line for line in setup.said)
    forward = setup.journal.get(setup.experiment)["forward"]
    assert (forward["state"], forward["check"], forward["verdict"], forward["why"]) == ("ended", started["id"], "broken", "broken")
    # Ended: its place is free, and nothing more is asked.
    assert setup.checks.active() == 0
    assert caught_up(setup, FRIDAY_EVENING) == 0


def test_the_run_before_is_let_go_once_the_next_is_settled(setup):
    setup.checks.start(setup.experiment, setup.candidate)
    caught_up(setup)
    settled(setup, WINNING, ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"])
    [first] = forward_runs(setup)

    assert caught_up(setup, FRIDAY_EVENING) == 1
    newer = [run for run in forward_runs(setup) if run != first][0]
    setup.backtests._waiting.clear()
    finished(setup.tmp_path, newer, ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"], WINNING)
    settle(setup)

    assert forward_runs(setup) == [newer]
    assert len(setup.checks.check(setup.checks.checks()[0]["id"])["history"]) == 2


def test_a_check_ends_after_its_days(setup, monkeypatch):
    monkeypatch.setattr(fc, "MOST_TESTED_DAYS", 3)
    setup.checks.start(setup.experiment, setup.candidate)
    caught_up(setup)

    found = settled(setup, WINNING, ["2026-10-19", "2026-10-20", "2026-10-21"])

    assert (found["state"], found["why"]) == ("ended", "days")


def test_a_check_whose_candidate_is_gone_ends(setup):
    setup.checks.start(setup.experiment, setup.candidate)
    setup.backtests.delete(setup.candidate)

    settle(setup)

    [entry] = setup.checks.checks()
    assert (entry["state"], entry["why"]) == ("ended", "gone")


def test_the_operator_stops_one(setup):
    started = setup.checks.start(setup.experiment, setup.candidate)

    stopped = setup.checks.stop(started["id"])

    assert (stopped["state"], stopped["why"]) == ("ended", "stopped")
    with pytest.raises(NotPossible, match="ended already"):
        setup.checks.stop(started["id"])
    with pytest.raises(UnknownCheck):
        setup.checks.check("../etc")


def test_a_candidate_made_again_on_newer_code_is_followed(setup):
    setup.checks.start(setup.experiment, setup.candidate)
    remade = asked(setup, pine(setup.scripts, **{"from": "2026-10-05", "to": "2026-10-16"}), setup.experiment)
    finished(setup.tmp_path, remade, DAYS, baseline=RANDOM)
    (folder_of(setup.tmp_path, setup.candidate) / "remade.json").write_text(json.dumps({"by": remade}))

    caught_up(setup)

    [run_id] = forward_runs(setup)
    assert setup.backtests.request_of(run_id)["forward_of"] == remade
    assert setup.checks.checks()[0]["candidate"] == remade


@pytest.mark.parametrize("judgement, ended, said", [
    ({"verdict": "watch", "n": 12, "cumulative": -3.5, "reasons": ["the test is past half of its threshold"]}, None,
     "Forward check of 'Open block': watch after 12 trade(s), -3.50 % — the test is past half of its threshold."),
    ({"verdict": "as_expected", "n": 12, "cumulative": 4.0, "reasons": []}, None, None),
    ({"verdict": "as_expected", "n": 40, "cumulative": 9.1, "reasons": []}, "horizon",
     "Forward check of 'Open block' ended (past the trades its candidate was drawn for): as expected after 40 "
     "trade(s), +9.10 % on days no run had seen."),
])
def test_what_a_check_says_on_telegram(judgement, ended, said):
    assert said_line({"title": "Open block"}, judgement, ended=ended) == said
