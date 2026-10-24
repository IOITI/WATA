import asyncio
import json
import os
from datetime import date, datetime, timezone

import pytest

from src.admin.data import PriceHistory
from src.admin.downloads import (
    FETCH_MAX_DAYS,
    INCOMPLETE,
    KEEP_AFTER_STOP_SECONDS,
    KEEP_BACKOFF_MAX_SECONDS,
    KEEP_BACKOFF_SECONDS,
    KEEP_MAX_DAYS,
    KEEP_PAUSE_SECONDS,
    STOPPED,
    HistoryDownloads,
    config_hours,
    trading_hours,
    trading_timezone,
)
from src.admin.errors import InvalidRequest, NotPossible
from src.admin.processes import DONE, INTERRUPTED, RUNNING
from src.price_history import dukascopy
from src.price_history.dukascopy import write_day_file

DAY = date(2026, 10, 12)
# Monday 12 October 2026, 21:30:45 UTC: 23:30 in Paris, the market is closed. The day before is a Sunday.
NOW = datetime(2026, 10, 12, 21, 30, 45, tzinfo=timezone.utc).timestamp()
MARKET_OPEN = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc).timestamp()
HOURS = ("Europe/Paris", 8, 22)


class FakeProcess:
    """A child process that ends when it is told to, or when it is terminated."""

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


def _option(arguments: list[str], name: str) -> str:
    return arguments[arguments.index(name) + 1]


class FakeLauncher:
    """Stands for the downloader: remembers what it was asked to run, and writes the report it would."""

    def __init__(self, ends_with: int | None = 0, days: list[dict] | None = None):
        # None: the command keeps running until the test ends it.
        self.ends_with = ends_with
        self.days = days
        self.commands: list[list[str]] = []
        self.processes: list[FakeProcess] = []

    async def __call__(self, arguments: list[str], log_path: str) -> FakeProcess:
        self.commands.append(arguments)
        process = FakeProcess()
        self.processes.append(process)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write("started\n")
        if self.ends_with is not None:
            self.finish(len(self.processes) - 1, self.ends_with)
        return process

    def finish(self, number: int, code: int = 0) -> None:
        arguments = self.commands[number]
        if self.days is not None:
            with open(_option(arguments, "--report"), "w", encoding="utf-8") as out_file:
                json.dump({"days": [
                    {"bars": 0, "unpublished_hours": 0, "unanswered_hours": 0, **day} for day in self.days
                ]}, out_file)
        self.processes[number].end(code)


class Clock:
    def __init__(self, now: float = NOW):
        self.now = now

    def __call__(self) -> float:
        return self.now


def service(tmp_path, launcher=None, published=None, hours=lambda: HOURS, clock=lambda: NOW, legacy=None) -> HistoryDownloads:
    return HistoryDownloads(
        prices_root=str(tmp_path / "prices"),
        hours=hours,
        launcher=launcher or FakeLauncher(),
        publish=(lambda event, **data: published.append((event, data))) if published is not None else None,
        clock=clock,
        legacy_state=legacy,
    )


def state_folder(tmp_path):
    return tmp_path / "prices" / "dukascopy" / "_fetch"


async def until(condition, seconds: float = 2.0) -> None:
    """Let the service work until ``condition`` holds."""
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "the service did not get there"
        await asyncio.sleep(0.001)


def worked(scenario):
    """Run ``scenario(...)`` in a loop of its own, the service closed afterwards, as in the admin."""

    async def run(downloads: HistoryDownloads, *arguments):
        try:
            return await scenario(downloads, *arguments)
        finally:
            await downloads.close()

    return lambda *arguments: asyncio.run(run(*arguments))


def _keep_hours(prices_root, day: date, hours, symbol: str = dukascopy.SYMBOL) -> None:
    for hour in hours:
        path = dukascopy.raw_path(str(prices_root), symbol, dukascopy.day_hours(day)[hour])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").close()


