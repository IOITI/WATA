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
from src.release.guards import Situation, deploy_refusal, market_is_open, refusal, scheduler_job_near

RELEASE = pathlib.Path("src/release")
# What the server's python3 runs: guards, plan, manifest, host, cli (check.py runs in the image).
HOST_MODULES = ("guards.py", "plan.py", "manifest.py", "host.py", "cli.py", "__init__.py")


def test_the_admin_reads_the_same_rules_it_always_imported():
    for name in ("ORDER", "TRADING_PATH", "SCHEDULER_JOB_MARGIN_SECONDS", "Situation", "refusal", "ordered",
                 "moment", "scheduler_job_near", "scheduler_jobs", "market_is_open"):
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
