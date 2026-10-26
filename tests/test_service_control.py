"""
The control service (src/service_control/), the admin's way to it
(src/admin/service_control.py), what holds a restart back and how a plan of
restarts goes (src/admin/service_restart.py), and the routes of the System
page that ask for them (src/admin/routers/services.py).
"""

import asyncio
import json
import os
import shutil
import tempfile
from collections import deque
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytz
import yaml

from src.admin.config_catalog import SERVICES
from src.admin.errors import ServiceUnavailable
from src.admin.service_control import RestartRefused, ServiceControl
from src.admin.service_restart import (
    FAILED,
    HELD,
    ORDER,
    READY,
    RESTARTING,
    SKIPPED,
    TRADING_PATH,
    Readiness,
    RestartPlans,
    Situation,
    moment,
    ordered,
    pending_changes,
    refusal,
    scheduler_job_near,
    situation_of,
)
from src.service_control import read_token
from src.service_control import containers
from src.service_control.app import HOUR_SECONDS, create_app, restart_wait
from src.service_control.docker import Docker, DockerError, is_watas, state_of
from tests.test_admin_api import (  # noqa: F401 (fixtures)
    PASSWORD,
    SAME_ORIGIN,
    config_path,
    make_client,
    make_ctx,
)

TOKEN = "f" * 64
STARTED = "2026-10-24T07:00:00.123456789Z"


def inspected(name: str, *, running=True, started=STARTED, image="wata-base:latest", service=None) -> dict:
    """What Docker answers for a container of WATA's compose file."""
    return {
        "State": {"Status": "running" if running else "exited", "Running": running, "StartedAt": started,
                  "FinishedAt": "0001-01-01T00:00:00Z", "ExitCode": 0, "OOMKilled": False},
        "RestartCount": 0,
        "Config": {"Image": image, "Labels": {"com.docker.compose.service": service or name}},
    }


# ── The Docker client ──


def docker_answering(handler, api_version="1.51") -> tuple[Docker, list]:
    asked = []

    def record(request: httpx.Request) -> httpx.Response:
        asked.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == "/_ping":
            return httpx.Response(200, text="OK", headers={"Api-Version": api_version} if api_version else {})
        return handler(request)

    return Docker(transport=httpx.MockTransport(record)), asked


def test_docker_is_asked_two_things_at_the_version_of_its_api_it_serves():
    docker, asked = docker_answering(lambda request: httpx.Response(
        200 if request.method == "GET" else 204, json=inspected("trader1") if request.method == "GET" else None))

    async def both():
        found = await docker.inspect("trader1")
        await docker.restart("trader1")
        await docker.close()
        return found

    assert asyncio.run(both())["State"]["Running"] is True
    # Pinged once; no ``t``: Docker waits the container's own stop_grace_period.
    assert asked == [("GET", "/_ping", {}),
                     ("GET", "/v1.51/containers/trader1/json", {}),
                     ("POST", "/v1.51/containers/trader1/restart", {})]


def test_a_docker_that_does_not_say_its_api_version_is_asked_nothing():
    docker, asked = docker_answering(lambda request: httpx.Response(200, json={}), api_version="")

    with pytest.raises(DockerError, match="version of its API"):
        asyncio.run(docker.inspect("trader1"))
    assert asked == [("GET", "/_ping", {})]


def test_docker_is_never_asked_anything_else():
    docker, asked = docker_answering(lambda request: httpx.Response(200, json={}))

    async def attempt(method, template, name):
        await docker._call(method, template, name, 1)

    for method, template, name in (
        ("POST", "/containers/{name}/stop", "trader1"),
        ("DELETE", "/containers/{name}", "trader1"),
        ("POST", "/containers/{name}/exec", "trader1"),
        ("GET", "/containers/{name}/json", "../images/json"),
        ("GET", "/containers/{name}/json", "trader1?all=1"),
        ("GET", "/_ping", "trader1"),
    ):
        with pytest.raises(ValueError):
            asyncio.run(attempt(method, template, name))
    assert asked == []


def test_a_container_docker_does_not_know_is_absent_and_a_refusal_is_an_error():
    docker, _ = docker_answering(lambda request: httpx.Response(404 if request.method == "GET" else 500, text="boom"))

    assert asyncio.run(docker.inspect("ta_engine1")) is None
    with pytest.raises(DockerError, match="500"):
        asyncio.run(docker.restart("ta_engine1"))


