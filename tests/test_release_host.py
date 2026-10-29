"""
The server's side of a deploy (src/release/host.py and watadeploy,
src/release/cli.py), over a Docker made up in memory: the pins, a plan read
from the server, applying it with its guards and readiness, a container that
does not stay up put back, the admin's requests, rollback, prune.
"""

import json
import os
import subprocess

import pytest

from src.release import cli
from src.release.host import (
    BACKUP_CONTAINER,
    NOT_READY,
    READY,
    ROLLED_BACK,
    SKIPPED,
    TOOLS_VARIABLE,
    DeployError,
    Docker,
    Host,
    Layout,
    pin_variable,
    read_pins,
    version_of,
    write_pins,
)

OLD, NEW = "0.59.1", "0.61.0"
OLD_ID, NEW_ID = "sha256:" + "a" * 64, "sha256:" + "b" * 64
CONTAINERS = {"admin1": "admin", "trader1": "trader", "ta_engine1": "ta_engine", "price_recorder1": "price_recorder"}


def files_of(role: str, version: str) -> dict:
    shared = {"src/trade/rules.py": "r1", "src/start_python_script.sh": "s1"}
    own = {"admin": {"src/admin/app.py": "a1" if version == OLD else "a2"},
           "trader": {"src/trader/__init__.py": "t1"},
           "ta_engine": {"src/ta_engine/engine.py": "e1"},
           "price_recorder": {"src/price_recorder/__init__.py": "p1"}}[role]
    return {**shared, **own}


def change(docker, path: str, value: str) -> None:
    """A file of the newest release changed, and the code of every container that holds it."""
    found = docker.manifests[NEW_ID]
    found["files"][path] = value
    for entry in found["containers"].values():
        if path in entry["files"]:
            entry["code"] = str(sorted((name, found["files"][name]) for name in entry["files"]))


