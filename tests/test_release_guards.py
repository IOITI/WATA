"""
What holds a restart or a deploy back (src/release/guards.py): the admin's
rules moved where the server's own python3 reads them, and the deploy's one
more rule for the recorders.
"""

import ast
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest
import pytz

from src.admin import downloads, service_restart
from src.release import guards
from src.release.guards import Situation, deploy_refusal, market_days, market_is_open, may_trade, refusal, scheduler_job_near
from src.trade.market_calendar import CLOSED, EARLY_CLOSE, read_market_calendar

RELEASE = pathlib.Path("src/release")
# What the server's python3 runs: guards, plan, manifest, host, cli (check.py runs in the image).
HOST_MODULES = ("guards.py", "plan.py", "manifest.py", "host.py", "cli.py", "__init__.py")


def test_the_admin_reads_the_same_rules_it_always_imported():
    for name in ("ORDER", "TRADING_PATH", "SCHEDULER_JOB_MARGIN_SECONDS", "Situation", "refusal", "ordered",
                 "moment", "scheduler_job_near", "scheduler_jobs", "market_is_open", "may_trade"):
        assert getattr(service_restart, name) is getattr(guards, name), name
    for name in ("market_is_open", "trading_hours", "trading_timezone", "within_hours"):
        assert getattr(downloads, name) is getattr(guards, name), name


def test_a_deploy_holds_the_recorders_back_while_the_market_is_open():
    open_market = Situation(market_open=True)
    for service in ("price_recorder", "capital_feed"):
        assert refusal(service, open_market) is None  # the admin's restart lets them go
        assert "bars it would miss" in deploy_refusal(service, open_market)
        assert deploy_refusal(service, Situation(market_open=False)) is None


def test_a_deploy_holds_back_what_a_restart_holds_back():
    situation = Situation(positions_open=1, market_open=True, backtests_running=1, live_rules=1)
    for service in guards.ORDER:
        if service in guards.RECORDERS:
            continue
        assert deploy_refusal(service, situation) == refusal(service, situation), service
    assert deploy_refusal("admin", situation) == "1 backtest running"
    assert deploy_refusal("telegram", situation) is None


def _pytz_job_near(config: dict, now: float, margin: int = guards.SCHEDULER_JOB_MARGIN_SECONDS):
    """scheduler_job_near as it was written with pytz, before v0.61.0."""
    for what, at, zone in guards.scheduler_jobs(config):
        hour, minute = (int(part) for part in str(at).split(":")[:2])
        local_zone = pytz.timezone(zone)
        today = datetime.fromtimestamp(now, local_zone).date()
        for day in (today - timedelta(days=1), today, today + timedelta(days=1)):
            due = local_zone.localize(datetime.combine(day, datetime.min.time().replace(hour=hour, minute=minute)))
            if abs(due.timestamp() - now) <= margin:
                return f"{what} at {hour:02d}:{minute:02d} ({zone})"
    return None


def _pytz_market_open(config: dict, now: float) -> bool:
    zone, start, end = guards.trading_hours(config)
    local = datetime.fromtimestamp(now, pytz.timezone(zone))
    return local.weekday() < 5 and start <= local.hour < end


@pytest.mark.parametrize("day", ["2026-03-08", "2026-03-29", "2026-10-25", "2026-11-01", "2026-10-29"])
def test_zoneinfo_says_what_pytz_said_around_the_clock_changes(day):
    config = {
        "trade": {
            "config": {"general": {"timezone": "Europe/Paris"}},
            "rules": [
                {"rule_type": "market_hours", "rule_config": {"trading_start_hour": 2, "trading_end_hour": 16,
                                                              "timezone": "America/New_York"}},
                {"rule_type": "day_trading", "rule_config": {"close_position_time": "15:55"}},
            ],
        }
    }
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) - timedelta(hours=12)
    for minutes in range(0, 48 * 60, 7):
        now = (start + timedelta(minutes=minutes)).timestamp()
        assert scheduler_job_near(config, now) == _pytz_job_near(config, now), (day, minutes)
        assert market_is_open(config, now) == _pytz_market_open(config, now), (day, minutes)


def test_an_unknown_zone_counts_as_open_and_names_no_job():
    config = {"trade": {"rules": [{"rule_type": "market_hours", "rule_config": {
        "trading_start_hour": 2, "trading_end_hour": 16, "timezone": "Mars/Olympus"}}],
        "config": {"general": {"timezone": "Mars/Olympus"}}}}
    assert market_is_open(config, 0) is True
    assert scheduler_job_near(config, 0) is None
    assert market_is_open({}, 0) is True