def _day_file(prices_root, day: date, label: str = "us100", symbol: str = "TEST") -> None:
    """A file of bars for the day, as the downloader writes one."""
    write_day_file(dukascopy.day_path(str(prices_root), label, day), [], symbol=symbol, label=label)


def _whole_day(prices_root, day: date) -> None:
    _day_file(prices_root, day)
    _keep_hours(prices_root, day, range(24))


# ── what there is ──


def test_each_day_is_ready_partial_incomplete_missing_or_closed(tmp_path):
    prices = tmp_path / "prices"
    _whole_day(prices, DAY)
    _keep_hours(prices, date(2026, 10, 13), range(5))
    # Written while its last hours were not published yet.
    _day_file(prices, date(2026, 10, 15))
    _keep_hours(prices, date(2026, 10, 15), range(20))
    downloads = service(tmp_path)

    coverage = downloads.coverage(date(2026, 10, 10), date(2026, 10, 15))

    assert [(day["day"], day["state"], day["hours"], day["hours_expected"]) for day in coverage["days"]] == [
        ("2026-10-10", "closed", 0, 0),          # a Saturday
        ("2026-10-11", "missing", 0, 4),         # a Sunday: the evening only
        ("2026-10-12", "ready", 24, 24),
        ("2026-10-13", "incomplete", 5, 24),
        ("2026-10-14", "missing", 0, 24),
        ("2026-10-15", "partial", 20, 24),
    ]
    assert coverage["ready"] == {"count": 2, "first": "2026-10-12", "last": "2026-10-15"}
    assert coverage["fetch"] == {"state": "idle", "lines": []}
    assert (coverage["keep"]["enabled"], coverage["keep"]["state"]) == (False, "off")
    with pytest.raises(InvalidRequest):
        downloads.coverage(date(2026, 10, 14), date(2026, 10, 10))


def test_the_disk_taken_by_prices_counts_the_days_of_every_instrument(tmp_path):
    prices = tmp_path / "prices"
    PriceHistory(str(prices)).add_instrument("DEUIDXEUR", "de40")
    _day_file(prices, DAY)
    _day_file(prices, DAY, "de40", "DEUIDXEUR")

    storage = service(tmp_path).storage()

    assert storage["days"] == 2
    assert storage["bars_bytes"] > 0 and storage["fetched_bytes"] == 0


# ── a download ──


def test_a_download_is_the_downloader_command_and_ends_with_days_missing_more_often_than_not(tmp_path):
    launcher, published = FakeLauncher(ends_with=None), []
    downloads = service(tmp_path, launcher, published)

    @worked
    async def scenario(downloads):
        started = downloads.start_fetch(date(2026, 10, 5), date(2026, 10, 9))
        await until(lambda: len(launcher.commands) == 1)
        with pytest.raises(NotPossible, match="already running"):
            downloads.start_fetch(date(2026, 10, 5), date(2026, 10, 9))
        launcher.finish(0, code=1)
        await until(lambda: downloads.fetch_status()["state"] != RUNNING)
        return started, downloads.fetch_status()

    started, ended = scenario(downloads)

    assert launcher.commands == [[
        "src.price_history.dukascopy", "--from", "2026-10-05", "--to", "2026-10-09",
        "--root", str(tmp_path / "prices"), "--symbol", "USATECHIDXUSD", "--label", "us100", "--low-priority",
        "--report", str(state_folder(tmp_path) / "report.json"),
    ]]
    assert (started["state"], started["from"], started["to"], started["automatic"]) == (RUNNING, "2026-10-05", "2026-10-09", False)
    assert (started["label"], started["symbol"]) == ("us100", "USATECHIDXUSD")
    assert (ended["state"], ended["exit_code"], ended["lines"]) == (INCOMPLETE, 1, ["started"])
    assert published == [("prices_fetch_finished", {"state": INCOMPLETE, "automatic": False, "label": "us100"})]