def made_manifest(version: str) -> dict:
    files, containers = {}, {}
    for name, role in CONTAINERS.items():
        mine = files_of(role, version)
        files.update(mine)
        containers[name] = {"role": role, "files": sorted(mine), "code": str(sorted(mine.items())),
                            "libraries": "l", "distributions": {"fastapi": "1"}, "imports": []}
    return {"manifest": 1, "version": version, "files": files, "containers": containers,
            "platform": {"digest": "p", "python": "3.14.10", "node": "v24"}}


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0  # a Friday evening, 2027-01-15 08:00 UTC... the market is set per test

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeDocker:
    """What Host asks of Docker, kept in memory."""

    def __init__(self, layout: Layout, clock: Clock):
        self.layout = layout
        self.clock = clock
        self.images = {f"wata-base:{NEW}": {"Id": NEW_ID, "Config": {"Env": ["A=1"], "Cmd": ["start"]}},
                       "wata-base:latest": {"Id": OLD_ID, "Config": {"Env": ["A=1"], "Cmd": ["start"]}},
                       f"wata-backup:{NEW}": {"Id": "sha256:" + "c" * 64, "Config": {}}}
        self.manifests = {OLD_ID: made_manifest(OLD), NEW_ID: made_manifest(NEW)}
        self.running = {}
        for name in [*CONTAINERS, BACKUP_CONTAINER]:
            self.running[name] = self._container(name, OLD_ID, "wata-base:latest" if name != BACKUP_CONTAINER
                                                 else "wata-backup:latest")
        self.calls = []
        self.positions = "0"
        self.beat = None
        self.started_rows = {}
        self.crash = set()        # images on which a container exits at once
        self.mute = set()         # containers never seen at work
        self.top = {"admin1": ["python -u /app/src/admin/__init__.py"], BACKUP_CONTAINER: ["sh /pg_backup.sh"]}
        self.config_refused = set()
        self.definitions = {}

    def _container(self, name, image_id, reference):
        return {"Id": name + "-id", "Image": image_id, "Name": f"/{name}",
                "Config": {"Image": reference, "Labels": {
                    "com.docker.compose.service": name, "com.docker.compose.project": "deploy",
                    "com.docker.compose.project.config_files": f"{self.layout.base}/{OLD}/deploy/docker-compose.yml"}},
                "State": {"Running": True, "Restarting": False, "Status": "running",
                          "StartedAt": f"2027-01-15T07:00:00.{len(name):09d}Z"}}

    # the interface Host uses
    def project(self):
        return "deploy"

    def call(self, arguments, **_):
        self.calls.append(("call", tuple(arguments)))
        return subprocess.CompletedProcess(arguments, 0, "", "")

    def compose(self, version, arguments, *, files=None, timeout=300, check=True):
        self.calls.append(("compose", version, tuple(arguments), tuple(files or ())))
        if arguments[:2] == ["config", "--services"]:
            return subprocess.CompletedProcess(arguments, 0, "\n".join([*CONTAINERS, BACKUP_CONTAINER, "postgres1"]), "")
        if arguments[:1] == ["config"]:
            key = "old" if files else "new"
            services = self.definitions.get(key) or {name: {"environment": ["X=1"]} for name in
                                                     [*CONTAINERS, BACKUP_CONTAINER, "postgres1"]}
            return subprocess.CompletedProcess(arguments, 0, json.dumps({"services": services}), "")
        if arguments[:1] == ["up"]:
            name = arguments[-1]
            reference = read_pins(self.layout.pins)[pin_variable(name)]
            image_id = self.images[reference]["Id"]
            container = self._container(name, image_id, reference)
            container["State"]["StartedAt"] = f"2027-01-15T09:{int(self.clock.now) % 60:02d}:00.000000001Z"
            if image_id in self.crash:
                container["State"].update(Running=False, Status="exited", ExitCode=1)
            self.running[name] = container
            self.started_rows[CONTAINERS.get(name, name)] = container["State"]["StartedAt"]
            return subprocess.CompletedProcess(arguments, 0, "", "")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    def containers(self):
        return {name: json.loads(json.dumps(found)) for name, found in self.running.items()}

    def inspect(self, name):
        found = self.running.get(name)
        return json.loads(json.dumps(found)) if found else None

    def image(self, reference):
        if reference.startswith("sha256:"):
            return next((image for image in self.images.values() if image["Id"] == reference), None)
        return self.images.get(reference)

    def manifest(self, image_id):
        self.calls.append(("manifest", image_id))
        return self.manifests[image_id]

    def psql(self, sql):
        if "turbo_data_position" in sql:
            return self.positions
        if "json_build_object" in sql:
            return self.beat
        def epoch(started):
            from src.release.guards import moment

            return None if started is None else str(moment(started).timestamp())

        assert "extract(epoch FROM" in sql, sql
        for role, started in self.started_rows.items():
            if f"'{role}'" in sql or f"{role}_state" in sql:
                return None if role in self.mute else epoch(started)
        if "price_recorder_coverage" in sql:
            return None if "price_recorder" in self.mute else epoch(self.started_rows.get("price_recorder"))
        return None

    def consumers(self):
        return {"trading-ops": 1, "telegram_channel": 1}

    def processes(self, container):
        return self.top.get(container, [])

    def answers(self, container, url):
        return (False, "no answer yet") if container in self.mute else (True, "answering (200)")

    def tag(self, source, target):
        self.calls.append(("tag", source, target))
        self.images[target] = dict(self.image(source))

    def remove_image(self, reference):
        self.calls.append(("rm", reference))
        return self.images.pop(reference, None) is not None

    def check_config(self, image):
        return (False, "ValueError: Missing required configuration sections: x") if image in self.config_refused else (
            True, "accepted")


@pytest.fixture
def server(tmp_path):
    base = tmp_path / "wata"
    for version in (OLD, NEW, "0.58.0", "0.52.1"):
        deploy = base / version / "deploy"
        deploy.mkdir(parents=True)
        (deploy / "docker-compose.yml").write_text("services: {}\n")
        (deploy / "pg_backup.Dockerfile").write_text("FROM postgres:16-alpine\n")
        (deploy / "pg_backup.sh").write_text("echo dump\n")
    (base / "VERSION").write_text(NEW)
    (base / "etc").mkdir()
    # The market by WATA's hours: 02:00-16:00 in New York; the clock below is 03:00 there on a Friday.
    config = {"trade": {"config": {"general": {"timezone": "Europe/Paris"}},
                        "rules": [{"rule_type": "market_hours", "rule_config": {
                            "trading_start_hour": 2, "trading_end_hour": 16, "timezone": "America/New_York"}}]}}
    (base / "etc" / "config.json").write_text(json.dumps(config))
    layout = Layout(base=str(base))
    clock = Clock()
    docker = FakeDocker(layout, clock)
    lines = []
    host = Host(layout, docker, clock=clock, sleep=clock.sleep, out=lines.append)
    host.bootstrap()
    # A server past its first deploy: the backups on a release's own image.
    docker.images[f"wata-backup:{OLD}"] = {"Id": "sha256:" + "e" * 64, "Config": {}}
    pins = read_pins(layout.pins)
    pins[pin_variable(BACKUP_CONTAINER)] = f"wata-backup:{OLD}"
    write_pins(layout.pins, pins)
    return host, docker, clock, lines


