"""
The server's side of a deploy: what runs (Docker, the compose files, the
pins), what holds a container back (the database, the heartbeats, the files),
recreating one on another image and seeing it at work again, the requests the
admin leaves, and the status the admin reads.

/app/wata/release/ is the deploy's own folder, out of every container's
reach but the status, which the admin mounts read-only:

    images.env          the image each container runs (compose reads it after .env)
    history.jsonl       each apply, rollback and full restart
    manifests/          what each image runs (src/release/manifest.py), by image id
    requests/           the admin's requests, once taken out of its folder
    status/status.json  what the System page shows
    .lock               one apply at a time

Standard library only: the server's own python3 runs it (``watadeploy``).
"""

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.heartbeat import HEARTBEAT_STALE_SECONDS
from src.release import manifest as manifests
from src.release.guards import ORDER, Situation, deploy_refusal, market_is_open, moment, scheduler_job_near
from src.release.plan import BACKUP, CHANGE, INFRA, NOW, OPTIONAL, WATA, Container, Step, make_plan, said, summary

BASE = "/app/wata"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
WATA_IMAGE = "wata-base"
BACKUP_IMAGE = "wata-backup"
BACKUP_CONTAINER = "pg_backup1"
INFRASTRUCTURE = ("traefik", "postgres1", "rabbitmq1")
# The containers of the infrastructure, by their compose service (container_name differs).
DATABASE_CONTAINER = "postgres"
QUEUE_CONTAINER = "rabbitmq"
TOOLS_VARIABLE = "WATA_IMAGE_TOOLS"
# What a container's compose definition is compared without: WATA's images are the pins' (the
# infrastructure's image is compared), and the order of starting says nothing of the container itself.
NOT_COMPARED = ("image", "depends_on")
# The admin's children whose restart would stop them (src/admin/processes.py launches them by name).
BACKTEST_MODULES = ("src.backtest.run", "src.backtest.turbo_model")
STUDY_MODULES = ("src.research.study",)
# What the AI agent takes up again after a restart (src/admin/agent/transcript.py).
AGENT_UNDER_WAY = ("queued", "running", "waiting")
# The queues a service consumes once at work (src/admin/services.py).
OPS_QUEUE = "trading-ops"
TELEGRAM_QUEUE = "telegram_channel"
SCHEDULER_LOG = ("wata-scheduler", ("WATA scheduler is running", "Started wata-scheduler application",
                                    "WATA scheduler stopped"), "WATA scheduler is running")
# Waits, as the admin's restarts allow them (src/admin/service_restart.py).
RUNNING_TIMEOUT_SECONDS = 180
READY_TIMEOUT_SECONDS = 180
# Running this long without a restart is "staying up".
STAY_UP_SECONDS = 15
POLL_SECONDS = 2
# The admin's requests.
REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
REQUEST_FIELDS = frozenset({"id", "version", "containers", "force", "asked_by", "asked_at"})
MAX_REQUEST_BYTES = 4096
REQUEST_MAX_AGE_SECONDS = 600
KEPT_REQUESTS = 50
# Releases kept by prune, the newest first, beside every one still named.
KEEP_RELEASES = 3
# Run in an image on config.json, read-only: its own validator, its refusal cleaned as a log line is.
CONFIG_PROBE = """
import logging, sys
logging.disable(logging.CRITICAL)
try:
    from src.configuration import ConfigurationManager
    ConfigurationManager("/app/etc/config.json")
except Exception as e:
    try:
        from src.logging_helper.redaction import redact
        said = redact(str(e))
    except Exception:
        said = ""
    print("REFUSED", type(e).__name__ + ":", str(said).replace("\\n", " ")[:300])
    sys.exit(1)
print("ACCEPTED")
"""


class DeployError(Exception):
    """Something the deploy cannot do, said to whoever asked."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pin_variable(container: str) -> str:
    """The compose variable of a container's image: trader1 → WATA_IMAGE_TRADER1."""
    return "WATA_IMAGE_" + re.sub(r"[^A-Z0-9]", "_", container.upper())