def test_a_download_can_be_stopped(tmp_path):
    launcher = FakeLauncher(ends_with=None)
    downloads = service(tmp_path, launcher)

    @worked
    async def scenario(downloads):
        with pytest.raises(NotPossible, match="No download is running"):
            downloads.stop_fetch()
        downloads.start_fetch(date(2026, 10, 5), date(2026, 10, 9))
        await until(lambda: len(launcher.processes) == 1)
        downloads.stop_fetch()
        await until(lambda: downloads.fetch_status()["state"] != RUNNING)
        return downloads.fetch_status()["state"]

    assert scenario(downloads) == STOPPED
    assert launcher.processes[0].terminated


@pytest.mark.parametrize("first_day, last_day, message", [
    (date(2026, 10, 9), date(2026, 10, 5), "ends before it starts"),
    (date(2026, 6, 1), date(2026, 10, 9), f"{FETCH_MAX_DAYS} days at most"),
    (date(2026, 10, 12), date(2026, 10, 13), "Prices of the future"),
])
def test_a_download_that_makes_no_sense_is_refused(tmp_path, first_day, last_day, message):
    downloads = service(tmp_path)

    async def scenario():
        with pytest.raises(InvalidRequest, match=message):
            downloads.start_fetch(first_day, last_day)

    asyncio.run(scenario())


def test_a_download_an_earlier_admin_left_running_is_said_to_be_interrupted(tmp_path):
    os.makedirs(state_folder(tmp_path))
    (state_folder(tmp_path) / "status.json").write_text(json.dumps({"state": RUNNING, "from": "2026-10-05"}))

    assert service(tmp_path).fetch_status()["state"] == INTERRUPTED


# ── other Dukascopy instruments ──


def test_another_dukascopy_instrument_is_fetched_under_its_own_label(tmp_path):
    PriceHistory(str(tmp_path / "prices")).add_instrument("DEUIDXEUR", "de40")
    launcher, published = FakeLauncher(), []
    downloads = service(tmp_path, launcher, published)

    @worked
    async def scenario(downloads):
        started = downloads.start_fetch(date(2026, 10, 5), date(2026, 10, 9), label="de40")
        await until(lambda: downloads.fetch_status()["state"] != RUNNING)
        return started

    started = scenario(downloads)

    assert launcher.commands[0][5:11] == ["--root", str(tmp_path / "prices"), "--symbol", "DEUIDXEUR", "--label", "de40"]
    assert (started["label"], started["symbol"]) == ("de40", "DEUIDXEUR")
    assert published == [("prices_fetch_finished", {"state": DONE, "automatic": False, "label": "de40"})]


@pytest.mark.parametrize("label", ["de40", "../us100", "--root", "US100"])
def test_a_label_that_is_not_an_instrument_is_refused_before_anything_is_made_of_it(tmp_path, label):
    launcher = FakeLauncher()
    downloads = service(tmp_path, launcher)

    async def scenario():
        with pytest.raises(InvalidRequest, match="No Dukascopy instrument"):
            downloads.start_fetch(date(2026, 10, 5), date(2026, 10, 9), label=label)
        with pytest.raises(InvalidRequest, match="No Dukascopy instrument"):
            downloads.coverage(date(2026, 10, 5), date(2026, 10, 9), label)

    asyncio.run(scenario())

    assert launcher.commands == []
    assert not os.path.exists(state_folder(tmp_path) / "status.json")


def test_the_coverage_of_another_instrument_is_read_from_its_own_files(tmp_path):
    prices = tmp_path / "prices"
    PriceHistory(str(prices)).add_instrument("DEUIDXEUR", "de40")
    _day_file(prices, DAY, "de40", "DEUIDXEUR")
    _keep_hours(prices, DAY, range(24), symbol="DEUIDXEUR")
    downloads = service(tmp_path)

    de40 = downloads.coverage(DAY, DAY, "de40")
    us100 = downloads.coverage(DAY, DAY)

    assert (de40["label"], de40["symbol"], de40["ready"]["count"]) == ("de40", "DEUIDXEUR", 1)
    assert [(day["state"], day["hours"]) for day in de40["days"]] == [("ready", 24)]
    assert (us100["label"], us100["symbol"], us100["ready"]["count"]) == ("us100", "USATECHIDXUSD", 0)
    assert [(day["state"], day["hours"]) for day in us100["days"]] == [("missing", 0)]


