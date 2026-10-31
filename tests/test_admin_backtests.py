"""
The admin's backtests (src/admin/backtests.py): what a request becomes on the
disk, the queue of runs, reading what a run found. The command itself is a
stand-in, but for one test that goes through a real child process.
"""

import asyncio
import json
import os
import sys
import types
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytz

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.admin.backtests import RUN_ID, BacktestService  # noqa: E402
from src.admin.errors import InvalidRequest, NotPossible, UnknownRun  # noqa: E402
from src.admin.processes import CANCELLED, DONE, FAILED, INTERRUPTED, QUEUED, RUNNING, launch  # noqa: E402
from src.admin.ta_scripts import ScriptStore  # noqa: E402
from tests.test_admin_ta_rules import BANDS, MATHS, STRATEGY  # noqa: E402

# Monday 19 October 2026, 20:00 UTC.
NOW = datetime(2026, 10, 19, 20, 0, tzinfo=timezone.utc).timestamp()


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self._ended = asyncio.Event()

    def end(self, code: int) -> None:
        self.returncode = code
        self._ended.set()

    async def wait(self) -> int:
        await self._ended.wait()
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.end(-15)

    def kill(self) -> None:
        self.end(-9)


class FakeLauncher:
    """Stands for the command: remembers what it was asked, and writes a summary when it ends well."""

    def __init__(self, ends_with: int | None = 0):
        self.ends_with = ends_with
        self.commands: list[list[str]] = []
        self.processes: list[FakeProcess] = []
        # The code its summaries say they were made with: not the admin's, unless a test says so.
        self.code = "x"

    async def __call__(self, arguments, log_path):
        self.commands.append(arguments)
        process = FakeProcess()
        self.processes.append(process)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write("started\n")
        if self.ends_with is not None:
            self.finish(len(self.processes) - 1, self.ends_with)
        return process

    def finish(self, number: int, code: int = 0) -> None:
        folder = self.commands[number][self.commands[number].index("--run") + 1]
        if code == 0:
            with open(os.path.join(folder, "summary.json"), "w", encoding="utf-8") as out_file:
                json.dump({"made_with": {"code": self.code}, "figures": {"total_percent": 1.5, "trades": 3, "win_rate": 66.7,
                                                                   "worst_drawdown": -0.4, "sample": "too_few"},
                           "against_random": {"beaten": 71.0}, "days": {"tested": 2}, "money": {"profit_loss": 150.0}}, out_file)
        self.processes[number].end(code)


@pytest.fixture
def scripts(tmp_path):
    store = ScriptStore(str(tmp_path / "ta"))
    store.create("Maths", MATHS)
    store.create("Bands", BANDS)
    store.create("Cross", STRATEGY)
    return store


@pytest.fixture
def config(tmp_path):
    with open("etc/config_example.json", encoding="utf-8") as config_file:
        example = json.load(config_file)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(example))
    return str(path)


def service(tmp_path, scripts, config, launcher=None, published=None, clock=lambda: NOW) -> BacktestService:
    return BacktestService(
        root=str(tmp_path / "backtests"), prices_root=str(tmp_path / "prices"), config_path=config, scripts=scripts,
        launcher=launcher or FakeLauncher(), clock=clock,
        publish=(lambda event, **data: published.append((event, data))) if published is not None else None,
    )


def cross_id(scripts) -> str:
    return next(meta["id"] for meta in scripts.scripts() if meta["name"] == "Cross")


def pine(scripts, **changes) -> dict:
    return {
        "from": "2026-10-13", "to": "2026-10-16", "indice": "us100",
        "source": {
            "kind": "pine", "script": {"id": cross_id(scripts), "version": 1},
            "asset": {"provider": "dukascopy", "key": "us100"}, "side": "bid", "bars": {"type": "range", "ticks": 60},
            "inputs": [{"id": "in_0", "var": "fast", "title": "Fast", "value": 12}],
            "alerts": [{"title": "Enter Long", "action": "long"}, {"title": "Exit", "action": "close-position"}],
        },
        **changes,
    }


JOURNAL = [
    {"signal_uuid": "a", "action": "long", "indice": "us100", "received_timestamp": "2026-10-13T07:00:01Z",
     "decided_at": "2026-10-13T07:00:02Z", "outcome": "executed"},
    {"signal_uuid": "b", "action": "close-position", "indice": "n/a", "received_timestamp": "2026-10-13T19:55:00Z",
     "decided_at": "2026-10-13T19:55:01Z", "outcome": "closed"},
]


def worked(scenario):
    """Run ``scenario(service)`` beside the service's own loop, as in the admin."""

    async def both(backtests: BacktestService):
        worker = asyncio.create_task(backtests.run())
        try:
            return await scenario(backtests)
        finally:
            worker.cancel()
            await backtests.close()

    return lambda backtests: asyncio.run(both(backtests))


