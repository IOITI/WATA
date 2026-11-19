"""
A backtest configuration made again after each trading day (src/admin/backtest_nightly.py): when a nightly run is
asked and over which days, what it copies and what it reads again, which runs it lets go once done and which it
keeps, what it says, and the configurations as the Backtests page lists them.
"""

import asyncio
import json
import os
import sys
import types
from datetime import date, datetime, timezone

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.admin.backtest_configurations import UnknownConfiguration  # noqa: E402
from src.admin.backtest_nightly import (  # noqa: E402
    KEPT_FAVOURITE,
    KEPT_JUDGED,
    KEPT_REFERENCE,
    KEPT_VERDICT,
    WAITS_FAILED,
    WAITS_MARKET,
    NightlyBacktests,
    said_line,
)
from src.admin.errors import DatabaseUnavailable, InvalidRequest, LineFull, NotPossible  # noqa: E402
from src.admin.processes import CANCELLED, DONE, FAILED, INTERRUPTED, RUNNING  # noqa: E402
from tests.test_admin_backtests import (  # noqa: E402, F401
    JOURNAL,
    FakeLauncher,
    Ticking,
    config,
    folder_of,
    pine,
    run_folder,
    scripts,
    service,
    until,
)
from tests.test_admin_paper import Queries  # noqa: E402

# Tuesday 20 October 2026, 23:00 in Paris: the example's market (8 to 22) closed, the day's hours over.
TUESDAY_EVENING = datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc).timestamp()
WEDNESDAY_EVENING = datetime(2026, 10, 21, 21, 0, tzinfo=timezone.utc).timestamp()
THURSDAY_EVENING = datetime(2026, 10, 22, 21, 0, tzinfo=timezone.utc).timestamp()
# Wednesday 21 October, 15:00 in Paris: the market open.
WEDNESDAY_AFTERNOON = datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc).timestamp()
RULE_ID = "a00000000001"


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def made(tmp_path, backtests, body: dict, *, journal=None, name: str | None = None, state: str = DONE) -> str:
    """A run asked as the operator asks it, then ended as a summary says."""

    async def ask():
        return backtests.create(body, journal=journal)

    run_id = asyncio.run(ask())["id"]
    folder = folder_of(tmp_path, run_id)
    if state == DONE:
        (folder / "summary.json").write_text(json.dumps({
            "made_with": {"code": "x", "version": "0.93.1"}, "figures": {"total_percent": 2.0, "trades": 4},
            "against_random": {"beaten": 80.0}, "days": {"tested": 3}, "money": {"profit_loss": 20.0},
        }))
    (folder / "status.json").write_text(json.dumps({"state": state, "started_at": "2026-10-19T20:00:00Z",
                                                    "finished_at": "2026-10-19T20:05:00Z"}))
    backtests._waiting.clear()
    if name is not None:
        backtests.update(run_id, name=name)
    return run_id


def request_of(tmp_path, run_id: str) -> dict:
    return json.loads((folder_of(tmp_path, run_id) / "request.json").read_text())


@pytest.fixture
def setup(tmp_path, scripts, config):  # noqa: F811
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, scripts, config, launcher, clock=Ticking())
    clock, said = Clock(TUESDAY_EVENING), []

    async def notify(text):
        said.append(text)

    queries = Queries()
    nightly = NightlyBacktests(backtests=backtests, queries=queries, config_path=config, clock=clock, notify=notify,
                               verdict_runs=lambda: set())
    return types.SimpleNamespace(backtests=backtests, launcher=launcher, clock=clock, said=said, queries=queries,
                                 nightly=nightly)


def switched_on(setup, run_id: str, **nightly) -> str:
    configuration = setup.backtests.configuration_of(run_id)
    setup.nightly.update(configuration, nightly={"on": True, **nightly})
    return configuration


def in_the_loop(setup, scenario):
    """``scenario()`` beside the service's worker and with the nightly task's hooks, as in the admin."""

    async def both():
        worker = asyncio.create_task(setup.backtests.run())
        try:
            return await scenario()
        finally:
            worker.cancel()
            await setup.backtests.close()

    return asyncio.run(both())