def test_a_download_of_another_instrument_settles_no_day_of_the_history_kept_up_to_date(tmp_path):
    PriceHistory(str(tmp_path / "prices")).add_instrument("DEUIDXEUR", "de40")
    clock = Clock()
    launcher = FakeLauncher(days=[
        # What would settle the day for the Nasdaq history...
        {"day": "2026-10-05", "status": "empty", "unpublished_hours": 24},
        # ... and the server not answering for hours.
        {"day": "2026-10-06", "status": "incomplete", "unanswered_hours": 5},
    ])
    downloads = service(tmp_path, launcher, clock=clock)

    @worked
    async def scenario(downloads):
        downloads.start_fetch(date(2026, 10, 5), date(2026, 10, 6), label="de40")
        await until(lambda: downloads.fetch_status()["state"] == DONE)

    scenario(downloads)

    keeper = downloads._keeper()
    assert keeper["settled"] == {}
    # The server's patience is the same whatever was asked of it: the next download waits all the same.
    assert (keeper["failures"], keeper["next_at"]) == (1, clock.now + KEEP_BACKOFF_SECONDS)
    assert downloads.coverage(date(2026, 10, 5), date(2026, 10, 5))["days"][0]["state"] == "missing"


# ── prices kept up to date without anyone asking ──


def test_nothing_is_fetched_by_itself_until_it_is_asked_for(tmp_path):
    downloads = service(tmp_path)

    assert downloads.keep_once() == "off"
    assert downloads.keep_status() == {
        # Of the last 120 days: the weekdays and the Sunday evenings. The Saturdays are closed.
        "enabled": False, "days": 120, "state": "off", "missing": 103, "settled": 0, "failures": 0, "next_at": None,
        "limits": {"days": KEEP_MAX_DAYS},
    }
    for days in (0, KEEP_MAX_DAYS + 1):
        with pytest.raises(InvalidRequest, match=f"Between 1 and {KEEP_MAX_DAYS} days"):
            downloads.set_keep(True, days)


def test_the_newest_days_that_are_missing_are_fetched_first_a_few_at_a_time(tmp_path):
    launcher = FakeLauncher(ends_with=None)
    downloads = service(tmp_path, launcher)
    # Friday is there already: nothing is asked for a second time.
    _whole_day(tmp_path / "prices", date(2026, 10, 9))

    @worked
    async def scenario(downloads):
        status = downloads.set_keep(True, 10)
        found = downloads.keep_once()
        await until(lambda: len(launcher.commands) == 1)
        return status, found, downloads.keep_once(), downloads.fetch_status()

    status, found, again, fetch = scenario(downloads)

    # From the 2nd to the 11th: six weekdays and the Sunday evening are missing, the Saturdays are closed.
    assert (status["enabled"], status["days"], status["state"], status["missing"]) == (True, 10, "due", 7)
    assert (found, again) == ("fetching", "fetching")
    # Five days ending at the newest one that is missing, yesterday. Today is left to tomorrow.
    assert launcher.commands[0][1:5] == ["--from", "2026-10-07", "--to", "2026-10-11"]
    assert (fetch["state"], fetch["automatic"]) == (RUNNING, True)


def test_nothing_is_fetched_by_itself_while_the_market_is_open(tmp_path):
    downloads = service(tmp_path, clock=Clock(MARKET_OPEN))
    downloads.set_keep(True, 10)

    assert downloads.keep_once() == "waiting_for_close"

    def unreadable():
        raise ValueError("no rule")

    # Hours that cannot be read: the market is taken to be open.
    assert service(tmp_path, hours=unreadable).keep_once() == "waiting_for_close"


def test_the_hours_are_those_of_the_live_rules(tmp_path):
    with open("etc/config_example.json", encoding="utf-8") as config_file:
        config = json.load(config_file)

    zone, start_hour, end_hour = trading_hours(config)

    assert zone == config["trade"]["config"]["general"]["timezone"]
    assert start_hour < end_hour
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    assert config_hours(str(path))() == (zone, start_hour, end_hour)
    with pytest.raises(KeyError):
        trading_hours({"trade": {"rules": []}})