def version_of(image: str | None) -> str | None:
    """The release an image reference names (``wata-base:0.61.0`` → 0.61.0), None for any other."""
    if not image or ":" not in image:
        return None
    tag = image.rsplit(":", 1)[1]
    return tag.split("-", 1)[0] if VERSION_PATTERN.match(tag.split("-", 1)[0]) else None


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _write_atomically(path: str, text: str, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as written:
        written.write(text)
        written.flush()
        os.fsync(written.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)


@dataclass(frozen=True)
class Layout:
    """Where everything is on the server."""

    base: str = BASE

    @property
    def release(self) -> str:
        return os.path.join(self.base, "release")

    @property
    def pins(self) -> str:
        return os.path.join(self.release, "images.env")

    @property
    def history(self) -> str:
        return os.path.join(self.release, "history.jsonl")

    @property
    def manifests(self) -> str:
        return os.path.join(self.release, "manifests")

    @property
    def lock(self) -> str:
        return os.path.join(self.release, ".lock")

    @property
    def status(self) -> str:
        return os.path.join(self.release, "status", "status.json")

    @property
    def requests_kept(self) -> str:
        return os.path.join(self.release, "requests")

    @property
    def requests_in(self) -> str:
        return os.path.join(self.base, "var", "lib", "admin", "deploy", "requests")

    @property
    def env(self) -> str:
        return os.path.join(self.base, ".env")

    @property
    def config(self) -> str:
        return os.path.join(self.base, "etc", "config.json")

    def deploy_dir(self, version: str) -> str:
        return os.path.join(self.base, version, "deploy")

    def host_path(self, container_path: str) -> str:
        """A path the admin's settings give inside its container (/app/var/lib/...), on the server."""
        if container_path.startswith("/app/"):
            return os.path.join(self.base, container_path[len("/app/"):])
        return container_path

    def newest(self) -> str:
        """The newest release installed: the one whose compose files every command uses."""
        try:
            with open(os.path.join(self.base, "VERSION"), encoding="utf-8") as version:
                found = version.read().strip()
        except OSError as e:
            raise DeployError(f"No release is installed ({self.base}/VERSION: {e.strerror})") from e
        if not VERSION_PATTERN.match(found):
            raise DeployError(f"{self.base}/VERSION does not name a release: {found!r}")
        return found

    def installed(self) -> list[str]:
        """The releases whose folder is on the server, the newest first."""
        found = []
        for name in os.listdir(self.base):
            if VERSION_PATTERN.match(name) and os.path.isfile(os.path.join(self.deploy_dir(name), "docker-compose.yml")):
                found.append(name)
        return sorted(found, key=version_key, reverse=True)


def read_pins(path: str) -> dict[str, str]:
    pins = {}
    try:
        with open(path, encoding="utf-8") as lines:
            for line in lines:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    name, _, value = line.partition("=")
                    pins[name.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return pins


def write_pins(path: str, pins: dict[str, str]) -> None:
    lines = ["# The image each container runs, written by watadeploy: compose reads it after /app/wata/.env.",
             "# Change it with watadeploy (apply, rollback), never by hand while one runs."]
    lines += [f"{name}={pins[name]}" for name in sorted(pins)]
    _write_atomically(path, "\n".join(lines) + "\n")


def read_env(path: str) -> dict[str, str]:
    """``NAME=value`` lines of .env, read without running it."""
    found = {}
    try:
        with open(path, encoding="utf-8") as lines:
            for line in lines:
                match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*['\"]?(.*?)['\"]?\s*$", line)
                if match:
                    found[match.group(1)] = match.group(2)
    except OSError:
        pass
    return found


class Docker:
    """The Docker commands a deploy needs, and nothing else."""

    def __init__(self, layout: Layout, binary: str | None = None, runner=subprocess.run):
        self._layout = layout
        self._binary = binary or os.environ.get("WATA_DOCKER", "docker")
        self._runner = runner

    def call(self, arguments: list[str], *, cwd: str | None = None, stdin: str | None = None,
             timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess:
        try:
            done = self._runner([self._binary, *arguments], cwd=cwd, input=stdin, capture_output=True, text=True,
                                timeout=timeout, check=False)
        except subprocess.TimeoutExpired as e:
            raise DeployError(f"docker {arguments[0]} gave no answer within {timeout:.0f} s") from e
        if check and done.returncode != 0:
            said = (done.stderr or done.stdout or "").strip().splitlines()
            raise DeployError(f"docker {' '.join(arguments[:3])} failed: {said[-1] if said else done.returncode}")
        return done

    def project(self) -> str:
        return read_env(self._layout.env).get("COMPOSE_PROJECT_NAME") or "deploy"

    def compose(self, version: str, arguments: list[str], *, files: list[str] | None = None, timeout: float = 300,
                check: bool = True) -> subprocess.CompletedProcess:
        """``docker compose`` in a release's folder, with .env and the pins; ``files`` instead of its own."""
        options = ["compose"]
        for path in files or []:
            options += ["-f", path]
        options += ["--env-file", self._layout.env, "--env-file", self._layout.pins]
        return self.call(options + arguments, cwd=self._layout.deploy_dir(version), timeout=timeout, check=check)

    def containers(self) -> dict[str, dict]:
        """The project's containers, by compose service, as ``docker inspect`` gives them."""
        listed = self.call(["ps", "-a", "-q", "--no-trunc", "--filter",
                            f"label=com.docker.compose.project={self.project()}"]).stdout.split()
        if not listed:
            return {}
        found = {}
        for container in json.loads(self.call(["inspect", *listed]).stdout or "[]"):
            service = ((container.get("Config") or {}).get("Labels") or {}).get("com.docker.compose.service")
            if service and not ((container.get("Config") or {}).get("Labels") or {}).get("com.docker.compose.oneoff") == "True":
                found[service] = container
        return found

    def inspect(self, container: str) -> dict | None:
        done = self.call(["inspect", container], check=False)
        if done.returncode != 0:
            return None
        found = json.loads(done.stdout or "[]")
        return found[0] if found else None

    def image(self, reference: str) -> dict | None:
        done = self.call(["image", "inspect", reference], check=False)
        if done.returncode != 0:
            return None
        found = json.loads(done.stdout or "[]")
        return found[0] if found else None

    def manifest(self, image_id: str) -> dict:
        """What an image runs (src/release/manifest.py, given on stdin to the image's own Python)."""
        with open(manifests.__file__, encoding="utf-8") as script:
            source = script.read()
        done = self.call(["run", "--rm", "-i", "--network", "none", "--entrypoint", "python", image_id,
                          "-", "--root", "/app"], stdin=source, timeout=180)
        return json.loads(done.stdout)

    def psql(self, sql: str) -> str | None:
        """One answer of the database, None when it cannot be read."""
        done = self.call(["exec", DATABASE_CONTAINER, "psql", "-X", "-q", "-U", "wata", "-d", "wata", "-tA",
                          "-v", "ON_ERROR_STOP=1", "-c", sql], check=False, timeout=30)
        return done.stdout.strip() if done.returncode == 0 else None

    def consumers(self) -> dict[str, int] | None:
        done = self.call(["exec", QUEUE_CONTAINER, "rabbitmqctl", "-q", "list_queues", "name", "consumers"],
                         check=False, timeout=30)
        if done.returncode != 0:
            return None
        found = {}
        for line in done.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                found[parts[0]] = int(parts[1])
        return found

    def processes(self, container: str) -> list[str] | None:
        """The command lines of a container's processes; None when Docker does not say."""
        # Docker maps the processes by their PID: without that column it refuses ("Couldn't find PID field").
        done = self.call(["top", container, "-eo", "pid,args"], check=False, timeout=30)
        if done.returncode != 0:
            return None
        return [(line.split(None, 1) + [""])[1] for line in done.stdout.splitlines()[1:] if line.strip()]

    def answers(self, container: str, url: str) -> tuple[bool, str]:
        """Whether a server inside ``container`` answers ``url`` at all (any HTTP status), and how."""
        probe = ("import sys, urllib.request, urllib.error\n"
                 "try:\n    print(urllib.request.urlopen(sys.argv[1], timeout=10).status)\n"
                 "except urllib.error.HTTPError as e:\n    print(e.code)\n")
        done = self.call(["exec", container, "python", "-c", probe, url], check=False, timeout=30)
        status = done.stdout.strip()
        return (True, f"answering ({status})") if done.returncode == 0 and status.isdigit() else (False, "no answer yet")

    def tag(self, source: str, target: str) -> None:
        self.call(["tag", source, target])

    def remove_image(self, reference: str) -> bool:
        return self.call(["image", "rm", reference], check=False).returncode == 0

    def check_config(self, image: str) -> tuple[bool, str]:
        """
        Whether ``image`` would start on config.json as it is now: its own
        ConfigurationManager, without network, its refusal cleaned as a log
        line is (a value of the file is never printed as it is).
        """
        done = self.call(["run", "--rm", "--network", "none", "-v", f"{self._layout.config}:/app/etc/config.json:ro",
                          "--entrypoint", "python", image, "-c", CONFIG_PROBE], check=False, timeout=120)
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        last = lines[-1] if lines else ""
        if done.returncode == 0 and last == "ACCEPTED":
            return True, "accepted"
        return False, (last.removeprefix("REFUSED ") if last.startswith("REFUSED ") else
                       f"could not be checked (exit code {done.returncode})")[:300]

    def check_image(self, version: str) -> subprocess.CompletedProcess:
        """src/release/check.py in a release's image, on config.json as it is, without network."""
        return self.call(["run", "--rm", "--network", "none", "-v", f"{self._layout.config}:/app/etc/config.json:ro",
                          "--entrypoint", "python", f"{WATA_IMAGE}:{version}", "-m", "src.release.check"],
                         check=False, timeout=900)


@dataclass
class Move:
    """One container moved from an image to another, and what became of it."""

    container: str
    role: str
    before: str | None
    after: str
    state: str = "waiting"
    detail: str | None = None
    started_at: str | None = None

    def as_dict(self) -> dict:
        return {"container": self.container, "role": self.role, "before": self.before, "after": self.after,
                "state": self.state, "detail": self.detail, "started_at": self.started_at}


WAITING, RECREATING, STARTING, READY, NOT_READY, FAILED, ROLLED_BACK, HELD, SKIPPED = (
    "waiting", "recreating", "starting", "ready", "not_ready", "failed", "rolled_back", "held", "skipped",
)


@dataclass
class Facts:
    """What a plan is made of, gathered once."""

    newest: str
    target_image: str
    containers: list[Container]
    situation: Situation
    holds: dict[str, str] = field(default_factory=dict)


class Host:
    def __init__(self, layout: Layout | None = None, docker: Docker | None = None, *, clock=time.time,
                 sleep=time.sleep, out=None):
        self.layout = layout or Layout()
        self.docker = docker or Docker(self.layout)
        self._clock = clock
        self._sleep = sleep
        self._out = out or (lambda line: print(line, flush=True))

    # ── what runs ──

    def _config(self) -> dict:
        try:
            with open(self.layout.config, encoding="utf-8") as config_file:
                return json.load(config_file)
        except (OSError, ValueError):
            return {}

    def manifest(self, image_id: str) -> dict:
        """What an image runs, read once and kept by its id."""
        name = image_id.replace("sha256:", "")
        if not re.match(r"^[0-9a-f]{12,64}$", name):
            raise DeployError(f"Not an image id: {image_id!r}")
        path = os.path.join(self.layout.manifests, f"{name}.json")
        try:
            with open(path, encoding="utf-8") as kept:
                return json.load(kept)
        except (OSError, ValueError):
            pass
        found = self.docker.manifest(image_id)
        _write_atomically(path, json.dumps(found, sort_keys=True))
        return found

    @staticmethod
    def fingerprint(found: dict, container: str) -> dict | None:
        entry = (found.get("containers") or {}).get(container)
        if entry is None:
            return None
        platform = found.get("platform") or {}
        return {
            "code": entry.get("code"),
            "files": {path: (found.get("files") or {}).get(path) for path in entry.get("files") or []},
            "libraries": entry.get("libraries"),
            "distributions": entry.get("distributions") or {},
            "platform": platform.get("digest"),
            "platform_said": f"Python {platform.get('python')}, Node {platform.get('node') or '-'}",
        }

    def _backup_fingerprint(self, version: str | None) -> dict | None:
        """The backups' image is made of two files of a release's folder."""
        if version is None:
            return None
        files = {}
        for name in ("pg_backup.Dockerfile", "pg_backup.sh"):
            try:
                with open(os.path.join(self.layout.deploy_dir(version), name), "rb") as data:
                    files[f"deploy/{name}"] = manifests.digest(data.read())
            except OSError:
                return None
        return {"code": manifests.digest(*(f"{path}={value}".encode() for path, value in sorted(files.items()))),
                "files": files}

    def _definitions(self, version: str, files: list[str] | None) -> dict | None:
        """Each service's resolved compose definition, from a release's files or the ones named."""
        try:
            done = self.docker.compose(version, ["config", "--format", "json"], files=files, timeout=60)
            return json.loads(done.stdout).get("services") or {}
        except (DeployError, ValueError):
            return None

    @staticmethod
    def compose_changes(before: dict | None, after: dict | None, pinned: bool = True) -> list[str] | None:
        """The keys of a definition that differ (the image too, unless pinned); None when the old one is unknown."""
        if before is None or after is None:
            return None
        keys = (set(before) | set(after)) - (set(NOT_COMPARED) if pinned else {"depends_on"})
        return sorted(key for key in keys if before.get(key) != after.get(key))

    def facts(self, now: float | None = None) -> Facts:
        now = self._clock() if now is None else now
        newest = self.layout.newest()
        target_image = f"{WATA_IMAGE}:{newest}"
        target = self.docker.image(target_image)
        if target is None:
            raise DeployError(f"{target_image} is not built on this server: install {newest} first")
        target_manifest = self.manifest(target["Id"])
        running = self.docker.containers()
        pins = read_pins(self.layout.pins)
        active = set(self.docker.compose(newest, ["config", "--services"]).stdout.split())
        newest_definitions = self._definitions(newest, None) or {}
        old_definitions: dict[str, dict | None] = {}
        settings_target = {key: (target.get("Config") or {}).get(key)
                           for key in ("Env", "Cmd", "Entrypoint", "WorkingDir", "User")}
        containers = []
        for name in [*manifests.CONTAINERS, BACKUP_CONTAINER, *INFRASTRUCTURE]:
            inspected = running.get(name)
            if inspected is None and name not in active:
                continue
            kind = WATA if name in manifests.CONTAINERS else BACKUP if name == BACKUP_CONTAINER else INFRA
            role = manifests.CONTAINERS.get(name) or ("pg_backup" if kind == BACKUP else name)
            container = Container(name=name, role=role, kind=kind, running=bool(
                inspected and (inspected.get("State") or {}).get("Running")))
            if inspected is not None:
                files = ((inspected.get("Config") or {}).get("Labels") or {}).get(
                    "com.docker.compose.project.config_files", "")
                key = files
                if key not in old_definitions:
                    paths = [path for path in files.split(",") if path]
                    exists = paths and all(os.path.isfile(path) for path in paths)
                    old_definitions[key] = self._definitions(newest, paths) if exists else None
                # None when the files that made it are gone; {} when they had no such service.
                before = None if old_definitions[key] is None else old_definitions[key].get(name, {})
                container.compose = self.compose_changes(before, newest_definitions.get(name), kind != INFRA)
            if kind == WATA:
                container.image = pins.get(pin_variable(name)) or (inspected or {}).get("Config", {}).get("Image")
                if inspected is not None:
                    image_id = inspected.get("Image")
                    current = self.manifest(image_id) if image_id else None
                    container.current = self.fingerprint(current, name) if current else None
                    container.version = (current or {}).get("version")
                    found = self.docker.image(image_id) if image_id else None
                    settings = {key: ((found or {}).get("Config") or {}).get(key) for key in settings_target}
                    container.image_settings = found is not None and settings != settings_target
                container.target = self.fingerprint(target_manifest, name)
            elif kind == BACKUP:
                container.image = pins.get(pin_variable(name)) or (inspected or {}).get("Config", {}).get("Image")
                container.version = version_of(container.image)
                container.current = self._backup_fingerprint(container.version)
                container.target = self._backup_fingerprint(newest)
            containers.append(container)
        situation, holds = self.situation(now, running)
        return Facts(newest=newest, target_image=target_image, containers=containers, situation=situation, holds=holds)

    # ── what holds a container back ──

    def _count_open_positions(self) -> int | None:
        answer = self.docker.psql("SELECT count(*) FROM turbo_data_position WHERE position_status = 'Open'")
        return int(answer) if answer is not None and answer.isdigit() else None

    def _monitor_beat(self, now: float) -> tuple[tuple[str, ...], int]:
        answer = self.docker.psql("SELECT json_build_object('beat_at', beat_at, 'figures', figures) "
                                  "FROM service_heartbeat WHERE service = 'position_monitor'")
        try:
            beat = json.loads(answer) if answer else None
        except ValueError:
            beat = None
        beat_at = moment((beat or {}).get("beat_at"))
        if beat_at is None or now - beat_at.timestamp() > HEARTBEAT_STALE_SECONDS:
            return (), 0
        figures = beat.get("figures") or {}
        intents = tuple(str(intent.get("position_id")) for intent in figures.get("close_intents") or [])
        return intents, int(figures.get("positions_listed") or 0)

    def _live_rules(self, config: dict) -> int:
        folder = self.layout.host_path(((config.get("admin") or {}).get("persistant") or {}).get(
            "ta_path", "/app/var/lib/admin/ta"))
        try:
            with open(os.path.join(folder, "rules.json"), encoding="utf-8") as rules_file:
                rules = json.load(rules_file).get("rules") or []
        except (OSError, ValueError, AttributeError):
            return 0
        return sum(1 for rule in rules if isinstance(rule, dict) and rule.get("mode") == "live")

    def _agent_requests(self, config: dict) -> int:
        folder = os.path.join(self.layout.host_path(((config.get("admin") or {}).get("persistant") or {}).get(
            "agent_path", "/app/var/lib/admin/agent")), "conversations")
        found = 0
        try:
            names = os.listdir(folder)
        except OSError:
            return 0
        for name in names:
            try:
                with open(os.path.join(folder, name, "meta.json"), encoding="utf-8") as meta:
                    if json.load(meta).get("status") in AGENT_UNDER_WAY:
                        found += 1
            except (OSError, ValueError, AttributeError):
                continue
        return found

    def situation(self, now: float, running: dict[str, dict]) -> tuple[Situation, dict[str, str]]:
        """The guards' facts, read from the server; a fact that cannot be read holds back what it guards."""
        config = self._config()
        intents, listed = self._monitor_beat(now)
        backtests = studies = 0
        holds = {}
        if "admin1" in running:
            lines = self.docker.processes("admin1")
            if lines is None:
                holds["admin1"] = "its backtests and studies cannot be read"
            else:
                backtests = sum(1 for line in lines if any(f"-m {module}" in line for module in BACKTEST_MODULES))
                studies = sum(1 for line in lines if any(f"-m {module}" in line for module in STUDY_MODULES))
        if BACKUP_CONTAINER in running:
            lines = self.docker.processes(BACKUP_CONTAINER)
            if lines is None or any("pg_dump" in line or "pg_restore" in line for line in lines):
                holds[BACKUP_CONTAINER] = "a dump is being written or checked"
        situation = Situation(
            positions_open=self._count_open_positions(),
            positions_listed=listed,
            close_intents=intents,
            market_open=market_is_open(config, now) if config else True,
            live_rules=self._live_rules(config),
            backtests_running=backtests,
            study_running=studies > 0,
            scheduler_job_near=scheduler_job_near(config, now) if config else None,
            agent_requests=self._agent_requests(config),
        )
        return situation, holds

    def plan(self, wanted: list[str] | None = None, *, force: bool = False, platform: bool = False,
             now: float | None = None) -> tuple[Facts, list[Step]]:
        facts = self.facts(now)
        steps = make_plan(facts.containers, facts.situation, holds=facts.holds, target=facts.newest, wanted=wanted,
                          force=force, platform=platform)
        return facts, steps

    # ── seeing a container at work again ──

    def _scheduler_line(self) -> str | None:
        folder, markers, _ = SCHEDULER_LOG
        path = os.path.join(self.layout.base, "var", "log", folder, f"{folder}.log")
        try:
            with open(path, "rb") as log:
                log.seek(0, os.SEEK_END)
                log.seek(max(log.tell() - 1_048_576, 0))
                lines = log.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            if any(marker in line for marker in markers):
                return line
        return None

    def before(self, role: str):
        """What is to be told apart after a restart: the scheduler's last line of its lifecycle."""
        return self._scheduler_line() if role == "scheduler" else None

    def _since(self, column: str, table: str, started_at: datetime, where: str = "") -> bool:
        """Whether the newest ``column`` of ``table`` is at or after ``started_at`` (read as seconds since 1970:
        PostgreSQL's text of a time is not one the server's python3 3.10 reads)."""
        answer = self.docker.psql(f"SELECT extract(epoch FROM max({column})) FROM {table}{where}")
        try:
            return answer is not None and answer != "" and float(answer) >= started_at.timestamp()
        except ValueError:
            return False

    def at_work(self, container: str, role: str, started_at: datetime, before) -> tuple[bool, str]:
        """Whether ``container``, running since ``started_at``, does its work: the admin's ``Readiness``, from here."""
        config = self._config()
        if role in ("trader", "position_monitor"):
            if not self._since("started_at", "service_heartbeat", started_at, f" WHERE service = '{role}'"):
                return False, "no heartbeat of its new start yet"
            if role == "position_monitor" and not (self.docker.consumers() or {}).get(OPS_QUEUE):
                return False, f"no consumer on {OPS_QUEUE} yet"
            return True, "beating again"
        if role == "telegram":
            return (True, f"consuming {TELEGRAM_QUEUE}") if (self.docker.consumers() or {}).get(TELEGRAM_QUEUE) else (
                False, f"no consumer on {TELEGRAM_QUEUE} yet")
        if role == "web_server":
            return self.docker.answers(container, "http://127.0.0.1:80/latest-signals")
        if role == "watchlist_manager":
            watchlist = (((config.get("trade") or {}).get("config") or {}).get("watchlist_manager") or {})
            if watchlist.get("enabled", True) is False:
                return True, "running; not used by the trader (disabled in the configuration)"
            return self.docker.answers(container, "http://127.0.0.1:8081/healthz")
        if role == "scheduler":
            line = self._scheduler_line()
            if line and SCHEDULER_LOG[2] in line and line != before:
                return True, "running (from its log)"
            return False, "no line of its new start in its log yet"
        if role == "price_recorder":
            recorder = (((config.get("trade") or {}).get("config") or {}).get("price_recorder") or {})
            if not recorder.get("enabled"):
                return True, "running; switched off in the configuration"
            if self._since("ts", "price_recorder_coverage", started_at):
                return True, "recording"
            return False, "no bar bucket recorded since its start yet"
        if role in ("capital_feed", "ta_engine"):
            if self._since("started_at", f"{role}_state", started_at):
                return True, "its state written since its start"
            return False, "no state written since its start yet"
        if role == "admin":
            return self.docker.answers(container, "http://127.0.0.1:8082/")
        if role == "service_control":
            return self.docker.answers(container, "http://127.0.0.1:8090/services")
        return True, "running"

    def _until_running(self, container: str, previous_start: str | None) -> tuple[datetime | None, str]:
        """The container's new start once it stays up, or None and why not."""
        deadline = self._clock() + RUNNING_TIMEOUT_SECONDS
        steady_since = None
        detail = "not running yet"
        while self._clock() < deadline:
            state = (self.docker.inspect(container) or {}).get("State") or {}
            started = state.get("StartedAt")
            if state.get("Running") and not state.get("Restarting") and started and started != previous_start:
                if steady_since is None or steady_since[0] != started:
                    steady_since = (started, self._clock())
                elif self._clock() - steady_since[1] >= STAY_UP_SECONDS:
                    return moment(started), "running"
                detail = "running"
            else:
                detail = f"{state.get('Status') or 'absent'}" + (
                    f", exit code {state.get('ExitCode')}" if state.get("Status") == "exited" else "")
                steady_since = None
            self._sleep(POLL_SECONDS)
        return None, f"did not stay up within {RUNNING_TIMEOUT_SECONDS} s ({detail})"

    def _until_ready(self, container: str, role: str, started_at: datetime, before) -> tuple[bool, str]:
        deadline = self._clock() + READY_TIMEOUT_SECONDS
        detail = "not checked"
        while self._clock() < deadline:
            try:
                ready, detail = self.at_work(container, role, started_at, before)
            except DeployError as e:
                ready, detail = False, str(e)
            if ready:
                return True, detail
            self._sleep(POLL_SECONDS)
        return False, detail

    # ── moving containers ──

    def _pin(self, container: str, image: str) -> None:
        pins = read_pins(self.layout.pins)
        pins[pin_variable(container)] = image
        write_pins(self.layout.pins, pins)

    def _recreate(self, version: str, container: str) -> None:
        """That container alone, made again on what its pin names, even if compose would see nothing to change."""
        self.docker.compose(version, ["up", "-d", "--no-deps", "--force-recreate", "--pull", "never", container],
                            timeout=300)

    def move(self, steps: list[Move], *, kind: str, by: str, force: bool, version: str,
             request: str | None = None, dump: bool = True) -> dict:
        """
        Recreate each container on its new image in turn, the guards read again
        before each (unless forced). One that does not stay up goes back to its
        image of before and the rest waits; one that runs without being seen at
        work stops the rest too, and stays.
        """
        record = {"id": os.urandom(8).hex(), "kind": kind, "by": by, "force": force, "version": version,
                  "request": request, "started_at": utc_now(), "steps": [step.as_dict() for step in steps],
                  "done": False}

        def said() -> None:
            record["steps"] = [step.as_dict() for step in steps]
            self.write_status(applying=record)

        said()
        try:
            if dump and steps:
                self._dump()
            for index, step in enumerate(steps):
                if not force:
                    situation, holds = self.situation(self._clock(), self.docker.containers())
                    held = (deploy_refusal(step.role, situation) if step.role in ORDER else None) or holds.get(
                        step.container)
                    if held:
                        step.state, step.detail = HELD, f"Held back: {held}"
                        self._out(f"{step.container}: held back ({held})")
                        said()
                        continue
                previous_start = ((self.docker.inspect(step.container) or {}).get("State") or {}).get("StartedAt")
                before = self.before(step.role)
                step.state = RECREATING
                said()
                self._out(f"{step.container}: {step.before or '-'} → {step.after}")
                self._pin(step.container, step.after)
                try:
                    self._recreate(version, step.container)
                except DeployError as e:
                    started_at, detail = None, str(e)
                else:
                    started_at, detail = self._until_running(step.container, previous_start)
                if started_at is None:
                    step.state, step.detail = FAILED, detail
                    said()
                    self._back(step, version)
                    self._skip_rest(steps[index + 1:])
                    break
                step.state, step.started_at = STARTING, started_at.isoformat()
                said()
                ready, detail = self._until_ready(step.container, step.role, started_at, before)
                if not ready:
                    step.state, step.detail = NOT_READY, (
                        f"running on {step.after}, not seen at work within {READY_TIMEOUT_SECONDS} s: {detail}")
                    self._out(f"{step.container}: {step.detail}")
                    self._skip_rest(steps[index + 1:])
                    break
                step.state, step.detail = READY, detail
                self._out(f"{step.container}: at work ({detail})")
                said()
        except DeployError as e:
            record["error"] = str(e)
            self._skip_rest([step for step in steps if step.state == WAITING])
            raise
        finally:
            record["done"] = True
            record["finished_at"] = utc_now()
            record["steps"] = [step.as_dict() for step in steps]
            self._history(record)
            self.write_status(applying=None, last=record)
        return record

    def _back(self, step: Move, version: str) -> None:
        """A container that did not stay up on its new image, put back on the one before."""
        if not step.before:
            return
        self._out(f"{step.container}: back to {step.before}")
        self._pin(step.container, step.before)
        try:
            self._recreate(version, step.container)
            started_at, detail = self._until_running(step.container, None)
        except DeployError as e:
            started_at, detail = None, str(e)
        step.state = ROLLED_BACK
        step.detail = f"{step.detail}; back on {step.before}" + ("" if started_at else f", which {detail} either")

    @staticmethod
    def _skip_rest(steps: list[Move]) -> None:
        for step in steps:
            if step.state == WAITING:
                step.state, step.detail = SKIPPED, "not applied: the apply stopped before it"

    def _dump(self) -> None:
        """The database dumped before anything is recreated (deploy/pg_backup.sh)."""
        if BACKUP_CONTAINER not in self.docker.containers():
            self._out("No dump: the backups' container is not running")
            return
        self._out("Dumping the database first")
        self.docker.call(["exec", BACKUP_CONTAINER, "sh", "/pg_backup.sh", "now"], timeout=900)

    def _history(self, record: dict) -> None:
        os.makedirs(self.layout.release, exist_ok=True)
        with open(self.layout.history, "a", encoding="utf-8") as history:
            history.write(json.dumps(record, sort_keys=True) + "\n")

    def history(self, limit: int = 20) -> list[dict]:
        try:
            with open(self.layout.history, encoding="utf-8") as history:
                lines = history.readlines()
        except OSError:
            return []
        found = []
        for line in lines[-limit:]:
            try:
                found.append(json.loads(line))
            except ValueError:
                continue
        return found

    def image_for(self, container: str, version: str) -> str:
        return f"{BACKUP_IMAGE}:{version}" if container == BACKUP_CONTAINER else f"{WATA_IMAGE}:{version}"

    def apply(self, wanted: list[str] | None = None, *, force: bool = False, platform: bool = False,
              by: str = "watadeploy", request: str | None = None, dump: bool = True) -> dict:
        with self.locked():
            facts, steps = self.plan(wanted, force=force, platform=platform)
            pins = read_pins(self.layout.pins)
            moves = [Move(container=step.container, role=step.role, before=pins.get(pin_variable(step.container)),
                           after=self.image_for(step.container, facts.newest))
                     for step in steps if step.when == NOW]
            for step in steps:
                self._out(said(step))
            if not moves:
                self.write_status(facts=facts, steps=steps)
                return {"steps": [], "plan": summary(steps)}
            record = self.move(moves, kind="apply", by=by, force=force, version=facts.newest, request=request,
                               dump=dump)
            self.write_status()
            return record

    def rollback(self, containers: list[str] | None = None, *, force: bool = False, by: str = "watadeploy") -> dict:
        """The containers of the last apply put back on the images they ran before it."""
        with self.locked():
            last = next((record for record in reversed(self.history(200)) if record.get("kind") in ("apply", "full")),
                        None)
            if last is None:
                raise DeployError("No apply to undo")
            moved = [step for step in last.get("steps") or [] if step.get("state") in (READY, NOT_READY)
                     and step.get("before") and (containers is None or step["container"] in containers)]
            if containers is not None and {step["container"] for step in moved} != set(containers):
                missing = set(containers) - {step["container"] for step in moved}
                raise DeployError(f"Not moved by the last apply: {', '.join(sorted(missing))}")
            moves = [Move(container=step["container"], role=step["role"], before=step["after"], after=step["before"])
                     for step in moved]
            order = {name: index for index, name in enumerate(ORDER)}
            moves.sort(key=lambda step: (step.role != "pg_backup", order.get(step.role, len(order))))
            return self.move(moves, kind="rollback", by=by, force=force, version=self.layout.newest(), dump=False)

    def full(self, *, force: bool = False, by: str = "watadeploy") -> dict:
        """
        Every container stopped and started again, WATA's on the newest release:
        the deploy of before v0.61.0, the image built first. For a change of the
        infrastructure (Traefik, PostgreSQL, RabbitMQ), at a closed market.
        """
        with self.locked():
            facts = self.facts()
            if not force:
                reasons = {reason for container in facts.containers if container.role in ORDER
                           and (reason := deploy_refusal(container.role, facts.situation))}
                if trading := deploy_refusal("trader", facts.situation):
                    reasons.add(trading)
                if reasons:
                    raise DeployError("Held back: " + "; ".join(sorted(reasons)))
            pins = read_pins(self.layout.pins)
            moves = []
            for container in facts.containers:
                if container.kind in (WATA, BACKUP):
                    after = self.image_for(container.name, facts.newest)
                    moves.append(Move(container=container.name, role=container.role,
                                      before=pins.get(pin_variable(container.name)), after=after, state=STARTING))
                    pins[pin_variable(container.name)] = after
            pins[TOOLS_VARIABLE] = f"{WATA_IMAGE}:{facts.newest}"
            order = {name: index for index, name in enumerate(ORDER)}
            moves.sort(key=lambda move: (move.role != "pg_backup", order.get(move.role, len(order))))
            record = {"id": os.urandom(8).hex(), "kind": "full", "by": by, "force": force, "version": facts.newest,
                      "started_at": utc_now(), "steps": [move.as_dict() for move in moves], "done": False}
            self.write_status(applying=record)
            try:
                self._dump()
                write_pins(self.layout.pins, pins)
                self._out("Stopping every container")
                self.docker.compose(facts.newest, ["down"], timeout=600)
                self._out(f"Starting every container, WATA's on {facts.newest}")
                self.docker.compose(facts.newest, ["up", "-d", "--pull", "never"], timeout=900)
                for move in moves:
                    started_at, detail = self._until_running(move.container, None)
                    if started_at is None:
                        move.state, move.detail = FAILED, detail
                        continue
                    move.started_at = started_at.isoformat()
                    ready, detail = self._until_ready(move.container, move.role, started_at, None)
                    move.state, move.detail = (READY if ready else NOT_READY), detail
                    self._out(f"{move.container}: {move.state} ({detail})")
                    record["steps"] = [found.as_dict() for found in moves]
                    self.write_status(applying=record)
            except DeployError as e:
                record["error"] = str(e)
                raise
            finally:
                record["steps"] = [move.as_dict() for move in moves]
                record["finished_at"] = utc_now()
                record["done"] = True
                self._history(record)
                self.write_status(applying=None, last=record)
            self.write_status()
            return record

    # ── the admin's requests ──

    def _refuse(self, name: str, detail: str) -> None:
        self._out(f"Request {name} refused: {detail}")
        self.write_status(request={"id": name, "state": "refused", "detail": detail, "at": utc_now()})

    def read_request(self, path: str) -> dict:
        """A request the admin left, read without following a link and no larger than a request can be."""
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as request_file:
            if not os.path.isfile(path) or os.fstat(request_file.fileno()).st_size > MAX_REQUEST_BYTES:
                raise DeployError("not a request")
            data = request_file.read(MAX_REQUEST_BYTES + 1)
        try:
            request = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise DeployError("not a request") from e
        if not isinstance(request, dict) or set(request) - REQUEST_FIELDS:
            raise DeployError("not a request")
        return request

    def serve_requests(self) -> int:
        """Carry out each request the admin left (``watadeploy-requests.path`` starts it), oldest first."""
        folder = self.layout.requests_in
        try:
            names = sorted(os.listdir(folder), key=lambda name: os.lstat(os.path.join(folder, name)).st_mtime)
        except FileNotFoundError:
            return 0
        os.makedirs(self.layout.requests_kept, mode=0o700, exist_ok=True)
        served = 0
        for name in names:
            source = os.path.join(folder, name)
            stem = name[: -len(".json")] if name.endswith(".json") else ""
            if not REQUEST_ID.match(stem):
                if os.path.islink(source) or os.path.isfile(source):
                    os.unlink(source)
                continue
            kept = os.path.join(self.layout.requests_kept, name)
            # Out of the admin's reach before anything is read of it.
            os.replace(source, kept)
            served += 1
            try:
                request = self.read_request(kept)
            except (OSError, DeployError) as e:
                self._refuse(stem, str(e) if isinstance(e, DeployError) else "unreadable")
                continue
            try:
                self.serve(stem, request)
            except DeployError as e:
                self._refuse(stem, str(e))
            except Exception as e:
                # Said, and the next request served all the same: the file is out of the folder already.
                self._refuse(stem, f"watadeploy failed on it ({type(e).__name__}): see watadeploy-requests.service's log")
        self._prune_requests()
        return served

    def serve(self, name: str, request: dict) -> None:
        if request.get("id") != name:
            raise DeployError("its id is not its file's name")
        newest = self.layout.newest()
        if request.get("version") != newest:
            raise DeployError(f"asked for {request.get('version')!r}, and the newest release here is {newest}")
        asked_at = moment(request.get("asked_at"))
        if asked_at is None or abs(self._clock() - asked_at.timestamp()) > REQUEST_MAX_AGE_SECONDS:
            raise DeployError("asked too long ago: ask again")
        containers = request.get("containers")
        if (not isinstance(containers, list) or not containers or len(containers) > 20
                or not all(isinstance(name_, str) for name_ in containers)):
            raise DeployError("no container named")
        if not isinstance(request.get("force", False), bool):
            raise DeployError("not a request")
        _, steps = self.plan()
        waiting = {step.container for step in steps if step.kind in (CHANGE, OPTIONAL)}
        if set(containers) - waiting:
            raise DeployError(f"not waiting for {newest}: {', '.join(sorted(set(containers) - waiting))}")
        by = str(request.get("asked_by") or "the admin")[:80]
        self.write_status(request={"id": name, "state": "accepted", "at": utc_now()})
        record = self.apply(containers, force=request.get("force", False), platform=True, by=f"admin ({by})",
                            request=name)
        self.write_status(request={"id": name, "state": "done", "at": utc_now(), "apply": record.get("id")})

    def _prune_requests(self) -> None:
        folder = self.layout.requests_kept
        names = sorted(os.listdir(folder), key=lambda name: os.lstat(os.path.join(folder, name)).st_mtime)
        for name in names[:-KEPT_REQUESTS]:
            os.unlink(os.path.join(folder, name))

    # ── what the admin reads ──

    def read_status(self) -> dict:
        try:
            with open(self.layout.status, encoding="utf-8") as status:
                return json.load(status)
        except (OSError, ValueError):
            return {}

    def write_status(self, *, facts: Facts | None = None, steps: list[Step] | None = None, applying=False,
                     last=None, request=None, config=None) -> dict:
        """The status file, each part replaced only when given (``applying=None`` ends an apply)."""
        error = None
        if facts is None and steps is None and applying is False and last is None and request is None and config is None:
            try:
                facts, steps = self.plan()
            except DeployError as e:
                error = str(e)
        # Read and written whole by one writer at a time: the config check and an apply may both write it.
        with _Lock(self.layout.status + ".lock", blocking=True):
            return self._write_status(error, facts, steps, applying, last, request, config)

    def _write_status(self, error, facts, steps, applying, last, request, config) -> dict:
        status = self.read_status()
        if error is not None:
            status["error"] = error
        if facts is not None and steps is not None:
            pins = read_pins(self.layout.pins)
            versions = {container.name: container.version for container in facts.containers}
            status.update({
                "newest": facts.newest,
                "installed": self.layout.installed(),
                "containers": [{**step.as_dict(), "image": pins.get(pin_variable(step.container)),
                                "version": versions.get(step.container), "said": said(step)} for step in steps],
                "plan": summary(steps),
                "planned_at": utc_now(),
            })
            status.pop("error", None)
        if applying is not False:
            status["applying"] = applying
        if last is not None:
            status["last"] = last
        if request is not None:
            requests = [found for found in status.get("requests") or [] if found.get("id") != request["id"]]
            status["requests"] = ([request] + requests)[:20]
        if config is not None:
            status["config"] = config
        status["at"] = utc_now()
        _write_atomically(self.layout.status, json.dumps(status, sort_keys=True, indent=1))
        return status

    # ── keeping things right ──

    def locked(self):
        return _Lock(self.layout.lock)

    def check_config(self) -> list[dict]:
        """Whether every image in use, and the newest, still starts on config.json as it is."""
        newest = self.layout.newest()
        running = self.docker.containers()
        by_image: dict[str, list[str]] = {}
        for name, inspected in running.items():
            image = (inspected.get("Config") or {}).get("Image")
            if name in manifests.CONTAINERS and image:
                by_image.setdefault(read_pins(self.layout.pins).get(pin_variable(name)) or image, []).append(name)
        by_image.setdefault(f"{WATA_IMAGE}:{newest}", [])
        found = []
        for image, containers in sorted(by_image.items()):
            ok, detail = self.docker.check_config(image)
            found.append({"image": image, "version": version_of(image), "containers": sorted(containers),
                          "accepted": ok, "detail": detail})
        self.write_status(config={"at": utc_now(), "images": found})
        return found

    def check_image(self, version: str) -> int:
        """The checks of src/release/check.py in a release's image; what they said, and whether they passed."""
        if not VERSION_PATTERN.match(version):
            raise DeployError(f"Not a release: {version!r}")
        done = self.docker.check_image(version)
        for line in (done.stdout + done.stderr).splitlines():
            self._out(line)
        return 0 if done.returncode == 0 else 1

    def bootstrap(self) -> dict[str, str]:
        """
        The pins of a server that had none: each running container pinned to
        the image it runs, tagged with the release it is, so that switching to
        pins recreates nothing; the tools and what is not running yet on the newest.
        """
        newest = self.layout.newest()
        pins = read_pins(self.layout.pins)
        running = self.docker.containers()
        for name in [*manifests.CONTAINERS, BACKUP_CONTAINER]:
            variable = pin_variable(name)
            if variable in pins:
                continue
            inspected = running.get(name)
            if inspected is None:
                pins[variable] = self.image_for(name, newest)
                continue
            image_id = inspected["Image"]
            short = image_id.replace("sha256:", "")[:12]
            if name == BACKUP_CONTAINER:
                tag = f"{BACKUP_IMAGE}:before-{short}"
            else:
                version = self.manifest(image_id).get("version") or "unknown"
                tag = f"{WATA_IMAGE}:{version}"
                existing = self.docker.image(tag)
                if existing is not None and existing.get("Id") != image_id:
                    tag = f"{WATA_IMAGE}:{version}-{short}"
            if (self.docker.image(tag) or {}).get("Id") != image_id:
                self.docker.tag(image_id, tag)
            pins[variable] = tag
        pins[TOOLS_VARIABLE] = f"{WATA_IMAGE}:{newest}"
        write_pins(self.layout.pins, pins)
        return pins

    def prune(self, keep: int = KEEP_RELEASES) -> list[str]:
        """Older releases' folders and images, keeping the newest ``keep`` and every one still named."""
        installed = self.layout.installed()
        named = set(installed[:keep])
        pins = read_pins(self.layout.pins)
        named.update(version for version in map(version_of, pins.values()) if version)
        for inspected in self.docker.containers().values():
            files = ((inspected.get("Config") or {}).get("Labels") or {}).get("com.docker.compose.project.config_files", "")
            for path in files.split(","):
                match = re.match(rf"^{re.escape(self.layout.base)}/(\d+\.\d+\.\d+)/deploy/", path)
                if match:
                    named.add(match.group(1))
        for record in self.history(5):
            for step in record.get("steps") or []:
                named.update(version for version in (version_of(step.get("before")), version_of(step.get("after")))
                             if version)
        removed = []
        for version in installed:
            if version in named:
                continue
            for image in (f"{WATA_IMAGE}:{version}", f"{BACKUP_IMAGE}:{version}"):
                if self.docker.image(image) is not None:
                    self.docker.remove_image(image)
            shutil.rmtree(os.path.join(self.layout.base, version), ignore_errors=True)
            removed.append(version)
        for leftover in os.listdir("/tmp") if os.path.isdir("/tmp") else []:
            if re.match(r"^wata_app_v\d+\.\d+\.\d+\.zip$", leftover):
                try:
                    os.unlink(os.path.join("/tmp", leftover))
                except OSError:
                    pass
        return removed


class _Lock:
    """One apply at a time, across processes (the playbook, the command, the admin's requests); or a wait for it."""

    def __init__(self, path: str, blocking: bool = False):
        self._path = path
        self._blocking = blocking
        self._file = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._file = open(self._path, "w")
        try:
            fcntl.flock(self._file, fcntl.LOCK_EX | (0 if self._blocking else fcntl.LOCK_NB))
        except BlockingIOError as e:
            self._file.close()
            raise DeployError("Another apply is under way: one at a time") from e
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._file, fcntl.LOCK_UN)
        self._file.close()
        return False


def python_is_enough() -> bool:
    return sys.version_info >= (3, 10)