# ── asked ──


def test_a_configuration_switched_on_is_made_again_to_the_day_just_over_with_what_it_had(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts, changes={"stoploss_percent": -10}),
                    name="Cross R60 (again) (again)")
    configuration = switched_on(setup, template)

    assert asyncio.run(setup.nightly.catch_up()) == 1

    new = next(run_id for run_id in setup.backtests.run_ids() if run_id != template)
    request = request_of(tmp_path, new)
    assert (request["from"], request["to"], request["name"]) == ("2026-10-13", "2026-10-20", "Cross R60")
    assert request["nightly"] == {"configuration": configuration}
    assert "favourite" not in request and "remade_from" not in request
    # Its settings and sources as the template resolved them: the configuration.
    for name in ("settings.json",):
        assert (folder_of(tmp_path, new) / name).read_text() == (folder_of(tmp_path, template) / name).read_text()
    assert sorted(os.listdir(folder_of(tmp_path, new) / "sources")) == sorted(os.listdir(folder_of(tmp_path, template) / "sources"))
    assert setup.backtests.configuration_of(new) == configuration
    last = setup.backtests.configurations.entry(configuration)["last"]
    assert (last["day"], last["run"], last["error"], last["settled"]) == ("2026-10-20", new, None, False)
    # Asked once: not again while it waits, nor for that day.
    assert asyncio.run(setup.nightly.catch_up()) == 0


def test_its_settings_stay_the_templates_whatever_the_live_configuration_says_now(tmp_path, scripts, config, setup):  # noqa: F811
    template = made(tmp_path, setup.backtests, pine(scripts))
    switched_on(setup, template)
    with open(config, encoding="utf-8") as config_file:
        live = json.load(config_file)
    for rule in live["trade"]["rules"]:
        if rule.get("rule_type") == "day_trading":
            rule["rule_config"]["percent_profit_wanted_per_days"] = 9.5
    with open(config, "w", encoding="utf-8") as config_file:
        json.dump(live, config_file)

    asyncio.run(setup.nightly.catch_up())

    new = next(run_id for run_id in setup.backtests.run_ids() if run_id != template)
    settings = json.loads((folder_of(tmp_path, new) / "settings.json").read_text())["settings"]
    assert settings["day_profit_target"] != 9.5


def test_nothing_is_asked_while_the_market_is_open_or_a_run_of_the_operator_waits(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts))
    switched_on(setup, template)

    setup.clock.now = WEDNESDAY_AFTERNOON
    assert asyncio.run(setup.nightly.catch_up()) == 0

    setup.clock.now = TUESDAY_EVENING

    async def waiting():
        setup.backtests.create(pine(scripts, name="Mine"))
        return await setup.nightly.catch_up()

    assert asyncio.run(waiting()) == 0
    setup.backtests._waiting.clear()
    assert asyncio.run(setup.nightly.catch_up()) == 1


def test_a_configuration_switched_off_or_covered_already_asks_nothing(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts))
    configuration = switched_on(setup, template)
    setup.nightly.update(configuration, nightly={"on": False})
    assert asyncio.run(setup.nightly.catch_up()) == 0

    setup.nightly.update(configuration, nightly={"on": True})
    # A run asked by hand covers tonight's days already.
    made(tmp_path, setup.backtests, pine(scripts, **{"from": "2026-10-13", "to": "2026-10-20"}))
    assert asyncio.run(setup.nightly.catch_up()) == 0