def test_docker_is_reached_through_its_socket():
    folder = tempfile.mkdtemp(prefix="wsc")
    socket_path = os.path.join(folder, "docker.sock")
    seen = []

    async def answer(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        seen.append(head.splitlines()[0])
        body = b"OK" if " /_ping " in head else json.dumps(inspected("trader1")).encode()
        writer.write(b"HTTP/1.1 200 OK\r\nApi-Version: 1.47\r\nContent-Type: application/json\r\nContent-Length: "
                     + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
        await writer.drain()
        writer.close()

    async def ask():
        server = await asyncio.start_unix_server(answer, path=socket_path)
        docker = Docker(socket_path=socket_path)
        try:
            return await docker.inspect("trader1")
        finally:
            await docker.close()
            server.close()
            await server.wait_closed()

    try:
        found = asyncio.run(ask())
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    assert found["Config"]["Labels"]["com.docker.compose.service"] == "trader1"
    assert seen == ["GET /_ping HTTP/1.1", "GET /v1.47/containers/trader1/json HTTP/1.1"]


def test_a_container_is_watas_only_by_its_compose_label_and_its_image():
    assert is_watas("trader1", inspected("trader1"))
    assert not is_watas("trader1", inspected("trader1", service="postgres1"))
    assert not is_watas("trader1", inspected("trader1", image="postgres:16"))
    assert not is_watas("trader1", {"Config": {}})


def test_a_container_is_said_as_the_admin_shows_it():
    assert state_of("ta_engine", "ta_engine1", None) == {"service": "ta_engine", "container": "ta_engine1", "state": "absent"}
    said = state_of("trader", "trader1", inspected("trader1"))
    assert said["running"] is True and said["started_at"] == STARTED and said["image"] == "wata-base:latest"


# ── The control service ──


class FakeDocker:
    def __init__(self):
        self.containers = {name: inspected(name) for name in containers.CONTAINERS.values()}
        self.restarted = []
        self.release = None
        self.failing = False

    async def inspect(self, name):
        return self.containers.get(name)

    async def restart(self, name):
        if self.release is not None:
            await self.release.wait()
        if self.failing:
            raise DockerError("Docker answered 500 for trader1: boom")
        self.restarted.append(name)

    async def close(self):
        pass


def control_app(docker, now=lambda: 1000.0):
    return create_app(docker, TOKEN, clock=now)


async def call(app, method, path, token=TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://control") as client:
        return await client.request(method, path, headers=headers)


def test_the_control_service_answers_only_with_its_token():
    app = control_app(FakeDocker())

    async def asks():
        return [
            (await call(app, "GET", "/health", token=None)).status_code,
            (await call(app, "GET", "/services", token=None)).status_code,
            (await call(app, "GET", "/services", token="g" * 64)).status_code,
            (await call(app, "POST", "/services/trader/restart", token=None)).status_code,
            (await call(app, "GET", "/services")).status_code,
        ]

    assert asyncio.run(asks()) == [200, 401, 401, 401, 200]


def test_the_control_service_says_each_of_watas_containers():
    docker = FakeDocker()
    del docker.containers["ta_engine1"]
    found = asyncio.run(call(control_app(docker), "GET", "/services")).json()["services"]

    assert [entry["service"] for entry in found] == list(containers.CONTAINERS)
    by_service = {entry["service"]: entry for entry in found}
    assert by_service["ta_engine"]["state"] == "absent"
    assert by_service["trader"]["running"] is True and by_service["trader"]["restarting"] is False


def test_a_restart_is_taken_then_done_in_the_background():
    docker = FakeDocker()
    app = control_app(docker)

    async def restart_then_look():
        answer = await call(app, "POST", "/services/scheduler/restart")
        await asyncio.sleep(0)
        return answer, (await call(app, "GET", "/services")).json()["services"]

    answer, found = asyncio.run(restart_then_look())

    assert answer.status_code == 202 and answer.json()["container"] == "scheduler1"
    assert docker.restarted == ["scheduler1"]
    scheduler = next(entry for entry in found if entry["service"] == "scheduler")
    assert scheduler["last_restart"]["ok"] is True


def test_the_control_service_restarts_nothing_else():
    docker = FakeDocker()
    docker.containers["trader1"] = inspected("trader1", service="postgres1")
    del docker.containers["ta_engine1"]
    app = control_app(docker)

    async def asks():
        return [(await call(app, "POST", f"/services/{name}/restart")).status_code
                for name in ("postgres", "rabbitmq", "traefik", "service_control", "pg_backup", "ta_engine", "trader")]

    # Not one of its services; one whose profile is off; one that is not the container of WATA's compose file.
    assert asyncio.run(asks()) == [404, 404, 404, 404, 404, 404, 409]
    assert docker.restarted == []


def test_one_restart_at_a_time():
    docker = FakeDocker()
    app = control_app(docker)

    async def two():
        docker.release = asyncio.Event()
        first = await call(app, "POST", "/services/telegram/restart")
        second = await call(app, "POST", "/services/scheduler/restart")
        docker.release.set()
        await asyncio.sleep(0.01)
        return first.status_code, second.status_code, second.json()["detail"]

    first, second, detail = asyncio.run(two())

    assert (first, second) == (202, 409)
    assert "telegram is being restarted" in detail
    assert docker.restarted == ["telegram1"]


def test_a_restart_docker_refused_is_said():
    docker = FakeDocker()
    docker.failing = True
    app = control_app(docker)

    async def restart_then_look():
        await call(app, "POST", "/services/trader/restart")
        await asyncio.sleep(0)
        return (await call(app, "GET", "/services")).json()["services"]

    trader = next(entry for entry in asyncio.run(restart_then_look()) if entry["service"] == "trader")
    assert trader["last_restart"]["ok"] is False and "500" in trader["last_restart"]["detail"]


def test_the_trading_path_is_restarted_at_most_once_in_five_minutes_and_three_times_an_hour():
    assert restart_wait("trader", deque(), 1000.0) == 0
    assert restart_wait("trader", deque([900.0]), 1000.0) == 200
    assert restart_wait("trader", deque([600.0]), 1000.0) == 0
    assert restart_wait("trader", deque([0.0, 400.0, 800.0]), 1200.0) == HOUR_SECONDS - 1200
    # The others twelve times an hour, whatever the gap.
    assert restart_wait("telegram", deque([990.0]), 1000.0) == 0
    assert restart_wait("telegram", deque(float(moment) for moment in range(0, 1200, 100)), 1200.0) == HOUR_SECONDS - 1200


def test_a_restart_asked_again_too_soon_is_refused_with_its_wait():
    docker = FakeDocker()
    clock = iter([1000.0, 1100.0])
    app = control_app(docker, now=lambda: next(clock))

    async def twice():
        await call(app, "POST", "/services/trader/restart")
        await asyncio.sleep(0)
        return await call(app, "POST", "/services/trader/restart")

    refused = asyncio.run(twice())

    assert refused.status_code == 429 and refused.headers["retry-after"] == "200"
    assert docker.restarted == ["trader1"]


def test_the_control_service_refuses_a_short_token(tmp_path):
    short = tmp_path / "token"
    short.write_text("abc\n")
    with pytest.raises(ValueError, match="too short"):
        read_token(str(short))
    short.write_text(TOKEN + "\n")
    assert read_token(str(short)) == TOKEN


# ── Its list of containers ──


def test_the_control_service_may_restart_watas_services_and_nothing_else_of_the_compose_file():
    with open("deploy/docker-compose.yml") as compose_file:
        services = yaml.safe_load(compose_file)["services"]
    # The services, not the tools run by hand (profile tools, never started by up).
    wata = {
        name for name, service in services.items()
        if service.get("image") == "wata-base:latest" and service.get("profiles") != ["tools"]
    }

    assert set(containers.CONTAINERS.values()) == wata - {"service_control1"}
    assert containers.CONTAINERS == {service.id: service.container for service in SERVICES}
    assert containers.TRADING_PATH == TRADING_PATH
    assert sorted(ORDER) == sorted(containers.CONTAINERS)
    assert ORDER[-1] == "admin"


# ── The admin's way to it ──


def control_client(tmp_path, handler, token=True) -> tuple[ServiceControl, list]:
    token_file = tmp_path / "token"
    if token:
        token_file.write_text(TOKEN + "\n")
    asked = []

    def record(request: httpx.Request) -> httpx.Response:
        asked.append((request.method, request.url.path, request.headers.get("authorization")))
        return handler(request)

    return ServiceControl(url="http://control", token_file=str(token_file), transport=httpx.MockTransport(record)), asked


def test_the_admin_without_the_token_has_no_control_service(tmp_path):
    control, asked = control_client(tmp_path, lambda request: httpx.Response(200, json={"services": []}), token=False)

    assert control.available is False
    with pytest.raises(ServiceUnavailable, match="no token"):
        asyncio.run(control.services())
    assert asked == []


def test_the_admin_asks_with_its_token_and_reuses_a_recent_answer(tmp_path):
    control, asked = control_client(tmp_path, lambda request: httpx.Response(
        200 if request.method == "GET" else 202, json={"services": [{"service": "trader"}]} if request.method == "GET" else {}))

    async def asks():
        await control.services()
        await control.services(max_age=10)
        await control.restart("trader")
        # A restart asked makes the last answer old.
        await control.services(max_age=10)

    asyncio.run(asks())

    assert asked == [
        ("GET", "/services", f"Bearer {TOKEN}"),
        ("POST", "/services/trader/restart", f"Bearer {TOKEN}"),
        ("GET", "/services", f"Bearer {TOKEN}"),
    ]


def test_a_restart_the_control_service_refuses_is_said_with_its_wait(tmp_path):
    control, _ = control_client(tmp_path, lambda request: httpx.Response(
        429, json={"detail": "trader was restarted lately: again in 200 s"}, headers={"Retry-After": "200"}))

    with pytest.raises(RestartRefused) as refused:
        asyncio.run(control.restart("trader"))

    assert (refused.value.status, refused.value.retry_after) == (429, 200)
    assert "again in 200 s" in refused.value.detail


def test_a_control_service_out_of_reach_is_unavailable(tmp_path):
    def unreachable(request):
        raise httpx.ConnectError("refused")

    control, _ = control_client(tmp_path, unreachable)

    with pytest.raises(ServiceUnavailable):
        asyncio.run(control.services())
    with pytest.raises(ServiceUnavailable):
        asyncio.run(control.restart("trader"))


# ── What holds a restart back ──


def test_the_trading_path_is_held_back_by_a_position_or_the_open_market():
    for service in TRADING_PATH:
        assert refusal(service, Situation()) is None
        assert "1 position open" in refusal(service, Situation(positions_open=1))
        # Saxo's count from the monitor's heartbeat: a position bought and not in a row yet.
        assert "2 positions open" in refusal(service, Situation(positions_open=0, positions_listed=2))
        assert "cannot be read" in refusal(service, Situation(positions_open=None))
        assert "market is open" in refusal(service, Situation(market_open=True))
    for service in ("telegram", "price_recorder", "capital_feed"):
        assert refusal(service, Situation(positions_open=3, market_open=True)) is None


def test_the_monitor_is_held_back_by_its_sales_the_scheduler_by_its_jobs():
    said = refusal("position_monitor", Situation(close_intents=("5001",)))
    assert "1 sale still being tried (position 5001), which the restart drops" in said
    assert "the close of the day" in refusal("scheduler", Situation(scheduler_job_near="the close of the day at 15:55"))
    assert refusal("trader", Situation(close_intents=("5001",))) is None


def test_the_ta_engine_is_held_back_by_a_live_rule_while_the_market_is_open():
    assert refusal("ta_engine", Situation(live_rules=1)) is None
    assert refusal("ta_engine", Situation(market_open=True)) is None
    assert "1 live rule" in refusal("ta_engine", Situation(live_rules=1, market_open=True))


def test_the_admin_is_held_back_by_what_runs_in_it():
    assert "2 backtests running" in refusal("admin", Situation(backtests_running=2))
    assert "studies" in refusal("admin", Situation(study_running=True))
    assert refusal("admin", Situation(market_open=True, positions_open=1)) is None


def test_a_plan_restarts_in_its_order_and_the_admin_last():
    assert ordered(["admin", "trader", "web_server", "trader"]) == ["web_server", "trader", "admin"]
    with pytest.raises(ValueError, match="postgres"):
        ordered(["postgres"])


def test_scheduler_jobs_hold_it_back_two_minutes_either_side():
    config = {
        "trade": {
            "config": {"general": {"timezone": "Europe/Paris"}},
            "rules": [
                {"rule_type": "market_hours", "rule_config": {"timezone": "America/New_York"}},
                {"rule_type": "day_trading", "rule_config": {"close_position_time": "15:55"}},
            ],
        },
    }
    new_york, paris = pytz.timezone("America/New_York"), pytz.timezone("Europe/Paris")

    def at(zone, hour, minute, second=0):
        return zone.localize(datetime(2026, 10, 23, hour, minute, second)).timestamp()

    assert scheduler_job_near(config, at(new_york, 15, 54)) == "the close of the day at 15:55 (America/New_York)"
    assert scheduler_job_near(config, at(new_york, 15, 56, 30)) is not None
    assert scheduler_job_near(config, at(new_york, 15, 50)) is None
    assert scheduler_job_near(config, at(paris, 22, 1)) == "the daily report at 22:00 (Europe/Paris)"
    assert scheduler_job_near(config, at(paris, 8, 59)) == "the calendars' check at 09:00 (Europe/Paris)"


def test_an_instant_is_read_as_docker_and_the_journal_write_it():
    assert moment("2026-10-24T07:00:00.123456789Z") == datetime(2026, 10, 24, 7, 0, 0, 123456, tzinfo=timezone.utc)
    assert moment("2026-10-24T09:00:00+02:00") == datetime(2026, 10, 24, 7, tzinfo=timezone.utc)
    assert moment("0001-01-01T00:00:00Z") is None
    assert moment(None) is None and moment("soon") is None
    assert moment(datetime(2026, 10, 24, 7)) == datetime(2026, 10, 24, 7, tzinfo=timezone.utc)


# ── What a service does not run with yet ──


CLOSE = "trade.rules[day_trading].rule_config.close_position_time"
SILENCE = "reporting.signal_silence_minutes"
START = datetime(2026, 10, 24, 7, tzinfo=timezone.utc)


def config_with(close="21:55", silence=None) -> dict:
    config = {"trade": {"rules": [{"rule_type": "day_trading", "rule_name": "day_trading",
                                   "rule_config": {"close_position_time": close}}]}}
    if silence is not None:
        config["reporting"] = {"signal_silence_minutes": silence}
    return config


def saved(minutes_after_start: int, *changes) -> dict:
    return {"at": (START + timedelta(minutes=minutes_after_start)).isoformat(), "changes": list(changes)}


def change(path, before, after, added=False) -> dict:
    return {"path": path, "before": before, "after": after, "added": added, "removed": False}


def test_a_change_saved_after_a_service_started_is_pending_for_the_services_that_read_it():
    history = [saved(10, change(CLOSE, "21:55", "21:50"))]

    assert pending_changes("scheduler", START, history, config_with("21:50"))["paths"] == [CLOSE]
    # Read by the scheduler, not by the trader.
    assert pending_changes("trader", START, history, config_with("21:50"))["paths"] == []
    # Saved before the start: the service runs with it.
    assert pending_changes("scheduler", START + timedelta(minutes=20), history, config_with("21:50"))["paths"] == []


def test_a_change_undone_since_is_none():
    history = [saved(10, change(CLOSE, "21:55", "21:50")), saved(20, change(CLOSE, "21:50", "21:55"))]

    assert pending_changes("scheduler", START, history, config_with("21:55"))["paths"] == []


def test_a_key_added_since_is_pending_until_it_is_removed_again():
    history = [saved(10, change(SILENCE, None, 30, added=True))]

    assert pending_changes("trader", START, history, config_with(silence=30))["paths"] == [SILENCE]
    assert pending_changes("trader", START, history, config_with())["paths"] == []


def test_a_change_made_on_the_server_is_said_without_knowing_what_it_was():
    history = [saved(-60, change(CLOSE, "21:50", "21:55"))]
    # A save's own write is followed by its note within a second.
    written_by_a_save = START + timedelta(minutes=-60)
    by_hand = START + timedelta(minutes=30)

    assert pending_changes("trader", START, history, config_with(), written_by_a_save)["outside"] is False
    assert pending_changes("trader", START, history, config_with(), by_hand) == {"paths": [], "outside": True}
    assert pending_changes("trader", None, history, config_with(), by_hand) == {"paths": [], "outside": False}


# ── A plan of restarts ──


class FakeControl:
    """WATA's containers, each started again a poll or two after its restart is asked."""

    available = True

    def __init__(self, never_again=(), absent=(), refuse=None, docker_fails=(), started=STARTED):
        self.states = {service: {"service": service, "state": "running", "running": True, "started_at": started}
                       for service in ORDER}
        for service in absent:
            self.states[service] = {"service": service, "state": "absent"}
        self.never_again = set(never_again)
        self.refuse = refuse
        self.docker_fails = set(docker_fails)
        self.restarted = []
        self.polls = 0

    async def services(self, max_age=0):
        self.polls += 1
        return [dict(state) for state in self.states.values()]

    async def restart(self, service):
        if self.refuse is not None:
            raise self.refuse
        self.restarted.append(service)
        requested_at = f"2026-10-24T08:{len(self.restarted):02d}:00+00:00"
        if service in self.docker_fails:
            self.states[service]["last_restart"] = {"requested_at": requested_at, "ok": False, "detail": "Docker answered 500"}
        elif service not in self.never_again:
            self.states[service]["started_at"] = f"2026-10-24T08:{len(self.restarted):02d}:00.5Z"
        return {"service": service, "requested_at": requested_at}

    async def close(self):
        pass


class FakeReadiness:
    def __init__(self, never=()):
        self.never = set(never)
        self.asked = []

    async def before(self, service):
        return None

    async def ready(self, service, started_at, before):
        self.asked.append((service, started_at))
        return (False, "No heartbeat of its new start yet") if service in self.never else (True, "Beating again")


class FakeEvents:
    def __init__(self):
        self.published = []

    def publish(self, event_type, **data):
        self.published.append((event_type, data))


def plans_with(control, readiness=None, situation=Situation(), events=None, notify=None) -> RestartPlans:
    clock = [0.0]

    async def sleep(seconds):
        clock[0] += seconds

    async def gathered():
        return situation() if callable(situation) else situation

    return RestartPlans(control, readiness or FakeReadiness(), gathered, events, notify=notify, poll_seconds=2,
                        running_timeout=20, ready_timeout=20, clock=lambda: clock[0], sleep=sleep)


def run_plan(plans: RestartPlans, services, forced=False):
    async def go():
        plan = plans.start(services, forced=forced, by="10.0.0.1")
        await plans._task
        return plan.as_dict()

    return asyncio.run(go())


def states(plan) -> dict:
    return {step["service"]: step["state"] for step in plan["steps"]}


def test_a_plan_restarts_each_service_once_the_one_before_works_again():
    control, readiness, events = FakeControl(), FakeReadiness(), FakeEvents()
    said = []

    async def notify(message):
        said.append(message)

    plan = run_plan(plans_with(control, readiness, events=events, notify=notify), ["trader", "telegram", "web_server"])

    assert control.restarted == ["web_server", "telegram", "trader"]
    assert states(plan) == {"web_server": READY, "telegram": READY, "trader": READY}
    assert plan["done"] is True
    # Each judged from its own new start.
    assert [(service, started.minute) for service, started in readiness.asked] == [("web_server", 1), ("telegram", 2), ("trader", 3)]
    assert events.published[-1][0] == "restart_plan" and events.published[-1][1]["plan"]["done"] is True
    assert events.published[-1][1]["plan"]["names"] == {"web_server": "Web server", "telegram": "Telegram", "trader": "Trader"}
    # A plan done as asked is not said on Telegram: the audit said it was asked.
    assert said == []


def test_a_plan_stops_at_a_service_not_running_again_or_not_at_work():
    control = FakeControl(never_again={"telegram"})
    said = []

    async def notify(message):
        said.append(message)

    plan = run_plan(plans_with(control, notify=notify), ["web_server", "telegram", "trader"])

    assert states(plan) == {"web_server": READY, "telegram": FAILED, "trader": SKIPPED}
    assert "Not running again" in plan["steps"][1]["detail"]
    assert control.restarted == ["web_server", "telegram"]
    # Said on Telegram: whoever asked may have left the page.
    assert said == ["⚠️ WATA Admin: a restart stopped\nTelegram: Not running again 20 s after the restart"]

    control = FakeControl()
    plan = run_plan(plans_with(control, FakeReadiness(never={"web_server"})), ["web_server", "trader"])
    assert states(plan) == {"web_server": FAILED, "trader": SKIPPED}
    assert "not at work" in plan["steps"][0]["detail"] and "No heartbeat" in plan["steps"][0]["detail"]


def test_a_plan_stops_at_what_docker_or_the_control_service_refused():
    plan = run_plan(plans_with(FakeControl(docker_fails={"scheduler"})), ["scheduler", "trader"])
    assert states(plan) == {"scheduler": FAILED, "trader": SKIPPED}
    assert "Docker answered 500" in plan["steps"][0]["detail"]

    refused = RestartRefused(429, "trader was restarted lately: again in 200 s", 200)
    plan = run_plan(plans_with(FakeControl(refuse=refused)), ["trader"])
    assert states(plan) == {"trader": FAILED}
    assert plan["steps"][0]["detail"] == "trader was restarted lately: again in 200 s"


def test_a_plan_reads_its_guards_again_before_each_step_unless_forced():
    seen = iter([Situation(), Situation(positions_open=1)])
    control = FakeControl()

    plan = run_plan(plans_with(control, situation=lambda: next(seen)), ["telegram", "trader"])

    # A position opened while the telegram service restarted: the trader is held back.
    assert states(plan) == {"telegram": READY, "trader": HELD}
    assert "1 position open" in plan["steps"][1]["detail"]
    assert control.restarted == ["telegram"]

    control = FakeControl()
    plan = run_plan(plans_with(control, situation=Situation(positions_open=1, market_open=True)), ["telegram", "trader"],
                    forced=True)
    assert states(plan) == {"telegram": READY, "trader": READY}


def test_a_restart_that_failed_before_the_plan_does_not_stop_it():
    control = FakeControl()
    control.states["telegram"]["last_restart"] = {"requested_at": "2026-10-23T20:00:00+00:00", "ok": False, "detail": "boom"}

    plan = run_plan(plans_with(control), ["telegram"])

    assert states(plan) == {"telegram": READY}


def test_a_plan_passes_over_a_service_whose_profile_is_off_and_ends_with_the_admin():
    control = FakeControl(absent={"ta_engine"})

    plan = run_plan(plans_with(control), ["admin", "ta_engine", "telegram"])

    assert states(plan) == {"telegram": READY, "ta_engine": SKIPPED, "admin": RESTARTING}
    assert "sign in again" in plan["steps"][2]["detail"]
    assert control.restarted == ["telegram", "admin"]


def test_one_plan_at_a_time():
    plans = plans_with(FakeControl())

    async def two():
        plans.start(["telegram"], forced=False, by=None)
        with pytest.raises(RuntimeError, match="one at a time"):
            plans.start(["trader"], forced=False, by=None)
        await plans._task

    asyncio.run(two())


# ── What the guards and the readiness read ──


class Queries:
    def __init__(self):
        self.open = 0
        self.heartbeats = {}
        self.capital = None
        self.recorder = None
        self.engine = None
        self.failing = False

    async def list_positions(self, status=None, **_):
        if self.failing:
            raise ServiceUnavailable("down")
        return {"items": [{"position_id": str(number)} for number in range(self.open)]}

    async def service_heartbeats(self):
        return self.heartbeats

    async def capital_feed_state(self):
        return self.capital

    async def price_recorder_status(self):
        return self.recorder

    async def ta_engine_state(self):
        return self.engine


class Services:
    watchlist_enabled = True

    def __init__(self):
        self.consumers = {"trading-ops": 1, "telegram_channel": 1}

    async def latest_signals(self):
        return [{"indice": "us100"}]

    async def webhook_state(self):
        return None

    async def watchlist_health(self):
        return {"ready": True}

    async def queue_stats(self):
        return [{"name": name, "consumers": count} for name, count in self.consumers.items()]


class Backtests:
    under_way = 0


class Studies:
    busy = False


class Settings:
    def __init__(self, config_path, log_path):
        self.config_path = config_path
        self.log_path = log_path


class Context:
    def __init__(self, tmp_path, config):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(config))
        self.settings = Settings(str(path), str(tmp_path / "log"))
        self.queries = Queries()
        self.services = Services()
        self.backtests = Backtests()
        self.studies = Studies()
        self.ta_scripts = None


HOURS = {
    "trade": {
        "config": {"general": {"timezone": "Europe/Paris"}},
        "rules": [
            {"rule_type": "market_hours", "rule_config": {"trading_start_hour": 9, "trading_end_hour": 16,
                                                           "timezone": "America/New_York"}},
            {"rule_type": "day_trading", "rule_config": {"close_position_time": "15:55"}},
        ],
    },
}


def new_york(day, hour, minute=0) -> float:
    return pytz.timezone("America/New_York").localize(datetime(2026, 10, day, hour, minute)).timestamp()


def test_the_situation_is_read_from_the_database_the_heartbeats_and_the_file(tmp_path):
    ctx = Context(tmp_path, HOURS)
    ctx.queries.open = 1
    ctx.queries.heartbeats = {"position_monitor": {
        "beat_at": datetime.now(timezone.utc), "figures": {"positions_listed": 2, "close_intents": [{"position_id": 5001}]},
    }}
    ctx.backtests.under_way = 1
    ctx.studies.busy = True

    found = asyncio.run(situation_of(ctx, new_york(23, 10)))

    assert found == Situation(positions_open=1, positions_listed=2, close_intents=("5001",), market_open=True,
                              live_rules=0, backtests_running=1, study_running=True, scheduler_job_near=None)
    # Friday 15:54 in New York: the close of the day is near; Saturday the market is closed.
    assert asyncio.run(situation_of(ctx, new_york(23, 15, 54))).scheduler_job_near.startswith("the close of the day")
    assert asyncio.run(situation_of(ctx, new_york(24, 10))).market_open is False


def test_an_old_heartbeat_says_nothing_and_an_unreadable_database_holds_the_trading_path(tmp_path):
    ctx = Context(tmp_path, HOURS)
    ctx.queries.heartbeats = {"position_monitor": {
        "beat_at": datetime.now(timezone.utc) - timedelta(minutes=5), "figures": {"positions_listed": 2},
    }}
    ctx.queries.failing = True

    found = asyncio.run(situation_of(ctx, new_york(24, 10)))

    assert found.positions_listed == 0 and found.positions_open is None
    assert "cannot be read" in refusal("trader", found)


def test_an_unreadable_file_counts_as_an_open_market(tmp_path):
    ctx = Context(tmp_path, HOURS)
    os.remove(ctx.settings.config_path)

    assert asyncio.run(situation_of(ctx, new_york(24, 10))).market_open is True


def test_a_service_is_at_work_again_by_what_it_leaves_once_started(tmp_path):
    ctx = Context(tmp_path, HOURS)
    readiness = Readiness(ctx)
    started = datetime(2026, 10, 24, 8, tzinfo=timezone.utc)
    before, after = started - timedelta(minutes=5), started + timedelta(seconds=20)

    def ready(service):
        return asyncio.run(readiness.ready(service, started, None))[0]

    ctx.queries.heartbeats = {"trader": {"started_at": before}, "position_monitor": {"started_at": after}}
    assert ready("trader") is False
    ctx.queries.heartbeats["trader"] = {"started_at": after}
    assert ready("trader") is True
    ctx.services.consumers["trading-ops"] = 0
    assert ready("position_monitor") is False
    ctx.services.consumers["telegram_channel"] = 0
    assert ready("telegram") is False
    assert ready("web_server") is True and ready("watchlist_manager") is True
    ctx.queries.recorder = {"ts": before}
    assert ready("price_recorder") is False
    ctx.queries.recorder = {"ts": after}
    assert ready("price_recorder") is True
    ctx.queries.capital = {"started_at": after, "state": "streaming", "detail": "2 epics"}
    assert asyncio.run(readiness.ready("capital_feed", started, None)) == (True, "streaming: 2 epics")
    ctx.queries.engine = {"started_at": before.isoformat()}
    assert ready("ta_engine") is False


def test_the_scheduler_is_at_work_again_once_its_log_says_a_new_start(tmp_path):
    ctx = Context(tmp_path, HOURS)
    log = tmp_path / "log" / "wata-scheduler" / "wata-scheduler.log"
    log.parent.mkdir(parents=True)
    log.write_text("2026-10-24 07:00:01,000 - INFO - root - WATA scheduler is running\n")
    readiness = Readiness(ctx)
    before = asyncio.run(readiness.before("scheduler"))

    assert asyncio.run(readiness.ready("scheduler", None, before))[0] is False
    with open(log, "a") as file:
        file.write("2026-10-24 08:00:00,000 - INFO - root - WATA scheduler stopped\n")
        file.write("2026-10-24 08:00:05,000 - INFO - root - Started wata-scheduler application\n")
    assert asyncio.run(readiness.ready("scheduler", None, before))[0] is False
    with open(log, "a") as file:
        file.write("2026-10-24 08:00:06,000 - INFO - root - WATA scheduler is running\n")
    assert asyncio.run(readiness.ready("scheduler", None, before)) == (True, "Running since 2026-10-24 08:00:06 (from its log)")


# ── The System page's routes ──


@pytest.fixture
def ctx(config_path, tmp_path):
    return make_ctx(config_path, tmp_path)


def signed_in(ctx, tmp_path):
    client = make_client(ctx, tmp_path / "no-interface")
    client.__enter__()
    assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
    return client


@pytest.fixture
def guards(monkeypatch):
    """The situation the routes read, set by the test."""
    held = {"situation": Situation()}

    async def fake(_ctx, now=None):
        return held["situation"]

    monkeypatch.setattr("src.admin.routers.services.situation_of", fake)
    return held


def test_without_the_control_service_the_page_counts_changes_from_the_admins_start(ctx, tmp_path, guards):
    client = signed_in(ctx, tmp_path)
    try:
        found = client.get("/api/system/services").json()
    finally:
        client.__exit__(None, None, None)

    assert found["available"] is False and found["starts_known"] is False
    assert "on the server" in found["error"]
    assert [service["id"] for service in found["services"]] == list(ORDER)
    trader = found["services"][ORDER.index("trader")]
    assert trader["cost"] and trader["refusal"] is None and trader["trading_path"] is True


def test_each_service_is_said_with_its_container_its_pending_changes_and_what_holds_it_back(ctx, tmp_path, guards):
    control = FakeControl()
    control.states["ta_engine"] = {"service": "ta_engine", "state": "absent"}
    ctx.service_control = control
    guards["situation"] = Situation(positions_open=1)
    client = signed_in(ctx, tmp_path)
    try:
        found = client.get("/api/system/services").json()
    finally:
        client.__exit__(None, None, None)

    by_id = {service["id"]: service for service in found["services"]}
    assert found["available"] is True and found["starts_known"] is True
    assert by_id["trader"]["started_at"] == STARTED and by_id["trader"]["running"] is True
    assert by_id["trader"]["refusal"] == "1 position open"
    assert by_id["telegram"]["refusal"] is None
    assert by_id["ta_engine"]["state"] == "absent" and by_id["ta_engine"]["pending"] == {"paths": [], "outside": False}


def test_a_restart_held_back_is_refused_unless_asked_anyway_and_then_recorded(ctx, tmp_path, guards):
    control = FakeControl()
    ctx.service_control = control
    ctx.restarts = plans_with(control)
    guards["situation"] = Situation(market_open=True)
    client = signed_in(ctx, tmp_path)
    try:
        refused = client.post("/api/system/services/trader/restart", json={}, headers=SAME_ORIGIN)
        taken = client.post("/api/system/services/trader/restart", json={"force": True}, headers=SAME_ORIGIN)
    finally:
        client.__exit__(None, None, None)

    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("Held back: Trader: the market is open by WATA's hours")
    assert taken.status_code == 202 and taken.json()["plan"]["forced"] is True
    audit = ctx.audit_log.read(5)
    assert audit[0]["action"] == "restart Trader ANYWAY (Trader: the market is open by WATA's hours)"


def test_a_plan_of_several_is_asked_in_one_request(ctx, tmp_path, guards):
    control = FakeControl()
    ctx.service_control = control
    ctx.restarts = plans_with(control)
    client = signed_in(ctx, tmp_path)
    try:
        taken = client.post("/api/system/services/restart", json={"services": ["admin", "scheduler", "telegram"]},
                            headers=SAME_ORIGIN)
        unknown = client.post("/api/system/services/restart", json={"services": ["postgres"]}, headers=SAME_ORIGIN)
        one_unknown = client.post("/api/system/services/postgres/restart", json={}, headers=SAME_ORIGIN)
    finally:
        client.__exit__(None, None, None)

    assert taken.status_code == 202
    assert taken.json()["plan"]["services"] == ["telegram", "scheduler", "admin"]
    assert ctx.audit_log.read(5)[0]["action"] == "restart Telegram, Scheduler, Admin"
    assert (unknown.status_code, one_unknown.status_code) == (422, 404)


def test_no_restart_without_the_control_service_nor_with_the_actions_off(ctx, config_path, tmp_path, guards):
    client = signed_in(ctx, tmp_path)
    try:
        assert client.post("/api/system/services/telegram/restart", json={}, headers=SAME_ORIGIN).status_code == 503
    finally:
        client.__exit__(None, None, None)

    off = make_ctx(config_path, tmp_path, actions_enabled=False)
    off.service_control = FakeControl()
    client = signed_in(off, tmp_path)
    try:
        assert client.post("/api/system/services/telegram/restart", json={}, headers=SAME_ORIGIN).status_code == 403
    finally:
        client.__exit__(None, None, None)


def test_the_top_bar_names_the_services_that_run_without_a_saved_change(ctx, tmp_path, guards):
    # Started once the file was written.
    ctx.service_control = FakeControl(started=datetime.now(timezone.utc).isoformat())
    client = signed_in(ctx, tmp_path)
    try:
        before = client.get("/api/session").json()
        loaded = client.get("/api/config").json()
        config = loaded["config"]
        for rule in config["trade"]["rules"]:
            if rule["rule_type"] == "market_hours":
                rule["rule_config"]["trading_start_hour"] = 7
        saved_answer = client.put("/api/config", json={"config": config, "revision": loaded["revision"]}, headers=SAME_ORIGIN)
        after = client.get("/api/session").json()
    finally:
        client.__exit__(None, None, None)

    assert saved_answer.status_code == 200
    assert before["restart_services"] == [] and before["restart_required"] is False
    # The containers started before the save: those that read the trading hours run without them.
    assert [service["id"] for service in after["restart_services"]] == [
        "price_recorder", "scheduler", "position_monitor", "trader", "admin",
    ]
    assert after["restart_services"][1] == {"id": "scheduler", "name": "Scheduler"}
    assert after["restart_required"] is True