def market(clock: Clock, open_: bool) -> None:
    # Friday 2027-01-15: 08:00 UTC is 03:00 in New York (open), 22:00 UTC is 17:00 (closed).
    clock.now = 1_768_464_000.0 + (8 if open_ else 22) * 3600


def test_the_first_pins_are_the_images_that_run_so_that_nothing_is_recreated(server):
    host, docker, _, _ = server
    pins = read_pins(host.layout.pins)
    del pins[pin_variable(BACKUP_CONTAINER)]
    write_pins(host.layout.pins, pins)
    host.bootstrap()
    pins = read_pins(host.layout.pins)
    assert pins[pin_variable("admin1")] == f"wata-base:{OLD}"
    assert docker.images[f"wata-base:{OLD}"]["Id"] == OLD_ID
    assert pins[pin_variable(BACKUP_CONTAINER)] == "wata-backup:before-" + "a" * 12
    assert pins[TOOLS_VARIABLE] == f"wata-base:{NEW}"
    assert not [call for call in docker.calls if call[0] == "compose" and call[2][:1] == ("up",)]
    # Bootstrapped twice, nothing changes.
    host.bootstrap()
    assert read_pins(host.layout.pins) == pins


def test_the_first_deploy_moves_the_backups_too_whose_image_names_no_release(server):
    host, docker, _, _ = server
    pins = read_pins(host.layout.pins)
    pins[pin_variable(BACKUP_CONTAINER)] = "wata-backup:before-" + "a" * 12
    write_pins(host.layout.pins, pins)
    _, steps = host.plan()
    backup = next(step for step in steps if step.container == BACKUP_CONTAINER)
    assert backup.kind == "change" and "unknown" in backup.reasons


def test_an_admin_only_release_goes_now_with_the_market_open_and_nothing_else_moves(server):
    host, docker, clock, lines = server
    market(clock, True)
    docker.positions = "1"
    record = host.apply()
    moved = {step["container"]: step for step in record["steps"]}
    assert set(moved) == {"admin1"} and moved["admin1"]["state"] == READY
    assert moved["admin1"]["before"] == f"wata-base:{OLD}" and moved["admin1"]["after"] == f"wata-base:{NEW}"
    ups = [call for call in docker.calls if call[0] == "compose" and call[2][:1] == ("up",)]
    assert ups == [("compose", NEW, ("up", "-d", "--no-deps", "--force-recreate", "--pull", "never", "admin1"), ())]
    assert docker.running["trader1"]["Image"] == OLD_ID
    assert read_pins(host.layout.pins)[pin_variable("admin1")] == f"wata-base:{NEW}"
    # A dump first, the history and the status written.
    assert ("call", ("exec", BACKUP_CONTAINER, "sh", "/pg_backup.sh", "now")) in docker.calls
    assert host.history()[-1]["steps"][0]["state"] == READY
    status = host.read_status()
    assert status["last"]["id"] == record["id"] and status["applying"] is None
    assert {entry["container"]: entry["kind"] for entry in status["containers"]}["admin1"] == "unchanged"


def test_a_change_of_the_trader_waits_for_the_close(server):
    host, docker, clock, _ = server
    change(docker, "src/trade/rules.py", "r2")
    market(clock, True)
    _, steps = host.plan()
    when = {step.container: step.when for step in steps}
    assert when["trader1"] == "pending" and when["price_recorder1"] == "pending"
    # They share src/trade/rules.py with the admin and the TA engine: the whole group waits.
    assert when["admin1"] == when["ta_engine1"] == "pending"
    market(clock, False)
    _, steps = host.plan()
    assert {step.container: step.when for step in steps}["trader1"] == "now"


def test_what_cannot_be_read_holds_the_trading_path_back(server):
    host, docker, clock, _ = server
    change(docker, "src/trade/rules.py", "r2")
    market(clock, False)
    docker.positions = None
    _, steps = host.plan()
    trader = next(step for step in steps if step.container == "trader1")
    assert trader.when == "pending" and "cannot be read" in trader.held