@pytest.mark.parametrize("state, again", [(FAILED, False), (CANCELLED, False), (INTERRUPTED, True), (RUNNING, True)])
def test_a_nightly_run_that_did_not_finish_is_asked_again_only_when_the_admin_cut_it(tmp_path, scripts, setup, state, again):
    template = made(tmp_path, setup.backtests, pine(scripts))
    configuration = switched_on(setup, template)
    asyncio.run(setup.nightly.catch_up())
    first = setup.backtests.configurations.entry(configuration)["last"]["run"]
    (folder_of(tmp_path, first) / "status.json").write_text(json.dumps({"state": state}))
    # Not in the line any more: the admin stopped, or it ended.
    setup.backtests._waiting.clear()
    setup.nightly.settle()

    assert asyncio.run(setup.nightly.catch_up()) == (1 if again else 0)
    # The next evening, whatever happened to it.
    setup.clock.now = WEDNESDAY_EVENING
    setup.backtests._waiting.clear()
    if again:
        newest = setup.backtests.configurations.entry(configuration)["last"]["run"]
        (folder_of(tmp_path, newest) / "status.json").write_text(json.dumps({"state": FAILED}))
        setup.nightly.settle()
    assert asyncio.run(setup.nightly.catch_up()) == 1


def test_one_nightly_run_at_a_time_the_oldest_asked_first(tmp_path, scripts, setup):
    one = made(tmp_path, setup.backtests, pine(scripts, changes={"stoploss_percent": -10}))
    two = made(tmp_path, setup.backtests, pine(scripts, changes={"stoploss_percent": -11}))
    first, second = switched_on(setup, one), switched_on(setup, two)
    setup.backtests.configurations.update(first, lambda entry: entry.update(last={"day": "2026-10-19", "run": None,
                                                                                    "asked_at": "2026-10-19T21:00:00Z"}))

    assert asyncio.run(setup.nightly.catch_up()) == 1
    asked = setup.backtests.configurations.entry(second)["last"]
    assert asked["day"] == "2026-10-20" and asked["run"]
    # The other waits for it.
    assert asyncio.run(setup.nightly.catch_up()) == 0
    assert setup.backtests.configurations.entry(first)["last"]["day"] == "2026-10-19"


def test_a_full_line_waits_and_keeps_nothing(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts))
    configuration = switched_on(setup, template)
    setup.backtests.max_queued = 0

    assert asyncio.run(setup.nightly.catch_up()) == 0
    assert "last" not in setup.backtests.configurations.entry(configuration)


# ── the days ──


def test_its_window_grows_from_the_day_chosen_then_moves_on_held_to_the_runs_limit(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts))
    configuration = switched_on(setup, template, **{"from": "2026-10-14"})
    asyncio.run(setup.nightly.catch_up())
    assert request_of(tmp_path, setup.backtests.configurations.entry(configuration)["last"]["run"])["from"] == "2026-10-14"

    setup.backtests._waiting.clear()
    setup.backtests.max_period_days = 5
    setup.clock.now = WEDNESDAY_EVENING
    asyncio.run(setup.nightly.catch_up())
    newest = request_of(tmp_path, setup.backtests.configurations.entry(configuration)["last"]["run"])
    assert (newest["from"], newest["to"]) == ("2026-10-17", "2026-10-21")


def test_a_first_day_not_over_yet_is_refused(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts))

    with pytest.raises(InvalidRequest, match="trading hours are over"):
        switched_on(setup, template, **{"from": "2026-10-21"})
    with pytest.raises(InvalidRequest, match="YYYY-MM-DD"):
        switched_on(setup, template, **{"from": "21/10/2026"})


# ── what it reads again ──