async def until(condition, seconds: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "the service did not get there"
        await asyncio.sleep(0.001)


def folder_of(tmp_path, run_id: str):
    return tmp_path / "backtests" / "runs" / run_id


# ── asking ──


def test_a_pine_run_is_a_folder_with_its_rule_its_sources_and_its_settings(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)

    async def ask():
        return backtests.create(pine(scripts, changes={"stoploss_percent": -10}, name="  Cross R60 ", tags=["kernel", "kernel"]),
                                capital=25_000.0)

    run = asyncio.run(ask())

    assert RUN_ID.fullmatch(run["id"])
    folder = folder_of(tmp_path, run["id"])
    request = json.loads((folder / "request.json").read_text())
    assert (request["name"], request["tags"], request["kind"]) == ("Cross R60", ["kernel"], "run")
    rule = request["source"]["rule"]
    assert (rule["name"], rule["asset"], rule["bars"], rule["indice"]) == (
        "Cross", {"provider": "dukascopy", "key": "us100"}, {"type": "range", "ticks": 60}, "us100",
    )
    assert [library["name"] for library in rule["libraries"]] == ["Maths", "Bands"]
    assert request["chart"] == {"asset": rule["asset"], "side": "bid", "bars": rule["bars"]}
    assert request["baseline"] == {"repetitions": 100, "seed": 1}
    # The sources as they are now: the run is made again with them even if the script changes.
    kept = sorted(os.listdir(folder / "sources"))
    assert len(kept) == 3 and f"{cross_id(scripts)}-v1.pine" in kept
    settings = json.loads((folder / "settings.json").read_text())
    assert settings["changed"] == {"stoploss_percent": -10}
    assert settings["live_before"] == {"stoploss_percent": -15}
    assert (settings["settings"]["capital"], settings["settings"]["signal_delay_seconds"]) == (25_000.0, 3.0)
    assert (run["status"]["state"], run["position"]) == (QUEUED, 1)


def test_a_replay_of_the_journal_keeps_its_signals_and_the_asset_its_days_are_drawn_on(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    body = {"from": "2026-10-13", "to": "2026-10-13", "indice": "us100", "source": {"kind": "journal"},
            "chart": {"asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "time", "timeframe": "1"}}}

    async def ask():
        return backtests.create(body, journal=JOURNAL)

    run = asyncio.run(ask())

    folder = folder_of(tmp_path, run["id"])
    assert json.loads((folder / "journal.json").read_text()) == JOURNAL
    request = json.loads((folder / "request.json").read_text())
    assert request["source"] == {"kind": "journal", "signals": 2, "entries": 1}
    assert request["name"] == "Signals WATA received"
    assert json.loads((folder / "settings.json").read_text())["settings"]["signal_delay_seconds"] == 0.5
    assert os.listdir(folder / "sources") == []


def test_a_replay_of_the_journal_keeps_its_rows_as_the_database_gives_them(tmp_path, scripts, config):
    """asyncpg gives moments and numerics as objects: the file keeps them as times of UTC and numbers."""
    backtests = service(tmp_path, scripts, config)
    body = {"from": "2026-10-13", "to": "2026-10-13", "indice": "us100", "source": {"kind": "journal"},
            "chart": {"asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "time", "timeframe": "1"}}}
    paris = pytz.timezone("Europe/Paris")
    rows = [
        {"signal_uuid": "a", "action": "long", "indice": "us100", "confidence": Decimal("0.75"),
         "signal_timestamp": datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc),
         "received_timestamp": paris.localize(datetime(2026, 10, 13, 9, 0, 1, 250000)),
         "decided_at": datetime(2026, 10, 13, 7, 0, 2), "outcome": "executed"},
    ]

    async def ask():
        return backtests.create(body, journal=rows)

    run = asyncio.run(ask())

    kept = json.loads((folder_of(tmp_path, run["id"]) / "journal.json").read_text())
    assert kept == [{
        "signal_uuid": "a", "action": "long", "indice": "us100", "confidence": 0.75,
        "signal_timestamp": "2026-10-13T07:00:00Z", "received_timestamp": "2026-10-13T07:00:01.250000Z",
        "decided_at": "2026-10-13T07:00:02Z", "outcome": "executed",
    }]


def test_a_run_that_cannot_be_written_leaves_no_folder(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    body = {"from": "2026-10-13", "to": "2026-10-13", "indice": "us100", "source": {"kind": "journal"},
            "chart": {"asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "time", "timeframe": "1"}}}
    unwritable = [{**JOURNAL[0], "position_id": object()}]

    async def ask():
        return backtests.create(body, journal=unwritable)

    with pytest.raises(TypeError):
        asyncio.run(ask())

    assert os.listdir(tmp_path / "backtests" / "runs") == []
    assert backtests.list_runs() == []


def test_a_folder_left_without_its_request_is_no_run_and_goes_at_the_next_start(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    debris = tmp_path / "backtests" / "runs" / "20261013-120000-abcdef"
    debris.mkdir(parents=True)
    (debris / "journal.json.part").write_text("[")

    assert backtests.list_runs() == []
    with pytest.raises(UnknownRun):
        backtests.get("20261013-120000-abcdef")
    assert backtests.storage()["runs"] == {"count": 0, "bytes": 0}

    backtests.recover()
    assert not debris.exists()


@pytest.mark.parametrize("change, message", [
    ({"from": "2026-10-16", "to": "2026-10-13"}, "ends before it starts"),
    ({"from": "2026-06-01", "to": "2026-10-16"}, "92 days at most"),
    ({"from": "2026-10-21", "to": "2026-10-22"}, "in the future"),
    ({"indice": "de40"}, "one the trader takes: us100"),
    ({"changes": {"stoploss_percent": 3}}, "between -100 and -0.1"),
    ({"changes": {"allowed_indices": []}}, "not a setting a backtest can change"),
    ({"baseline": {"repetitions": 501}}, "0 to 500 repetitions"),
    ({"tags": ["a" * 30]}, "24 characters at most"),
    ({"sweep": {"axes": [{"kind": "setting", "name": "stoploss_percent", "values": [-5]}]}}, "2 to 12 different values"),
    ({"sweep": {"axes": [{"kind": "setting", "name": "stoploss_percent", "values": [-5, 4]}]}}, "between -100 and -0.1"),
])
def test_what_makes_no_sense_is_refused_saying_why_and_leaves_no_folder(tmp_path, scripts, config, change, message):
    backtests = service(tmp_path, scripts, config)

    with pytest.raises(InvalidRequest, match=message):
        backtests.create(pine(scripts, **change))

    assert not os.path.exists(tmp_path / "backtests" / "runs") or os.listdir(tmp_path / "backtests" / "runs") == []


@pytest.mark.parametrize("source, message", [
    ({"script": {"id": "aaaaaaaaaaaa", "version": 1}}, "No script"),
    ({"alerts": [{"title": "Enter Long", "action": "ignore"}]}, "No alert of the script is made a signal"),
    ({"asset": {"provider": "saxo", "key": "../1"}}, "No recorded instrument"),
    ({"asset": {"provider": "elsewhere", "key": "x"}}, "capital/<epic>"),
    ({"bars": {"type": "range", "ticks": 0}}, "Range bars are 1 to"),
    ({"bars": {"type": "time", "timeframe": "5S"}}, "10 seconds to a day"),
    ({"bars": {"type": "time", "timeframe": "W"}}, "10 seconds to a day"),
])
def test_a_pine_source_that_cannot_run_is_refused(tmp_path, scripts, config, source, message):
    body = pine(scripts)
    body["source"] = {**body["source"], **source}

    with pytest.raises(InvalidRequest, match=message):
        service(tmp_path, scripts, config).create(body)


@pytest.mark.parametrize("timeframe, kept", [("10S", "10S"), ("45S", "45S"), ("90s", "90S"), ("120S", "2"), ("240", "240")])
def test_a_run_reads_bars_of_any_length_from_ten_seconds_to_a_day(tmp_path, scripts, config, timeframe, kept):
    body = pine(scripts)
    body["source"] = {**body["source"], "bars": {"type": "time", "timeframe": timeframe}}

    run = service(tmp_path, scripts, config).create(body)

    request = json.loads((folder_of(tmp_path, run["id"]) / "request.json").read_text())
    assert request["source"]["rule"]["bars"] == {"type": "time", "timeframe": kept}
    assert request["chart"]["bars"] == {"type": "time", "timeframe": kept}


def test_a_replay_of_the_journal_draws_its_days_on_bars_of_seconds(tmp_path, scripts, config):
    body = {"from": "2026-10-13", "to": "2026-10-13", "indice": "us100", "source": {"kind": "journal"},
            "chart": {"asset": {"provider": "capital", "key": "US100"}, "bars": {"type": "time", "timeframe": "30S"}}}

    run = service(tmp_path, scripts, config).create(body, journal=JOURNAL)

    request = json.loads((folder_of(tmp_path, run["id"]) / "request.json").read_text())
    assert request["chart"]["bars"] == {"type": "time", "timeframe": "30S"}


def test_a_replay_of_a_period_without_an_entry_has_nothing_to_replay(tmp_path, scripts, config):
    body = {"from": "2026-10-13", "to": "2026-10-13", "indice": "us100", "source": {"kind": "journal"},
            "chart": {"asset": {"provider": "capital", "key": "US100"}, "bars": {"type": "time", "timeframe": "1"}}}

    with pytest.raises(NotPossible, match="no signal of entry"):
        service(tmp_path, scripts, config).create(body, journal=JOURNAL[1:])


# ── running them ──


class Ticking:
    """A clock a second further each time it is read."""

    def __init__(self):
        self.now = NOW

    def __call__(self) -> float:
        self.now += 1
        return self.now


def test_runs_are_the_command_taken_one_at_a_time_in_order(tmp_path, scripts, config):
    launcher, published = FakeLauncher(ends_with=None), []
    backtests = service(tmp_path, scripts, config, launcher, published, clock=Ticking())

    @worked
    async def scenario(backtests):
        first = backtests.create(pine(scripts))
        second = backtests.create(pine(scripts, name="Second"))
        await until(lambda: len(launcher.commands) == 1)
        states = (backtests.get(first["id"])["status"]["state"], backtests.get(second["id"])["status"]["state"])
        launcher.finish(0)
        await until(lambda: len(launcher.commands) == 2)
        launcher.finish(1)
        await until(lambda: backtests.get(second["id"])["status"]["state"] == DONE)
        return first, second, states

    first, second, states = scenario(backtests)

    assert states == (RUNNING, QUEUED)
    assert launcher.commands[0] == [
        "src.backtest.run", "--run", str(folder_of(tmp_path, first["id"])), "--data", "database",
        "--prices", str(tmp_path / "prices"), "--node", "node", "--low-priority",
        "--signal-cache", str(tmp_path / "backtests" / "_signal_cache"),
    ]
    done = backtests.get(first["id"])
    assert (done["status"]["state"], done["headline"]["total_percent"], done["headline"]["beaten"]) == (DONE, 1.5, 71.0)
    assert done["stale"] is True
    assert published[0] == ("backtest_finished", {"run_id": first["id"], "state": DONE, "name": "Cross on us100"})
    assert [run["id"] for run in backtests.list_runs()] == [second["id"], first["id"]]


def test_a_research_machine_runs_several_at_once_within_its_own_limits(tmp_path, scripts, config):
    launcher = FakeLauncher(ends_with=None)
    backtests = BacktestService(
        root=str(tmp_path / "backtests"), prices_root=str(tmp_path / "prices"), config_path=config, scripts=scripts,
        launcher=launcher, clock=Ticking(), workers=2, max_queued=4, max_period_days=200,
    )

    @worked
    async def scenario(backtests):
        runs = [backtests.create(pine(scripts, **{"from": "2026-05-01", "to": "2026-10-16"})) for _ in range(4)]
        await until(lambda: len(launcher.commands) == 2)
        with pytest.raises(NotPossible):
            backtests.create(pine(scripts))
        states = [backtests.get(run["id"])["status"]["state"] for run in runs]
        positions = [backtests.get(run["id"])["position"] for run in runs]
        launcher.finish(1)
        await until(lambda: len(launcher.commands) == 3)
        for number in (0, 2):
            launcher.finish(number)
        await until(lambda: len(launcher.commands) == 4)
        launcher.finish(3)
        await until(lambda: all(backtests.get(run["id"])["status"]["state"] == DONE for run in runs))
        return states, positions

    states, positions = scenario(backtests)

    assert states == [RUNNING, RUNNING, QUEUED, QUEUED] and positions == [None, None, 1, 2]
    assert backtests.options()["limits"]["period_days"] == 200


def test_a_command_that_fails_or_cannot_start_leaves_what_it_said(tmp_path, scripts, config):
    launcher = FakeLauncher(ends_with=1)
    backtests = service(tmp_path, scripts, config, launcher)

    async def refused(arguments, log_path):
        raise OSError("no python")

    @worked
    async def scenario(backtests):
        run = backtests.create(pine(scripts))
        await until(lambda: backtests.get(run["id"])["status"]["state"] == FAILED)
        return backtests.get(run["id"])

    failed = scenario(backtests)
    assert failed["status"]["error"] == "started" and failed["lines"] == ["started"]

    unstarted = scenario(service(tmp_path, scripts, config, refused))
    assert unstarted["status"]["error"] == "The backtest could not be started: no python"


def test_a_run_the_kernel_stopped_says_where_it_was_and_the_memory_then(tmp_path, scripts, config, monkeypatch):
    from src.admin import backtests as service_module

    class Killed(FakeLauncher):
        async def __call__(self, arguments, log_path):
            process = await super().__call__(arguments, log_path)
            folder = arguments[arguments.index("--run") + 1]
            with open(os.path.join(folder, "progress.json"), "w", encoding="utf-8") as out_file:
                json.dump({"phase": "signals", "done": 3, "total": 90, "day": "2026-09-17",
                           "engine_day": "2026-09-16T21:15:00Z", "bars": 102_365, "runner_peak_mb": 590.4}, out_file)
            self.finish(len(self.processes) - 1, -9)
            return process

    kills = iter([3, 4])
    monkeypatch.setattr(service_module, "oom_kills", lambda: next(kills))
    monkeypatch.setattr(service_module, "cgroup_memory", lambda: (1480 * 1024 ** 2, 1536 * 1024 ** 2))

    @worked
    async def scenario(backtests):
        run = backtests.create(pine(scripts))
        await until(lambda: backtests.get(run["id"])["status"]["state"] == FAILED)
        return backtests.get(run["id"])["status"]

    status = scenario(service(tmp_path, scripts, config, Killed(ends_with=None)))

    assert status["error"].splitlines()[0] == (
        "The backtest was stopped by the kernel (SIGKILL), most likely for memory: the admin's children are the first it "
        "stops, while running its script (3 of 90, 2026-09-17), on the engine day from 2026-09-16T21:15:00Z, 102,365 bars, "
        "the runner having held 590 MB at most, the admin used 1,480 of its 1,536 MB then, the kernel's OOM killer "
        "stopped 1 process(es) during the run."
    )
    assert status["failure"]["exit"] == -9 and status["failure"]["oom_kills"] == 1 and status["failure"]["bars"] == 102_365


def test_a_run_is_stopped_waiting_or_running(tmp_path, scripts, config):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, scripts, config, launcher)

    @worked
    async def scenario(backtests):
        running = backtests.create(pine(scripts))
        waiting = backtests.create(pine(scripts))
        await until(lambda: len(launcher.commands) == 1)
        backtests.cancel(waiting["id"])
        backtests.cancel(running["id"])
        await until(lambda: backtests.get(running["id"])["status"]["state"] == CANCELLED)
        with pytest.raises(NotPossible):
            backtests.cancel(running["id"])
        return backtests.get(waiting["id"])["status"]["state"]

    assert scenario(backtests) == CANCELLED
    assert launcher.processes[0].terminated and len(launcher.commands) == 1


def test_a_run_an_earlier_admin_left_unfinished_is_said_to_be_interrupted(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)

    async def ask():
        return backtests.create(pine(scripts))

    run = asyncio.run(ask())
    again = service(tmp_path, scripts, config)
    again.recover()

    assert again.get(run["id"])["status"]["state"] == INTERRUPTED


@pytest.mark.parametrize("run_id", ["..", "../prices", "20261019-200000-zzzzzz", "_fetch", "x" * 21])
def test_an_id_that_is_not_one_of_a_run_reaches_no_folder(tmp_path, scripts, config, run_id):
    backtests = service(tmp_path, scripts, config)

    for read in (backtests.get, backtests.days, backtests.trades, backtests.delete, backtests.breakdowns):
        with pytest.raises(UnknownRun):
            read(run_id)


def test_the_runs_of_one_script_are_listed_whatever_its_version(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config, clock=Ticking())
    other = next(meta["id"] for meta in scripts.scripts() if meta["name"] == "Maths")

    async def ask():
        made = [backtests.create(pine(scripts)), backtests.create(pine(scripts, name="Later"))]
        made.append(backtests.create({**pine(scripts), "source": {"kind": "journal"}, "chart": {
            "asset": {"provider": "dukascopy", "key": "us100"}, "side": "bid", "bars": {"type": "time", "timeframe": "1"}}},
            journal=JOURNAL))
        return made

    first, later, replay = asyncio.run(ask())
    listed = [run["id"] for run in backtests.list_runs(script=cross_id(scripts))]

    assert listed == [later["id"], first["id"]]
    assert backtests.list_runs(script=other) == [] and replay["id"] in [run["id"] for run in backtests.list_runs()]


def test_runs_are_named_tagged_removed_and_what_the_former_backtester_left_too(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    former = tmp_path / "backtests" / "20261012-213045-a1b2c3"
    former.mkdir(parents=True)
    (former / "summary.json").write_text("{}")
    (tmp_path / "backtests" / "_calibration").mkdir()

    async def ask():
        return backtests.create(pine(scripts)), backtests.create(pine(scripts))

    first, second = asyncio.run(ask())
    renamed = backtests.update(first["id"], name="September", tags=["keep"])
    with pytest.raises(InvalidRequest):
        backtests.update(first["id"], name="   ")
    storage = backtests.storage()
    with pytest.raises(NotPossible, match="Stop this backtest"):
        backtests.delete(second["id"])
    backtests._waiting.clear()
    removed = backtests.delete_many([first["id"], second["id"], "nope"])

    assert (renamed["name"], renamed["tags"]) == ("September", ["keep"])
    assert storage["runs"]["count"] == 2 and storage["former"]["count"] == 1 and storage["former"]["bytes"] > 0
    assert removed == {"removed": [first["id"], second["id"]], "kept": [{"id": "nope", "reason": "No such backtest"}]}
    assert backtests.delete_former() == {"removed": 1}
    assert sorted(os.listdir(tmp_path / "backtests")) == ["runs"]


# ── reading them ──


def finished(tmp_path, backtests, scripts, files: dict) -> str:
    async def ask():
        return backtests.create(pine(scripts))

    run_id = asyncio.run(ask())["id"]
    folder = folder_of(tmp_path, run_id)
    for name, content in files.items():
        (folder / name).write_text(json.dumps(content))
    (folder / "status.json").write_text(json.dumps({"state": DONE}))
    backtests._waiting.clear()
    return run_id


def trade(number: int, day: str, opened: str, performance: float, reason: str, **more) -> dict:
    return {
        "id": number, "day": day, "direction": more.get("direction", "long"), "confidence": more.get("confidence"),
        "turbo": {"issuer": more.get("issuer", "SG")}, "entry": {"filled_at": opened, "price": more.get("price", 5.0)},
        "exit": {"reason": reason}, "performance_percent": performance, "max_performance_percent": max(performance, 0.5),
        "profit_loss": performance * 10, "holding_seconds": more.get("holding", 600),
    }


def test_the_days_signals_and_trades_of_a_run_are_read_and_its_trades_broken_down(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    run_id = finished(tmp_path, backtests, scripts, {
        "days.json": [{"day": "2026-10-13", "status": "tested", "regime": {"labels": {"volatility": "Calm", "news": "No news"}}},
                      {"day": "2026-10-14", "status": "tested", "regime": {"labels": {"volatility": "Agitated", "news": "CPI"}}},
                      {"day": "2026-10-15", "status": "no_recorded_turbos"}],
        "signals.json": [{"id": 1, "day": "2026-10-13", "fate": "executed"}, {"id": 2, "day": "2026-10-13", "fate": "refused"},
                         {"id": 3, "day": "2026-10-14", "fate": "executed"}],
        "trades.json": [
            trade(1, "2026-10-13", "2026-10-13T07:30:04Z", 2.0, "Performance (Takeprofit (60%) hit at 60.1%)", confidence=0.9),
            trade(2, "2026-10-14", "2026-10-14T13:10:00Z", -1.0, "Explicit Close (All)", direction="short", price=12.0),
            # On a day that was not tested: left out of the figures.
            trade(3, "2026-10-15", "2026-10-15T13:10:00Z", -9.0, "SaxoAPI"),
        ],
    })

    assert [row["id"] for row in backtests.signals(run_id, fate="executed")] == [1, 3]
    assert [row["id"] for row in backtests.signals(run_id, datetime(2026, 10, 13).date())] == [1, 2]
    assert [row["id"] for row in backtests.trades(run_id, datetime(2026, 10, 14).date())] == [2]
    broken = backtests.breakdowns(run_id)
    assert broken["trades"] == 2
    assert [(row["hour"], row["trade_count"]) for row in broken["groups"]["hour"]] == [(9, 1), (15, 1)]
    # Paris hours: a Tuesday at 9:30 and a Wednesday at 15:10, the weekdays as ISO numbers (Monday is 1).
    assert [(row["weekday"], row["hour"], row["trade_count"]) for row in broken["groups"]["weekday_hour"]] == [
        (2, 9, 1), (3, 15, 1),
    ]
    assert [(row["exit"], row["trade_count"]) for row in broken["groups"]["exit"]] == [
        ("Signal or end of day", 1), ("Take-profit", 1),
    ]
    assert [row["price"] for row in broken["groups"]["turbo_price"] if row["trade_count"]] == ["5 to 7", "10 to 15"]
    assert broken["given_back"]["turned_negative_count"] == 1
    # The kind of day each was made on, every group named, and the place against the US open.
    assert [(row["group"], row["trade_count"]) for row in broken["groups"]["volatility"]] == [
        ("Calm", 1), ("Ordinary", 0), ("Agitated", 1),
    ]
    assert [row["trade_count"] for row in broken["groups"]["news"] if row["group"] == "CPI"] == [1]
    assert sum(row["trade_count"] for row in broken["groups"]["gap"]) == 0
    # A run made before the edge was measured has none; the signals one by one stay in the file.
    assert backtests.edge(run_id) is None
    (folder_of(tmp_path, run_id) / "edge.json").write_text(json.dumps({"horizons": [{"minutes": 1}], "signals": [{"id": 1}]}))
    assert backtests.edge(run_id) == {"horizons": [{"minutes": 1}]}


def test_a_combination_of_a_sweep_is_made_a_run_of_its_own(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    body = pine(scripts, sweep={"axes": [{"kind": "setting", "name": "stoploss_percent", "values": [-5, -15]},
                                         {"kind": "input", "name": "in_0", "values": [12, 20]}], "held_out": 0.3})

    async def ask():
        return backtests.create(body)

    sweep_run = asyncio.run(ask())
    folder = folder_of(tmp_path, sweep_run["id"])
    (folder / "sweep.json").write_text(json.dumps({"cells": [{"values": [-5, 12]}, {"values": [-5, 20]}]}))
    backtests._waiting.clear()

    async def cell():
        return backtests.run_cell(sweep_run["id"], 1)

    single = asyncio.run(cell())

    request = json.loads((folder_of(tmp_path, single["id"]) / "request.json").read_text())
    assert (request["kind"], request["sweep"], request["from_sweep"]) == ("run", None, {"id": sweep_run["id"], "cell": 1})
    assert request["changes"]["stoploss_percent"] == -5
    assert [entry["value"] for entry in request["source"]["rule"]["inputs"] if entry["id"] == "in_0"] == [20]
    settings = json.loads((folder_of(tmp_path, single["id"]) / "settings.json").read_text())
    assert settings["settings"]["stoploss_percent"] == -5
    assert len(os.listdir(folder_of(tmp_path, single["id"]) / "sources")) == 3
    with pytest.raises(InvalidRequest):
        asyncio.run(asyncio.to_thread(backtests.run_cell, sweep_run["id"], 5))


def test_a_replay_keeps_wata_s_positions_beside_it_and_its_combinations_too(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    body = {**pine(scripts), "source": {"kind": "journal"}, "chart": {"asset": {"provider": "dukascopy", "key": "us100"},
            "side": "bid", "bars": {"type": "time", "timeframe": "1"}},
            "sweep": {"axes": [{"kind": "setting", "name": "stoploss_percent", "values": [-5, -15]}], "held_out": 0.3}}
    positions = [{"position_id": "p-1", "execution_time_open": datetime(2026, 10, 13, 7, tzinfo=timezone.utc)}]

    async def ask():
        return backtests.create(body, journal=JOURNAL, positions=positions)

    sweep_run = asyncio.run(ask())
    folder = folder_of(tmp_path, sweep_run["id"])
    assert json.loads((folder / "positions.json").read_text()) == [{"position_id": "p-1", "execution_time_open": "2026-10-13T07:00:00Z"}]
    (folder / "sweep.json").write_text(json.dumps({"cells": [{"values": [-5]}, {"values": [-15]}]}))
    backtests._waiting.clear()

    async def cell():
        return backtests.run_cell(sweep_run["id"], 1)

    single = asyncio.run(cell())
    assert json.loads((folder_of(tmp_path, single["id"]) / "positions.json").read_text())[0]["position_id"] == "p-1"
    # Nothing set beside them yet: the run has not run.
    assert backtests.reconciliation(single["id"]) is None


def test_a_finished_run_is_stressed_with_later_and_worse_fills_its_own_first(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    run_id = finished(tmp_path, backtests, scripts, {"summary.json": {"made_with": {}}})

    async def stress():
        return backtests.stress(run_id)

    stressed = asyncio.run(stress())

    assert stressed["kind"] == "sweep" and stressed["stress_of"] == run_id
    request = json.loads((folder_of(tmp_path, stressed["id"]) / "request.json").read_text())
    settings = json.loads((folder_of(tmp_path, run_id) / "settings.json").read_text())["settings"]
    assert [(axis["name"], axis["values"][0]) for axis in request["sweep"]["axes"]] == [
        ("order_seconds", settings["order_seconds"]), ("slippage", settings["slippage"]),
    ]
    assert request["sweep"]["axes"][1]["values"] == [round(settings["slippage"] + worse, 6) for worse in (0, 0.01, 0.02, 0.05)]
    assert request["baseline"] is None and request["name"].startswith("Stress: ")
    # The same script and the same signals.
    assert len(os.listdir(folder_of(tmp_path, stressed["id"]) / "sources")) == len(os.listdir(folder_of(tmp_path, run_id) / "sources"))
    assert [listed["stress_of"] for listed in backtests.list_runs() if listed["id"] == stressed["id"]] == [run_id]
    # A sweep, or a run not finished, is not stressed.
    backtests._waiting.clear()
    with pytest.raises(NotPossible):
        asyncio.run(asyncio.to_thread(backtests.stress, stressed["id"]))


# ── the list's verdict, and runs made again on today's code ──


EDGE = {"horizons": [{"minutes": 5, "signals": 40, "mean_bp": 3.0, "hit_rate": 0.6, "t": 2.9, "independent": 30,
                      "random_mean_bp": 0.1, "beaten": 97.0}], "unpriced": 0}
GRID_AXES = [{"kind": "setting", "name": "order_seconds", "values": [1, 3]},
             {"kind": "setting", "name": "slippage", "values": [0, 0.01]}]


def test_the_list_carries_what_its_verdict_reads_without_reading_a_whole_run(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config, clock=Ticking())
    run_id = finished(tmp_path, backtests, scripts, {"summary.json": {
        "made_with": {"code": "x"}, "figures": {"total_percent": 4.0, "trades": 41, "days": 22, "sample": "fair"},
        "against_random": {"beaten": 96.0, "repetitions": 100, "seed": 1}, "days": {"tested": 22}, "edge": EDGE,
        "model_check": {"paired": 30, "correlation": 0.8, "recorded_trades": 31},
    }})

    async def ask():
        return backtests.create(pine(scripts, sweep={"axes": GRID_AXES, "held_out": 0.3}))

    sweep_id = asyncio.run(ask())["id"]
    folder = folder_of(tmp_path, sweep_id)
    cells = [{"values": [order, slip], "whole": {"total_percent": order - slip * 100, "days": 4, "trades": 9},
              "tuning": None, "held_out": None, "money": {"capital": 1000}, "curve": [["2026-10-13", 1.0, 2]]}
             for order in (1, 3) for slip in (0, 0.01)]
    (folder / "sweep.json").write_text(json.dumps({"axes": GRID_AXES, "held_out": 0.3, "held_out_from": None, "cells": cells}))
    (folder / "summary.json").write_text(json.dumps({"made_with": {"code": "x"}, "days": {"tested": 4}, "cells": 4,
                                                     "pbo": {"probability": 0.2, "splits": 8, "combinations": 70}}))
    (folder / "status.json").write_text(json.dumps({"state": DONE}))
    backtests._waiting.clear()

    listed = {run["id"]: run["headline"] for run in backtests.list_runs()}
    single, grid = listed[run_id], listed[sweep_id]["grid"]
    assert (single["days"], single["repetitions"], single["edge"], single["model_check"]) == (22, 100, EDGE, {"paired": 30, "correlation": 0.8})
    assert single["pbo"] is None and single["deflated_sharpe"] is None and single["grid"] is None
    assert listed[sweep_id]["pbo"] == {"probability": 0.2}
    # Each combination's totals alone: neither its money, nor its days one by one.
    assert grid["axes"] == GRID_AXES and grid["cells"][1] == {
        "values": [1, 0.01], "tuning": None, "held_out": None, "whole": {"total_percent": 0.0, "days": 4},
    }
    # Read again once its file changed, not before.
    (folder / "sweep.json").write_text(json.dumps({"axes": GRID_AXES, "cells": cells[:1]}))
    os.utime(folder / "sweep.json", (NOW + 60, NOW + 60))
    assert len(backtests.get(sweep_id)["headline"]["grid"]["cells"]) == 1


def done_at(tmp_path, run_id: str, started: str, ended: str) -> None:
    (folder_of(tmp_path, run_id) / "status.json").write_text(json.dumps({"state": DONE, "started_at": started, "finished_at": ended}))


def run_folder(command: list[str]) -> str:
    return os.path.basename(command[command.index("--run") + 1])


def test_a_stale_run_is_made_again_with_what_it_had_once_nothing_else_waits(tmp_path, scripts, config):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, scripts, config, launcher, clock=Ticking())
    old = finished(tmp_path, backtests, scripts, {"summary.json": {"made_with": {"code": "x"}},
                                                     "journal.json": [{"signal_uuid": "a"}]})
    backtests.update(old, name="September", tags=["keep"])
    done_at(tmp_path, old, "2026-10-19T20:00:00Z", "2026-10-19T20:10:00Z")
    backtests._write_paper("references.json", {"abcdef123456": old, "live": "20261001-000000-aaaaaa"})
    launcher.code = backtests.code

    @worked
    async def scenario(backtests):
        preview = backtests.remake_preview()
        # One run waits, and another is asked while the first runs: both come before the run made again.
        first = backtests.create(pine(scripts, name="First"))
        await until(lambda: len(launcher.commands) == 1)
        asked = backtests.remake([old])
        second = backtests.create(pine(scripts, name="Second"))
        launcher.finish(0)
        await until(lambda: len(launcher.commands) == 2)
        launcher.finish(1)
        await until(lambda: len(launcher.commands) == 3)
        launcher.finish(2)
        await until(lambda: backtests.remaking()["made"] == 1)
        return preview, asked, first["id"], second["id"]

    preview, asked, first, second = scenario(backtests)
    new = run_folder(launcher.commands[2])

    assert preview == {"runs": 1, "claude": 0, "seconds": 600}
    assert asked["total"] == 1 and asked["waiting"] == 1
    assert [run_folder(command) for command in launcher.commands] == [first, second, new]
    old_request = json.loads((folder_of(tmp_path, old) / "request.json").read_text())
    new_request = json.loads((folder_of(tmp_path, new) / "request.json").read_text())
    # The same request, settings, script and signals: only when it was asked, and the run it makes again.
    assert {key: value for key, value in new_request.items() if key not in ("asked_at", "remade_from")} == \
        {key: value for key, value in old_request.items() if key != "asked_at"}
    assert new_request["remade_from"] == old and (new_request["name"], new_request["tags"]) == ("September", ["keep"])
    for name in ("settings.json", "journal.json"):
        assert json.loads((folder_of(tmp_path, new) / name).read_text()) == json.loads((folder_of(tmp_path, old) / name).read_text())
    assert sorted(os.listdir(folder_of(tmp_path, new) / "sources")) == sorted(os.listdir(folder_of(tmp_path, old) / "sources"))
    # Done: the old run says so and stays, a reference that named it names the new one.
    listed = {run["id"]: run for run in backtests.list_runs()}
    assert (listed[old]["remade_by"], listed[new]["remade_from"], listed[new]["stale"]) == (new, old, False)
    assert backtests.references() == {"abcdef123456": new, "live": "20261001-000000-aaaaaa"}
    assert backtests.remaking() == {"asked_at": asked["asked_at"], "total": 1, "waiting": 0, "under_way": [], "made": 1,
                                    "failed": [], "stopped": 0}
    with pytest.raises(NotPossible, match=f"Made again already, as {new}"):
        backtests.remake([old])


def test_a_run_made_again_that_fails_leaves_the_old_one_and_one_a_restart_cut_is_asked_again(tmp_path, scripts, config):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, scripts, config, launcher, clock=Ticking())
    old = finished(tmp_path, backtests, scripts, {"summary.json": {"made_with": {"code": "x"}}})

    @worked
    async def scenario(backtests):
        backtests.remake([old])
        await until(lambda: len(launcher.commands) == 1)
        launcher.finish(0, 1)
        await until(lambda: bool(backtests.remaking()["failed"]))

    scenario(backtests)
    assert backtests.remaking()["failed"] == [{"run": old, "error": "started"}]
    assert backtests.get(old)["remade_by"] is None and backtests.get(old)["status"]["state"] == DONE

    # Asked again, then the admin stopped while it waited: the next admin asks it again, its unfinished run gone.
    backtests.remake([old])
    assert backtests._feed_remake() is True
    cut = backtests.remaking()["under_way"][0]
    again = service(tmp_path, scripts, config, clock=Ticking())
    again.recover()
    assert again.remaking()["waiting"] == 1 and again.remaking()["under_way"] == []
    assert not folder_of(tmp_path, cut).exists()


def test_a_stress_of_a_run_made_again_waits_for_it_and_then_stresses_the_new_one(tmp_path, scripts, config):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, scripts, config, launcher, clock=Ticking())
    base = finished(tmp_path, backtests, scripts, {"summary.json": {"made_with": {"code": "x"}}})

    async def stress():
        return backtests.stress(base)

    stressed = asyncio.run(stress())["id"]
    (folder_of(tmp_path, stressed) / "summary.json").write_text(json.dumps({"made_with": {"code": "x"}}))
    (folder_of(tmp_path, stressed) / "status.json").write_text(json.dumps({"state": DONE}))
    backtests._waiting.clear()
    launcher.code = backtests.code

    @worked
    async def scenario(backtests):
        backtests.remake(stale=True)
        await until(lambda: len(launcher.commands) == 1)
        launcher.finish(0)
        await until(lambda: len(launcher.commands) == 2)
        launcher.finish(1)
        await until(lambda: backtests.remaking()["made"] == 2)

    scenario(backtests)
    new_base, new_stress = (run_folder(command) for command in launcher.commands)
    base_request = json.loads((folder_of(tmp_path, new_base) / "request.json").read_text())
    stress_request = json.loads((folder_of(tmp_path, new_stress) / "request.json").read_text())
    # The stress, newer, waited for its run, then judged the run made again.
    assert base_request["remade_from"] == base
    assert (stress_request["remade_from"], stress_request["stress_of"]) == (stressed, new_base)


def test_only_a_finished_run_made_with_older_code_and_not_on_paper_is_made_again(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config, clock=Ticking())
    current = finished(tmp_path, backtests, scripts, {"summary.json": {"made_with": {"code": backtests.code}}})
    on_paper = finished(tmp_path, backtests, scripts, {"summary.json": {"made_with": {"code": "x"}}})
    backtests._index_paper("abcdef123456", "2026-10-13", on_paper)

    async def ask():
        return backtests.create(pine(scripts))

    waiting = asyncio.run(ask())["id"]

    with pytest.raises(NotPossible, match="today's code already"):
        backtests.remake([current])
    with pytest.raises(NotPossible, match="Only a finished backtest"):
        backtests.remake([waiting])
    with pytest.raises(NotPossible, match="paper trading"):
        backtests.remake([on_paper])
    with pytest.raises(UnknownRun):
        backtests.remake(["20261019-200000-abcdef"])
    with pytest.raises(NotPossible, match="No backtest made with older code"):
        backtests.remake(stale=True)
    assert backtests.remaking() is None and backtests.remake_preview() == {"runs": 0, "claude": 0, "seconds": 0}


def test_stopping_drops_what_waits_and_forgets_them_once_none_is_under_way(tmp_path, scripts, config):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, scripts, config, launcher, clock=Ticking())
    for _ in range(3):
        finished(tmp_path, backtests, scripts, {"summary.json": {"made_with": {"code": "x"}}})

    @worked
    async def scenario(backtests):
        backtests.remake(stale=True)
        await until(lambda: len(launcher.commands) == 1)
        stopped = backtests.stop_remaking()
        launcher.finish(0)
        await until(lambda: not backtests.remaking()["under_way"])
        return stopped

    stopped = scenario(backtests)
    assert (stopped["stopped"], len(stopped["under_way"])) == (2, 1)
    assert (backtests.remaking()["made"], backtests.remaking()["stopped"]) == (1, 2) and len(launcher.commands) == 1
    # Nothing under way any more: stopping again forgets them.
    assert backtests.stop_remaking() is None and backtests.remaking() is None


def test_turbos_are_measured_by_a_command_and_a_run_on_made_up_turbos_keeps_the_measure(tmp_path, scripts, config):
    asked = []

    class Measuring:
        returncode = 0

        async def wait(self):
            (tmp_path / "backtests" / "_turbo_model" / "calibration.json").write_text(json.dumps(MEASURE))
            return 0

        def kill(self):
            pass

    async def launcher(command, log):
        asked.append(command)
        return Measuring()

    backtests = service(tmp_path, scripts, config)
    backtests._launcher = launcher
    assert backtests.turbo_model()["measure"] is None
    with pytest.raises(NotPossible, match="measured yet"):
        backtests.create(pine(scripts, turbos="modelled"))

    async def measure():
        started = backtests.measure_turbos(date(2026, 10, 13), date(2026, 10, 19))
        with pytest.raises(NotPossible):
            backtests.measure_turbos(date(2026, 10, 13), date(2026, 10, 19))
        await backtests._measure_task
        return started

    assert asyncio.run(measure())["state"] == "running"
    command = asked[0]
    assert command[:2] == ["src.backtest.turbo_model", "calibrate"] and "--low-priority" in command
    assert command[command.index("--underlying") + 1] == "dukascopy/us100"
    found = backtests.turbo_model()
    assert found["state"] == "done" and found["measure"]["days"] == 6 and found["measure"]["first"] == "2026-10-13"
    assert backtests.options()["turbo_model"]["directions"]["long"]["factor"] == 0.0086
    with pytest.raises(InvalidRequest):
        backtests.measure_turbos(date(2026, 10, 19), date(2026, 10, 13))

    async def ask():
        # Two years of the index are allowed on made-up turbos.
        return backtests.create(pine(scripts, turbos="modelled", **{"from": "2024-10-20", "to": "2026-10-19"}))

    made = asyncio.run(ask())
    folder = folder_of(tmp_path, made["id"])
    assert made["turbos"] == "modelled"
    assert json.loads((folder / "turbo_model.json").read_text()) == MEASURE
    assert "turbo_model" not in json.loads((folder / "settings.json").read_text())
    command = backtests._command(made["id"])
    assert command[command.index("--data") + 1] == f"model:{folder / 'turbo_model.json'}"
    with pytest.raises(InvalidRequest, match="days at most"):
        backtests.create(pine(scripts, **{"from": "2024-10-20", "to": "2026-10-19"}))


def test_a_finished_recorded_run_is_checked_on_made_up_turbos_with_its_trades_kept(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    trades = [trade(1, "2026-10-14", "2026-10-14T08:00:00Z", 1.5, "Performance (Trailing stop ...)")]
    run_id = finished(tmp_path, backtests, scripts, {"summary.json": {"made_with": {}}, "trades.json": trades})

    def check(of: str):
        return asyncio.run(asyncio.to_thread(backtests.model_check, of))

    with pytest.raises(NotPossible, match="measured yet"):
        check(run_id)
    measured = tmp_path / "backtests" / "_turbo_model" / "calibration.json"
    measured.parent.mkdir(parents=True, exist_ok=True)
    measured.write_text(json.dumps(MEASURE))

    checked = check(run_id)

    assert checked["model_check_of"] == run_id and checked["turbos"] == "modelled"
    folder = folder_of(tmp_path, checked["id"])
    request = json.loads((folder / "request.json").read_text())
    assert request["baseline"]["repetitions"] == 0 and request["name"].startswith("Model check: ")
    assert json.loads((folder / "recorded_trades.json").read_text()) == trades
    assert json.loads((folder / "turbo_model.json").read_text()) == MEASURE
    assert len(os.listdir(folder / "sources")) == len(os.listdir(folder_of(tmp_path, run_id) / "sources"))
    assert [listed["model_check_of"] for listed in backtests.list_runs() if listed["id"] == checked["id"]] == [run_id]
    # Only a finished run on the recorded turbos: not the check itself, made up and not finished.
    backtests._waiting.clear()
    with pytest.raises(NotPossible, match="recorded turbos"):
        check(checked["id"])
    # The model measured for another indice.
    measured.write_text(json.dumps({**MEASURE, "indice": "de40"}))
    with pytest.raises(NotPossible, match="measured for de40"):
        check(run_id)


MEASURE = {
    "format": 1, "made_at": "2026-10-20T01:00:00Z", "indice": "us100",
    "underlying": {"provider": "dukascopy", "key": "us100", "side": "bid"},
    "days": ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"],
    "directions": {"long": {"factor": 0.0086, "offset": -2.0, "spacing": 50.0, "turbos": 40, "bars": 9000},
                   "short": {"factor": 0.0086, "offset": 2.0, "spacing": 50.0, "turbos": 38, "bars": 8800}},
    "spread_by_hour": {"9": 0.01}, "quoting": {"first_minute": 480, "last_minute": 1320}, "decimals": 2,
    "few_days": False, "issuer": "SG",
}


def test_the_form_is_told_every_setting_its_live_value_and_the_account_to_start_from(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    offered = backtests.options(30_000.0)

    # 1,000 € unless a run says another, the live balance beside it.
    assert offered["capital"] == {"value": 1_000.0, "balance": 30_000.0}
    assert next(entry for entry in offered["fields"] if entry["name"] == "capital")["value"] == 1_000.0
    made = asyncio.run(asyncio.to_thread(backtests.create, pine(scripts)))
    assert json.loads((folder_of(tmp_path, made["id"]) / "settings.json").read_text())["settings"]["capital"] == 1_000.0
    assert offered["indices"] == ["us100"] and offered["signal_delay"] == {"pine": 3.0, "journal": 0.5}
    stop = next(entry for entry in offered["fields"] if entry["name"] == "stoploss_percent")
    assert stop["value"] == -15
    assert [period["scale_percent"] for period in offered["fixed"]["time_periods"]] == [100, 50, 75]


# ── one run, for real ──


def test_the_admin_runs_a_real_backtest_on_recorded_files(tmp_path, scripts, config):
    from tests.test_ta_replay import fake_runner, index_rows, write_rows
    from tests.test_backtest_run import turbo_rows

    files = tmp_path / "recorded"
    rows = []
    for day in (datetime(2026, 10, 13).date(), datetime(2026, 10, 14).date()):
        rows += index_rows(day, 6, 20) + turbo_rows(day, 11, "long", 24_500) + turbo_rows(day, 12, "short", 25_500)
    write_rows(str(files / "saxo" / "recorded.csv.gz"), rows)
    command, _ = fake_runner(tmp_path)

    async def runner_launch(arguments, log_path):
        # The real command, given the stand-in for the Pine runner.
        return await launch([*arguments, "--runner", "\x1f".join(command)], log_path)

    backtests = BacktestService(
        root=str(tmp_path / "backtests"), prices_root=str(tmp_path / "prices"), config_path=config, scripts=scripts,
        launcher=runner_launch, clock=lambda: NOW, data=f"files:{files}",
    )
    body = pine(scripts, **{"from": "2026-10-13", "to": "2026-10-14"}, baseline={"repetitions": 3, "seed": 1})
    body["source"]["asset"] = {"provider": "saxo", "key": "1"}
    body["source"]["bars"] = {"type": "time", "timeframe": "1"}
    body["source"]["alerts"] = [{"title": "Enter Long", "action": "long"}, {"title": "Enter Short", "action": "short"},
                                {"title": "Exit", "action": "close-position"}]

    @worked
    async def scenario(backtests):
        run = backtests.create(body)
        await until(lambda: backtests.get(run["id"])["status"]["state"] in (DONE, FAILED), seconds=60)
        return backtests.get(run["id"])

    run = scenario(backtests)

    assert run["status"]["state"] == DONE, run["lines"]
    assert run["summary"]["days"]["tested"] == 2 and run["summary"]["figures"]["trades"] > 0
    assert run["stale"] is False
    assert backtests.trades(run["id"]) and backtests.signals(run["id"], fate="executed")
    assert backtests.baseline(run["id"])["repetitions"] == 3