def _imports(path: pathlib.Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            found.add(node.module)
    return found


# ── the days nothing is traded: a restart and a deploy need no forcing ──

def _hours(closed=(), timezone_name="America/New_York") -> dict:
    """WATA's hours as the server has them: 02:00 to 16:00 in New York, days counted in Paris."""
    hours = {"trading_start_hour": 2, "trading_end_hour": 16}
    if timezone_name:
        hours["timezone"] = timezone_name
    rules = [{"rule_type": "market_hours", "rule_config": hours}]
    if closed:
        rules.append({"rule_type": "market_closed_dates", "rule_config": {"market_closed_dates": list(closed)}})
    return {"trade": {"config": {"general": {"timezone": "Europe/Paris"}}, "rules": rules}}


def _at(text: str, zone: str = "America/New_York") -> float:
    return pytz.timezone(zone).localize(datetime.fromisoformat(text)).timestamp()


def test_a_us_holiday_is_no_open_market_for_a_restart_or_a_deploy():
    config = _hours()
    thanksgiving = _at("2026-11-26T08:00")
    # By its hours alone, a Thursday at 08:00 in New York is an open market: what waits for the close goes on waiting.
    assert market_is_open(config, thanksgiving) is True
    assert may_trade(config, thanksgiving) is False
    # An ordinary Tuesday in its hours, after them, and a Saturday.
    assert may_trade(config, _at("2026-11-24T08:00")) is True
    assert may_trade(config, _at("2026-11-24T16:00")) is False
    assert may_trade(config, _at("2026-11-28T08:00")) is False


def test_a_day_that_closes_early_is_traded_until_its_close():
    config = _hours()
    # The day after Thanksgiving closes at 13:00 in New York.
    assert may_trade(config, _at("2026-11-27T12:59")) is True
    assert may_trade(config, _at("2026-11-27T13:00")) is False
    assert market_is_open(config, _at("2026-11-27T13:00")) is True
    # Hours written in Paris: the close is still New York's (13:00 there is 19:00 in Paris).
    paris = {"trade": {"config": {"general": {"timezone": "Europe/Paris"}}, "rules": [
        {"rule_type": "market_hours", "rule_config": {"trading_start_hour": 8, "trading_end_hour": 22}}]}}
    assert may_trade(paris, _at("2026-11-27T18:59", "Europe/Paris")) is True
    assert may_trade(paris, _at("2026-11-27T19:00", "Europe/Paris")) is False


def test_the_configurations_closed_dates_are_read_as_the_trader_reads_them():
    config = _hours(closed=["24/11/2026"])
    assert may_trade(config, _at("2026-11-24T08:00")) is False
    assert may_trade(config, _at("2026-11-25T08:00")) is True
    # The day is the one of the market's hours: 02:30 in New York on the 24th is still that closed day.
    assert may_trade(config, _at("2026-11-24T02:30")) is False
    # A list that is no list is no closed date.
    odd = _hours()
    odd["trade"]["rules"].append({"rule_type": "market_closed_dates", "rule_config": {"market_closed_dates": "24/11/2026"}})
    assert may_trade(odd, _at("2026-11-24T08:00")) is True


def test_a_calendar_that_cannot_be_read_leaves_the_hours_alone(tmp_path):
    config = _hours()
    thanksgiving = _at("2026-11-26T08:00")
    assert market_days(str(tmp_path / "absent.csv")) == {}
    assert may_trade(config, thanksgiving, days={}) is True
    other = tmp_path / "other.csv"
    other.write_text("day;what\n2026-11-26;closed\n")
    assert market_days(str(other)) == {}
    # An early close whose time cannot be read counts as open, and so does an unknown zone.
    assert may_trade(config, _at("2026-11-27T15:00"), days={"2026-11-27": (EARLY_CLOSE, "soon")}) is True
    assert may_trade(_hours(timezone_name="Mars/Olympus"), thanksgiving) is True
    assert may_trade({}, thanksgiving) is True


def _as_the_trader_reads_it(config: dict, calendar, now: float) -> bool:
    """The same answer from the trader's own calendar (src/trade/market_calendar.py, pytz)."""
    zone, start, end = guards.trading_hours(config)
    local = datetime.fromtimestamp(now, pytz.timezone(zone))
    if not (local.weekday() < 5 and start <= local.hour < end):
        return False
    if local.strftime("%d/%m/%Y") in guards.closed_dates(config) or calendar.closed_on(local.date()) is not None:
        return False
    early = calendar.early_close_on(local.date())
    return early is None or now < early.close_moment()


def test_the_guards_read_the_calendar_the_trader_reads():
    calendar = read_market_calendar()
    days = market_days()
    assert (guards.CLOSED, guards.EARLY_CLOSE) == (CLOSED, EARLY_CLOSE)
    assert days and set(days) == {day.isoformat() for day in calendar.by_day}
    for day, entry in calendar.by_day.items():
        assert days[day.isoformat()] == (entry.kind, entry.close_new_york or ""), day
    assert days["2026-11-26"] == (CLOSED, "") and days["2026-11-27"] == (EARLY_CLOSE, "13:00")


@pytest.mark.parametrize("first", ["2026-11-22", "2026-12-22", "2026-07-01", "2026-03-27"])
def test_a_week_with_a_holiday_is_judged_as_the_trader_judges_it(first):
    calendar = read_market_calendar()
    config = _hours(closed=["23/12/2026"])
    start = datetime.fromisoformat(first).replace(tzinfo=timezone.utc)
    answers = set()
    for minutes in range(0, 8 * 24 * 60, 7):
        now = (start + timedelta(minutes=minutes)).timestamp()
        said = may_trade(config, now)
        assert said == _as_the_trader_reads_it(config, calendar, now), (first, minutes)
        answers.add((said, market_is_open(config, now)))
    # Each week holds a moment in WATA's hours that is no trading moment, and none the other way round.
    assert (False, True) in answers and (True, False) not in answers


@pytest.mark.parametrize("name", HOST_MODULES)
def test_the_servers_python3_runs_the_deploy_with_nothing_wata_installs(name):
    """Standard library, src.release and src.heartbeat only, written for Python 3.10 (the host's python3)."""
    path = RELEASE / name
    ast.parse(path.read_text(), feature_version=(3, 10))
    allowed = set(sys.stdlib_module_names) | {"src.heartbeat"}
    for module in _imports(path):
        assert module in allowed or module.split(".")[0] in allowed or module.startswith("src.release"), (name, module)


def test_the_heartbeat_module_the_host_imports_stays_standard():
    for module in _imports(pathlib.Path("src/heartbeat.py")):
        assert module.split(".")[0] in sys.stdlib_module_names, module