def test_a_replay_of_the_journal_reads_the_signals_and_positions_of_its_new_days(tmp_path, setup):
    body = {"from": "2026-10-13", "to": "2026-10-13", "indice": "us100", "source": {"kind": "journal"},
            "chart": {"asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "time", "timeframe": "1"}}}
    template = made(tmp_path, setup.backtests, body, journal=JOURNAL)
    (folder_of(tmp_path, template) / "positions.json").write_text(json.dumps([{"position_id": 1, "of": "the old days"}]))
    switched_on(setup, template)
    setup.queries.journal = [*JOURNAL, {**JOURNAL[0], "signal_uuid": "c", "received_timestamp": "2026-10-20T07:00:00Z"}]
    setup.queries.positions = [{"position_id": 9}]

    asyncio.run(setup.nightly.catch_up())

    new = next(run_id for run_id in setup.backtests.run_ids() if run_id != template)
    assert json.loads((folder_of(tmp_path, new) / "journal.json").read_text())[-1]["signal_uuid"] == "c"
    assert json.loads((folder_of(tmp_path, new) / "positions.json").read_text()) == [{"position_id": 9}]
    assert request_of(tmp_path, new)["source"] == {"kind": "journal", "signals": 3, "entries": 2}


def test_positions_that_cannot_be_read_are_left_out_never_taken_from_the_old_days(tmp_path, setup):
    body = {"from": "2026-10-13", "to": "2026-10-13", "indice": "us100", "source": {"kind": "journal"},
            "chart": {"asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "time", "timeframe": "1"}}}
    template = made(tmp_path, setup.backtests, body, journal=JOURNAL)
    (folder_of(tmp_path, template) / "positions.json").write_text(json.dumps([{"position_id": 1}]))
    switched_on(setup, template)
    setup.queries.journal = list(JOURNAL)

    async def unavailable(start, end):
        raise DatabaseUnavailable("down")

    setup.queries.closed_positions = unavailable
    asyncio.run(setup.nightly.catch_up())

    new = next(run_id for run_id in setup.backtests.run_ids() if run_id != template)
    assert not (folder_of(tmp_path, new) / "positions.json").exists()


def test_without_a_signal_of_entry_it_is_kept_as_such_and_not_asked_again_that_day(tmp_path, setup):
    body = {"from": "2026-10-13", "to": "2026-10-13", "indice": "us100", "source": {"kind": "journal"},
            "chart": {"asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "time", "timeframe": "1"}}}
    template = made(tmp_path, setup.backtests, body, journal=JOURNAL)
    configuration = switched_on(setup, template)
    setup.queries.journal = [JOURNAL[1]]

    assert asyncio.run(setup.nightly.catch_up()) == 0

    last = setup.backtests.configurations.entry(configuration)["last"]
    assert (last["day"], last["run"]) == ("2026-10-20", None) and "signal of entry" in last["error"]
    assert setup.nightly.due() == []
    page = setup.nightly.page(configuration)
    assert page["configuration"]["nightly"]["next"]["waits"] == WAITS_FAILED


def test_a_rules_signals_are_read_again_for_its_new_days(tmp_path, setup):
    rows = asyncio.run(setup.queries.rule_signals(RULE_ID, date(2026, 10, 13), date(2026, 10, 13)))
    body = {"from": "2026-10-13", "to": "2026-10-13", "indice": "us100",
            "source": {"kind": "ta_signals", "rule": {"id": RULE_ID, "name": "EMA cross"}},
            "chart": {"asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "range", "ticks": 40}}}
    template = made(tmp_path, setup.backtests, body, journal=rows)
    switched_on(setup, template)

    asyncio.run(setup.nightly.catch_up())

    new = next(run_id for run_id in setup.backtests.run_ids() if run_id != template)
    raised = json.loads((folder_of(tmp_path, new) / "ta_signals.json").read_text())
    assert len(raised) == 8 and raised[-1]["raised_at"] == "2026-10-20T08:00:00Z"
    assert request_of(tmp_path, new)["source"] == {"kind": "ta_signals", "signals": 8, "rule": {"id": RULE_ID, "name": "EMA cross"}}


# ── once it ended ──


def test_done_its_line_is_kept_and_the_nightly_run_before_it_let_go_never_one_made_by_hand(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts), name="Cross R60")
    configuration = switched_on(setup, template, telegram=True)

    async def two_evenings():
        await setup.nightly.catch_up()
        await until(lambda: len(setup.launcher.commands) == 1)
        setup.launcher.finish(0)
        await until(lambda: setup.backtests.configurations.entry(configuration)["last"]["settled"])
        first = setup.backtests.configurations.entry(configuration)["last"]["run"]
        setup.clock.now = WEDNESDAY_EVENING
        await setup.nightly.catch_up()
        await until(lambda: len(setup.launcher.commands) == 2)
        setup.launcher.finish(1)
        await until(lambda: setup.backtests.configurations.entry(configuration)["last"]["settled"])
        await asyncio.sleep(0)
        return first, setup.backtests.configurations.entry(configuration)["last"]["run"]

    first, second = in_the_loop(setup, two_evenings)

    assert run_folder(setup.launcher.commands[1]) == second
    # The second was made from the first, the first let go once the second was done; the operator's run stays.
    assert set(setup.backtests.run_ids()) == {template, second}
    history = setup.backtests.configurations.history(configuration)
    assert [(line["run"], line["to"], line["total_percent"]) for line in history] == [
        (second, "2026-10-21", 1.5), (first, "2026-10-20", 1.5),
    ]
    assert setup.backtests.configurations.replaced(first)["configuration"] == configuration
    assert setup.said == [
        "Nightly backtest “Cross R60” to 20 Oct: +1.5 % in 3 trades over 2 days, beats random 71 %.",
        "Nightly backtest “Cross R60” to 21 Oct: +1.5 % in 3 trades over 2 days, beats random 71 %.",
    ]


@pytest.mark.parametrize("keep, reason", [("favourite", KEPT_FAVOURITE), ("reference", KEPT_REFERENCE),
                                          ("stress", KEPT_JUDGED), ("verdict", KEPT_VERDICT)])
def test_a_nightly_run_something_rests_on_is_kept_by_the_next(tmp_path, scripts, setup, keep, reason):
    template = made(tmp_path, setup.backtests, pine(scripts))
    configuration = switched_on(setup, template)
    asyncio.run(setup.nightly.catch_up())
    first = setup.backtests.configurations.entry(configuration)["last"]["run"]
    folder = folder_of(tmp_path, first)
    (folder / "summary.json").write_text(json.dumps({"made_with": {"code": "x"}, "figures": {"total_percent": 1.0}}))
    (folder / "status.json").write_text(json.dumps({"state": DONE}))
    setup.backtests._waiting.clear()
    setup.nightly.settle()
    if keep == "favourite":
        setup.backtests.update(first, favourite=True)
    elif keep == "reference":
        setup.backtests._write_paper("references.json", {"live": first})
    elif keep == "stress":
        async def stress():
            return setup.backtests.stress(first)

        asyncio.run(stress())
        setup.backtests._waiting.clear()
    else:
        setup.nightly._verdict_runs = lambda: {first}
    assert reason in next(line for line in setup.nightly.page(configuration)["configuration"]["runs"] if line["id"] == first)["kept"]

    setup.clock.now = WEDNESDAY_EVENING
    asyncio.run(setup.nightly.catch_up())
    second = setup.backtests.configurations.entry(configuration)["last"]["run"]
    folder = folder_of(tmp_path, second)
    (folder / "summary.json").write_text(json.dumps({"made_with": {"code": "x"}, "figures": {"total_percent": 1.0}}))
    (folder / "status.json").write_text(json.dumps({"state": DONE}))
    setup.backtests._waiting.clear()
    setup.nightly.settle()

    assert first in setup.backtests.run_ids()
    assert setup.backtests.references() == ({"live": first} if keep == "reference" else {})


def test_a_failed_nightly_run_leaves_the_one_before_and_says_so(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts), name="Cross R60")
    configuration = switched_on(setup, template, telegram=True)

    async def failing():
        await setup.nightly.catch_up()
        await until(lambda: len(setup.launcher.commands) == 1)
        setup.launcher.finish(0, 1)
        await until(lambda: setup.backtests.configurations.entry(configuration)["last"]["settled"])
        await asyncio.sleep(0)

    in_the_loop(setup, failing)

    failed = setup.backtests.configurations.entry(configuration)["last"]["run"]
    assert failed in setup.backtests.run_ids() and template in setup.backtests.run_ids()
    assert setup.backtests.configurations.history(configuration) == []
    assert setup.said == ["Nightly backtest “Cross R60” to 20 Oct failed: started"]
    assert setup.nightly.page(configuration)["configuration"]["nightly"]["next"]["waits"] == WAITS_FAILED


def test_settling_twice_changes_nothing_more(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts))
    configuration = switched_on(setup, template)
    asyncio.run(setup.nightly.catch_up())
    run_id = setup.backtests.configurations.entry(configuration)["last"]["run"]
    (folder_of(tmp_path, run_id) / "summary.json").write_text(json.dumps({"made_with": {"code": "x"}, "figures": {}}))
    (folder_of(tmp_path, run_id) / "status.json").write_text(json.dumps({"state": DONE}))
    setup.backtests._waiting.clear()

    assert setup.nightly.settle() == 1
    assert setup.nightly.settle() == 0
    assert len(setup.backtests.configurations.history(configuration)) == 1


def test_a_nightly_run_is_not_made_again_on_todays_code_while_its_configuration_makes_it_again(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts))
    configuration = switched_on(setup, template)
    asyncio.run(setup.nightly.catch_up())
    run_id = setup.backtests.configurations.entry(configuration)["last"]["run"]
    (folder_of(tmp_path, run_id) / "summary.json").write_text(json.dumps({"made_with": {"code": "x"}}))
    (folder_of(tmp_path, run_id) / "status.json").write_text(json.dumps({"state": DONE}))
    setup.backtests._waiting.clear()

    with pytest.raises(NotPossible, match="each evening"):
        setup.backtests.remake([run_id])
    assert setup.backtests.listed(run_id)["nightly"] == {"configuration": configuration, "on": True}

    setup.nightly.update(configuration, nightly={"on": False})
    assert setup.backtests.listed(run_id)["nightly"]["on"] is False
    assert setup.backtests.remake_candidates([run_id]) == [run_id]


def test_a_configuration_whose_runs_are_all_gone_is_forgotten(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts))
    configuration = switched_on(setup, template)
    other = made(tmp_path, setup.backtests, pine(scripts, changes={"stoploss_percent": -10}))
    setup.backtests.delete(template)

    setup.nightly.settle()

    assert configuration not in setup.backtests.configurations.index() and other in setup.backtests.run_ids()


# ── by hand ──


def test_brought_up_to_date_by_hand_at_any_hour_to_the_last_day_over(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts))
    configuration = setup.backtests.configuration_of(template)
    setup.clock.now = WEDNESDAY_AFTERNOON

    async def by_hand():
        return await setup.nightly.bring_up_to_date(configuration)

    page = asyncio.run(by_hand())
    last = setup.backtests.configurations.entry(configuration)["last"]
    assert (last["day"], last["by_hand"]) == ("2026-10-20", True)
    assert request_of(tmp_path, last["run"])["to"] == "2026-10-20"
    assert page["configuration"]["id"] == configuration

    with pytest.raises(NotPossible, match="being made already"):
        asyncio.run(by_hand())
    setup.backtests._waiting.clear()
    (folder_of(tmp_path, last["run"]) / "summary.json").write_text(json.dumps({"made_with": {"code": "x"}}))
    (folder_of(tmp_path, last["run"]) / "status.json").write_text(json.dumps({"state": DONE}))
    with pytest.raises(NotPossible, match="covers 2026-10-13 to 2026-10-20 already"):
        asyncio.run(by_hand())


def test_by_hand_a_full_line_and_a_refusal_are_said(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts))
    configuration = setup.backtests.configuration_of(template)
    setup.backtests.max_queued = 0

    async def by_hand():
        return await setup.nightly.bring_up_to_date(configuration)

    with pytest.raises(LineFull):
        asyncio.run(by_hand())
    assert "last" not in setup.backtests.configurations.entry(configuration)


# ── the configurations as the page lists them ──


def test_runs_are_listed_by_configuration_named_after_their_oldest_without_again(tmp_path, scripts, setup):
    first = made(tmp_path, setup.backtests, pine(scripts), name="Cross R60 (again)")
    second = made(tmp_path, setup.backtests, pine(scripts, **{"to": "2026-10-19"}), name="Cross R60 (again) (again)")
    other = made(tmp_path, setup.backtests, pine(scripts, changes={"capital": 100}), name="Cross R60 small")

    async def stress():
        return setup.backtests.stress(second)

    stressed = asyncio.run(stress())["id"]
    setup.backtests._waiting.clear()
    found = setup.nightly.configurations()

    by_id = {item["id"]: item for item in found["items"]}
    mine = by_id[setup.backtests.configuration_of(first)]
    assert mine["name"] == "Cross R60" and not mine["named"] and mine["count"] == 3
    assert [line["id"] for line in mine["runs"]] == [stressed, second, first]
    assert mine["latest"]["id"] == second and [run["id"] for run in mine["judging"]] == [stressed]
    assert next(line for line in mine["runs"] if line["id"] == stressed)["derived"] == "stress"
    assert mine["distinct"] == [{"name": "capital", "value": 1000.0}]
    theirs = by_id[setup.backtests.configuration_of(other)]
    assert theirs["distinct"] == [{"name": "capital", "value": 100}] and theirs["strategy"] == mine["strategy"]
    assert mine["nightly"]["on"] is False and mine["nightly"]["next"]["to"] == "2026-10-20"
    assert found["day"] == "2026-10-20" and found["most_days"] == 365

    page = setup.nightly.page(setup.backtests.configuration_of(first))
    assert [related["id"] for related in page["related"]] == [theirs["id"]]
    assert page["related"][0]["differences"] == [{"name": "capital", "here": 1000.0, "there": 100}]


def test_a_named_configuration_keeps_its_name_and_takes_its_runs_again_once_unnamed(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts), name="Cross R60")
    configuration = setup.backtests.configuration_of(template)

    assert setup.nightly.update(configuration, name="  Kernel   room ")["configuration"]["name"] == "Kernel room"
    assert setup.nightly.update(configuration, name=None)["configuration"]["name"] == "Cross R60"
    with pytest.raises(InvalidRequest, match="120 characters"):
        setup.nightly.update(configuration, name="x" * 121)
    with pytest.raises(UnknownConfiguration):
        setup.nightly.update("ba9876543210", name="x")