def test_the_admin_waits_for_its_backtests_and_its_agent(server):
    host, docker, clock, _ = server
    docker.top["admin1"] = ["python -u /app/src/admin/__init__.py", "python -u -m src.backtest.run --run x"]
    _, steps = host.plan()
    assert next(step for step in steps if step.container == "admin1").held == "1 backtest running"
    docker.top["admin1"] = ["python -u /app/src/admin/__init__.py"]
    conversation = os.path.join(host.layout.base, "var/lib/admin/agent/conversations/c1")
    os.makedirs(conversation)
    with open(os.path.join(conversation, "meta.json"), "w") as meta:
        json.dump({"status": "running"}, meta)
    _, steps = host.plan()
    assert "AI agent" in next(step for step in steps if step.container == "admin1").held


def test_a_container_that_does_not_stay_up_goes_back_and_the_rest_waits(server):
    host, docker, clock, _ = server
    change(docker, "src/trade/rules.py", "r2")
    market(clock, False)
    docker.crash.add(NEW_ID)
    docker.images[f"wata-base:{OLD}"] = docker.images["wata-base:latest"]
    record = host.apply()
    states = {step["container"]: step["state"] for step in record["steps"]}
    first = record["steps"][0]["container"]
    assert states[first] == ROLLED_BACK
    assert all(state == SKIPPED for name, state in states.items() if name != first)
    assert read_pins(host.layout.pins)[pin_variable(first)] == f"wata-base:{OLD}"
    assert cli._outcome(record) == 3


def test_a_container_not_seen_at_work_stops_the_apply_and_stays(server):
    host, docker, clock, _ = server
    docker.mute.add("admin1")
    record = host.apply()
    assert record["steps"][0]["state"] == NOT_READY
    assert read_pins(host.layout.pins)[pin_variable("admin1")] == f"wata-base:{NEW}"


def test_rollback_puts_the_last_apply_back(server):
    host, docker, clock, _ = server
    host.apply()
    record = host.rollback()
    assert [(step["container"], step["after"]) for step in record["steps"]] == [("admin1", f"wata-base:{OLD}")]
    assert read_pins(host.layout.pins)[pin_variable("admin1")] == f"wata-base:{OLD}"
    with pytest.raises(DeployError):
        host.rollback(["trader1"])


def test_one_apply_at_a_time(server):
    host, _, _, _ = server
    with host.locked():
        with pytest.raises(DeployError, match="one at a time"):
            host.apply()


def _request(host: Host, clock: Clock, name: str = "0" * 32, **changes) -> str:
    request = {"id": name, "version": NEW, "containers": ["admin1"], "force": False, "asked_by": "operator",
               "asked_at": __import__("datetime").datetime.fromtimestamp(clock.now, __import__("datetime").timezone.utc)
               .isoformat()}
    request.update(changes)
    os.makedirs(host.layout.requests_in, exist_ok=True)
    path = os.path.join(host.layout.requests_in, f"{name}.json")
    with open(path, "w") as request_file:
        json.dump(request, request_file)
    return path


def test_a_request_of_the_admin_is_taken_out_of_its_reach_then_applied(server):
    host, docker, clock, _ = server
    path = _request(host, clock)
    assert host.serve_requests() == 1
    assert not os.path.exists(path)
    assert os.path.exists(os.path.join(host.layout.requests_kept, os.path.basename(path)))
    assert docker.running["admin1"]["Image"] == NEW_ID
    assert host.read_status()["requests"][0]["state"] == "done"
    assert host.history()[-1]["by"] == "admin (operator)"


@pytest.mark.parametrize("changes, said", [
    ({"version": OLD}, "newest release"),
    ({"containers": ["trader1"]}, "not waiting"),
    ({"containers": []}, "no container"),
    ({"asked_at": "2020-01-01T00:00:00+00:00"}, "too long ago"),
    ({"force": "yes"}, "not a request"),
    ({"shell": "rm -rf /"}, "not a request"),
])
def test_a_request_out_of_bounds_is_refused_and_said(server, changes, said):
    host, docker, clock, _ = server
    _request(host, clock, **changes)
    host.serve_requests()
    found = host.read_status()["requests"][0]
    assert found["state"] == "refused" and said in found["detail"]
    assert docker.running["admin1"]["Image"] == OLD_ID


def test_a_link_or_a_stranger_in_the_requests_folder_is_never_followed(server, tmp_path):
    host, docker, clock, _ = server
    secret = tmp_path / "secret.txt"
    secret.write_text('{"id": "x"}')
    os.makedirs(host.layout.requests_in, exist_ok=True)
    os.symlink(secret, os.path.join(host.layout.requests_in, "1" * 32 + ".json"))
    (tmp_path / "wata/var/lib/admin/deploy/requests/notes.txt").write_text("hello")
    host.serve_requests()
    assert host.read_status()["requests"][0]["state"] == "refused"
    assert os.listdir(host.layout.requests_in) == []
    assert secret.read_text() == '{"id": "x"}'