def test_hours_written_in_new_yorks_time_say_so_and_the_days_keep_the_trading_timezone():
    with open("etc/config_example.json", encoding="utf-8") as config_file:
        config = json.load(config_file)
    hours = next(rule for rule in config["trade"]["rules"] if rule["rule_type"] == "market_hours")["rule_config"]
    hours.update(timezone="America/New_York", trading_start_hour=2, trading_end_hour=16, risky_trading_start_hour=15)

    assert trading_hours(config) == ("America/New_York", 2, 16)
    assert trading_timezone(config) == "Europe/Paris"
    # Monday 19 October, 21:30 in Paris (15:30 in New York): a download waits for the close.
    downloads = HistoryDownloads.__new__(HistoryDownloads)
    downloads._hours = lambda: trading_hours(config)
    assert downloads._market_is_open(datetime(2026, 10, 19, 19, 30, tzinfo=timezone.utc).timestamp()) is True
    assert downloads._market_is_open(datetime(2026, 10, 19, 20, 30, tzinfo=timezone.utc).timestamp()) is False


def test_a_download_that_went_well_is_followed_by_the_next_after_a_pause(tmp_path):
    clock = Clock()
    launcher = FakeLauncher(days=[{"day": "2026-10-09", "status": "written"}])
    downloads = service(tmp_path, launcher, clock=clock)

    @worked
    async def scenario(downloads):
        downloads.set_keep(True, 10)
        downloads.keep_once()
        await until(lambda: downloads.fetch_status()["state"] == DONE)
        waiting = downloads.keep_status()
        once = downloads.keep_once()
        clock.now += KEEP_PAUSE_SECONDS
        later = downloads.keep_once()
        await until(lambda: len(launcher.commands) == 2)
        return waiting, once, later

    waiting, once, later = scenario(downloads)

    assert (waiting["state"], waiting["failures"], waiting["next_at"]) == ("pausing", 0, "2026-10-12T21:35:45Z")
    assert (once, later) == ("pausing", "fetching")
    assert len(launcher.commands) == 2


def test_a_server_that_stops_answering_is_left_alone_longer_each_time(tmp_path):
    # A Saturday morning: the market stays closed for all the waiting there is here.
    clock = Clock(datetime(2026, 10, 10, 0, 30, tzinfo=timezone.utc).timestamp())
    # Hours it never answered for, and days it did not even try after that.
    launcher = FakeLauncher(ends_with=1, days=[
        {"day": "2026-10-08", "status": "incomplete", "unanswered_hours": 24},
        {"day": "2026-10-09", "status": "not tried"},
    ])
    downloads = service(tmp_path, launcher, clock=clock)

    @worked
    async def scenario(downloads):
        downloads.set_keep(True, 10)
        waits = []
        for attempt in range(1, 7):
            assert downloads.keep_once() == "fetching"
            await until(lambda: len(launcher.commands) == attempt and downloads.fetch_status()["state"] == INCOMPLETE)
            status = downloads.keep_status()
            assert (status["state"], status["failures"]) == ("backing_off", attempt)
            waits.append(downloads._keeper()["next_at"] - clock.now)
            clock.now += waits[-1] - 1
            assert downloads.keep_once() == "backing_off"
            clock.now += 1
        return waits

    waits = scenario(downloads)

    assert waits[:3] == [KEEP_BACKOFF_SECONDS, KEEP_BACKOFF_SECONDS * 2, KEEP_BACKOFF_SECONDS * 4]
    assert waits[-1] == KEEP_BACKOFF_MAX_SECONDS