def test_a_sweep_is_not_made_again_each_evening(tmp_path, scripts, setup):
    sweep = made(tmp_path, setup.backtests, pine(scripts, sweep={"axes": [
        {"kind": "setting", "name": "stoploss_percent", "values": [-5, -10]}]}))
    configuration = setup.backtests.configuration_of(sweep)

    listed = setup.nightly.page(configuration)["configuration"]
    assert listed["nightly"] is None and "combinations" in listed["why_no_nightly"]
    with pytest.raises(NotPossible, match="combinations"):
        setup.nightly.update(configuration, nightly={"on": True})


def test_while_the_market_is_open_the_next_run_goes_to_todays_day(tmp_path, scripts, setup):
    template = made(tmp_path, setup.backtests, pine(scripts, **{"to": "2026-10-20"}))
    configuration = switched_on(setup, template)
    setup.clock.now = WEDNESDAY_AFTERNOON

    nightly = setup.nightly.page(configuration)["configuration"]["nightly"]

    assert (nightly["next"]["to"], nightly["next"]["waits"]) == ("2026-10-21", WAITS_MARKET)


# ── what is said ──


def test_what_a_nightly_run_says_on_telegram():
    run = {"to": "2026-11-19", "headline": {"total_percent": 135.37, "trades": 38, "days_tested": 25, "beaten": 99.0},
           "status": {"error": "Stopped after 180 minutes\nmore"}}

    assert said_line("v4 room", run, DONE) == \
        "Nightly backtest “v4 room” to 19 Nov: +135.4 % in 38 trades over 25 days, beats random 99 %."
    assert said_line("v4 room", run, FAILED) == "Nightly backtest “v4 room” to 19 Nov failed: Stopped after 180 minutes"
    assert said_line("v4 room", run, CANCELLED) is None
    assert said_line("v4 room", {**run, "headline": {"total_percent": -1.0, "trades": 1}}, DONE) == \
        "Nightly backtest “v4 room” to 19 Nov: -1.0 % in 1 trade."