def test_prune_keeps_the_newest_and_whatever_is_still_named(server):
    host, docker, _, _ = server
    docker.images["wata-base:0.52.1"] = {"Id": "sha256:" + "d" * 64}
    removed = host.prune(keep=2)
    # 0.61.0 and 0.59.1 are the newest two (and 0.59.1 is pinned); 0.58.0 is named by the containers' labels? no.
    assert "0.52.1" in removed and "0.58.0" in removed
    assert not os.path.isdir(os.path.join(host.layout.base, "0.52.1"))
    assert os.path.isdir(os.path.join(host.layout.base, OLD))
    assert ("rm", "wata-base:0.52.1") in docker.calls


def test_config_json_checked_against_every_image_in_use(server):
    host, docker, _, _ = server
    docker.config_refused.add(f"wata-base:{OLD}")
    found = {entry["image"]: entry for entry in host.check_config()}
    assert found[f"wata-base:{OLD}"]["accepted"] is False
    assert set(found[f"wata-base:{OLD}"]["containers"]) == set(CONTAINERS)
    assert found[f"wata-base:{NEW}"]["accepted"] is True
    assert host.read_status()["config"]["images"]


def test_a_compose_definition_is_compared_without_the_pins_and_with_the_infrastructures_image():
    before = {"image": "wata-base:0.59.1", "environment": ["A=1"], "depends_on": {"x": {}}}
    after = {"image": "wata-base:0.61.0", "environment": ["A=1"], "depends_on": {}}
    assert Host.compose_changes(before, after) == []
    assert Host.compose_changes({**before, "image": "traefik:v3.7.7"}, {**after, "image": "traefik:v3.7.8"},
                                pinned=False) == ["image"]
    assert Host.compose_changes(None, after) is None
    assert Host.compose_changes({}, after) == ["environment"]


def test_the_names_of_pins_and_releases():
    assert pin_variable("pg_backup1") == "WATA_IMAGE_PG_BACKUP1"
    assert version_of("wata-base:0.61.0") == "0.61.0"
    assert version_of("wata-base:0.59.1-abcdef123456") == "0.59.1"
    assert version_of("wata-base:latest") is None and version_of("wata-backup:before-abc") is None


def test_the_pins_file_is_written_whole_and_read_back(tmp_path):
    path = tmp_path / "release" / "images.env"
    write_pins(str(path), {"WATA_IMAGE_ADMIN1": "wata-base:0.61.0"})
    assert read_pins(str(path)) == {"WATA_IMAGE_ADMIN1": "wata-base:0.61.0"}
    assert path.read_text().startswith("# ")


def test_the_real_docker_commands_are_the_ones_meant(tmp_path):
    """The command lines the server runs: both env files, the release's folder, no pull, the exact psql."""
    seen = []

    def runner(command, **options):
        seen.append((command, options.get("cwd")))
        if command[1] == "exec" and command[2] == "postgres":
            return subprocess.CompletedProcess(command, 0, "3\n", "")
        if command[1] == "run" and "-c" in command:
            return subprocess.CompletedProcess(command, 1, "REFUSED ValueError: Missing required x\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    layout = Layout(base=str(tmp_path))
    docker = Docker(layout, binary="docker", runner=runner)
    docker.compose(NEW, ["up", "-d", "--no-deps", "--pull", "never", "admin1"])
    command, cwd = seen[-1]
    assert command[:6] == ["docker", "compose", "--env-file", f"{tmp_path}/.env", "--env-file",
                           f"{tmp_path}/release/images.env"]
    assert cwd == f"{tmp_path}/{NEW}/deploy"
    assert docker.psql("SELECT 1") == "3"
    assert seen[-1][0][:4] == ["docker", "exec", "postgres", "psql"]
    assert docker.check_config("wata-base:0.59.1") == (False, "ValueError: Missing required x")


def test_watadeploy_says_the_plan_and_fails_on_a_container_that_did_not_come_back(server, capsys):
    host, docker, clock, lines = server
    assert cli.main(["plan"], host=host) == 0
    assert any(line.startswith("  admin1: code (1 file: src/admin/app.py) → now") for line in lines)
    docker.crash.add(NEW_ID)
    assert cli.main(["apply", "--services", "admin1"], host=host) == 3
    assert cli.main(["apply", "--services", "nobody1"], host=host) == 1