def test_a_day_the_source_has_no_more_of_is_not_asked_for_again(tmp_path):
    clock = Clock()
    launcher = FakeLauncher(days=[
        # Answered for every hour, with nothing for any: a day the source does not have.
        {"day": "2026-10-05", "status": "empty", "unpublished_hours": 24},
        # Written without its last hours, a week ago: they will not come.
        {"day": "2026-10-06", "status": "partial", "unpublished_hours": 2},
        # Yesterday: its last hours may still be published.
        {"day": "2026-10-11", "status": "partial", "unpublished_hours": 1},
        # A Saturday is never asked for anyway.
        {"day": "2026-10-10", "status": "empty"},
    ])
    downloads = service(tmp_path, launcher, clock=clock)
    _day_file(tmp_path / "prices", date(2026, 10, 6))
    _day_file(tmp_path / "prices", date(2026, 10, 11))

    @worked
    async def scenario(downloads):
        downloads.set_keep(True, 10)
        downloads.keep_once()
        await until(lambda: downloads.fetch_status()["state"] == DONE)
        return downloads.coverage(date(2026, 10, 5), date(2026, 10, 11))

    coverage = scenario(downloads)

    assert downloads._keeper()["settled"] == {"2026-10-05": "empty", "2026-10-06": "partial"}
    assert [(day["day"][-2:], day["state"]) for day in coverage["days"]] == [
        ("05", "unavailable"), ("06", "ready"), ("07", "missing"), ("08", "missing"), ("09", "missing"),
        ("10", "closed"), ("11", "partial"),
    ]
    # From the 2nd: that Friday, the evening of Sunday the 4th, the 7th, 8th and 9th, and yesterday.
    assert (coverage["keep"]["missing"], coverage["keep"]["settled"]) == (6, 2)


def test_a_download_the_operator_stopped_is_not_started_again_at_once(tmp_path):
    clock = Clock()
    launcher = FakeLauncher(ends_with=None)
    downloads = service(tmp_path, launcher, clock=clock)

    @worked
    async def scenario(downloads):
        downloads.set_keep(True, 10)
        downloads.keep_once()
        await until(lambda: len(launcher.processes) == 1)
        downloads.stop_fetch()
        await until(lambda: downloads.fetch_status()["state"] == STOPPED)
        return downloads.keep_once(), downloads._keeper()["next_at"] - clock.now

    assert scenario(downloads) == ("pausing", KEEP_AFTER_STOP_SECONDS)
    # Switching it on again is asking: the wait is over.
    assert downloads.set_keep(True, 10)["state"] == "due"


def test_the_service_looks_by_itself_whether_a_download_is_due(tmp_path):
    launcher = FakeLauncher(ends_with=None)
    downloads = service(tmp_path, launcher)
    downloads.set_keep(True, 10)
    ticks = []

    async def scenario():
        async def tick(seconds: float) -> None:
            ticks.append(seconds)
            await asyncio.sleep(0)

        keeper = asyncio.create_task(downloads.keep_prices(tick))
        try:
            await until(lambda: len(launcher.commands) == 1)
        finally:
            keeper.cancel()
            await downloads.close()

    asyncio.run(scenario())

    assert ticks[0] == 60
    assert launcher.commands[0][0] == "src.price_history.dukascopy"


def test_what_the_former_backtester_learnt_of_the_server_is_taken_over_once(tmp_path):
    legacy = tmp_path / "backtests" / "_fetch"
    legacy.mkdir(parents=True)
    (legacy / "keep.json").write_text(json.dumps({"enabled": True, "days": 30}))
    (legacy / "keeper.json").write_text(json.dumps({"settled": {"2026-10-05": "empty"}, "failures": 0}))
    (legacy / "status.json").write_text(json.dumps({"state": "done"}))

    downloads = service(tmp_path, legacy=str(legacy))

    status = downloads.keep_status()
    assert (status["enabled"], status["days"], status["settled"]) == (True, 30, 1)
    # Only what it learnt: the state of its last download stays where it was.
    assert not (state_folder(tmp_path) / "status.json").exists()
    # Changed here afterwards, it is never overwritten by the old files again.
    downloads.set_keep(False, 30)
    assert service(tmp_path, legacy=str(legacy)).keep_status()["enabled"] is False
