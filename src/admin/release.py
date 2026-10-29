"""
The release each container runs, what waits for the newest one, and the
operator's request to apply it, for the System page (src/admin/routers/release.py).

The admin holds no Docker, no compose file and no pin. watadeploy
(src/release/host.py) writes what it knows into a status file the admin
mounts read-only; the admin writes a request into its own folder, which a
systemd path unit on the server hands to ``watadeploy serve-requests``. That
one takes the file out of the admin's reach before reading it, accepts only
the newest release and containers that wait for it, and applies it with the
guards of any apply (src/release/guards.py), whatever the admin said of them.

Announced once each, on Telegram and to the open pages: an apply's outcome,
an image in use that would not start on config.json as it is, and, once the
market closed, what still waits.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.release.guards import trading_hours, within_hours

logger = logging.getLogger(__name__)

STATUS_PATH = "/app/var/lib/release/status.json"
REQUESTS_PATH = "/app/var/lib/admin/deploy/requests"
ANNOUNCED_PATH = "/app/var/lib/admin/deploy/announced.json"
# What a deploy may apply: a change, or the platform alone (Python, the system) when asked for.
WAITING_KINDS = ("change", "optional")
# How often the status file is looked at (its time only, unless it changed).
WATCH_SECONDS = 5
# What is remembered of what was announced.
KEPT_ANNOUNCED = 50
# The states of an apply's step that went wrong (src/release/host.py), and how each is said (as on the System page).
WENT_WRONG = ("failed", "rolled_back", "not_ready")
STATE_SAID = {"ready": "at work", "not_ready": "not seen at work", "failed": "did not stay up", "rolled_back": "put back",
              "held": "held back", "skipped": "not applied", "waiting": "waiting"}


class ReleaseRefused(Exception):
    """A request the admin does not write: said to the operator as it is."""


def _now_iso(clock) -> str:
    return datetime.fromtimestamp(clock(), timezone.utc).isoformat(timespec="seconds")


def _write_json(path: str, value) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as written:
        json.dump(value, written, sort_keys=True)
    os.replace(temporary, path)


def outcome_said(record: dict) -> str:
    """One message for an apply, a rollback or a full restart that ended."""
    steps = record.get("steps") or []
    kind = {"apply": "applied", "rollback": "rolled back", "full": "restarted whole"}.get(record.get("kind"), "applied")
    wrong = [step for step in steps if step.get("state") in WENT_WRONG]
    head = (f"⚠️ WATA: {record.get('version')} {kind} only in part" if wrong or record.get("error")
            else f"🚀 WATA: {record.get('version')} {kind}")
    lines = [f"{head}, by {record.get('by') or 'watadeploy'}"]
    for step in steps:
        detail = f" ({step['detail']})" if step.get("detail") and step.get("state") != "ready" else ""
        lines.append(f"{step.get('container')}: {STATE_SAID.get(step.get('state'), step.get('state'))}{detail}")
    if record.get("error"):
        lines.append(record["error"])
    return "\n".join(lines)


class Release:
    def __init__(self, status_path: str = STATUS_PATH, requests_path: str = REQUESTS_PATH,
                 announced_path: str = ANNOUNCED_PATH, *, clock=time.time):
        self._status_path = status_path
        self._requests_path = requests_path
        self._announced_path = announced_path
        self._clock = clock
        self._seen_mtime = None

    # ── what watadeploy says ──

    @property
    def available(self) -> bool:
        return os.path.isfile(self._status_path)

    def status(self) -> dict:
        try:
            with open(self._status_path, encoding="utf-8") as status_file:
                found = json.load(status_file)
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def applying(self, status: dict | None = None) -> dict | None:
        """The apply under way, if one is: the admin's own restarts wait for it."""
        found = (self.status() if status is None else status).get("applying")
        return found if isinstance(found, dict) and not found.get("done") else None

    @staticmethod
    def waiting(status: dict) -> dict[str, dict]:
        """The containers that wait for the newest release, by name."""
        return {entry["container"]: entry for entry in status.get("containers") or []
                if isinstance(entry, dict) and entry.get("kind") in WAITING_KINDS and entry.get("container")}

    def summary(self) -> dict | None:
        """What runs where, in short: for Claude's status (read-only)."""
        status = self.status()
        if not status:
            return None
        return {
            "newest": status.get("newest"),
            "containers": {entry.get("container"): entry.get("version") for entry in status.get("containers") or []
                           if isinstance(entry, dict) and entry.get("version")},
            "waiting": sorted(self.waiting(status)),
            "applying": bool(self.applying(status)),
        }

    # ── the operator's request ──

    def ask(self, containers: list[str], force: bool, by: str) -> dict:
        """Write a request for watadeploy; what it would refuse is refused here first, and said."""
        status = self.status()
        if not status.get("newest"):
            raise ReleaseRefused("watadeploy has said nothing yet on this server: run watadeploy status there")
        if self.applying(status):
            raise ReleaseRefused("An apply is under way: wait for it to end")
        waiting = self.waiting(status)
        wanted = list(dict.fromkeys(containers))
        if not wanted:
            raise ReleaseRefused("No container named")
        strangers = [name for name in wanted if name not in waiting]
        if strangers:
            raise ReleaseRefused(f"Not waiting for {status['newest']}: {', '.join(strangers)}")
        identifier = os.urandom(16).hex()
        request = {"id": identifier, "version": status["newest"], "containers": wanted, "force": bool(force),
                   "asked_by": by[:80], "asked_at": _now_iso(self._clock)}
        _write_json(os.path.join(self._requests_path, f"{identifier}.json"), request)
        return request

    # ── what is said of it ──

    def _announced(self) -> dict:
        try:
            with open(self._announced_path, encoding="utf-8") as announced:
                found = json.load(announced)
        except (OSError, ValueError):
            found = {}
        return found if isinstance(found, dict) else {}

    def news(self, status: dict, config: dict, now: float) -> list[str]:
        """What is to be said once, of the status as it is now; remembered as said."""
        announced = self._announced()
        applies = list(announced.get("applies") or [])
        refusals = list(announced.get("config") or [])
        messages = []
        last = status.get("last")
        if isinstance(last, dict) and last.get("done") and last.get("id") and last["id"] not in applies:
            # The first look of an admin started before any apply has nothing to say of an old one.
            if announced or (status.get("last") or {}).get("finished_at", "") >= _now_iso(lambda: now - 3600):
                messages.append(outcome_said(last))
            applies.append(last["id"])
        for image in ((status.get("config") or {}).get("images") or []):
            key = f"{image.get('image')}|{image.get('detail')}"
            if image.get("accepted") is False and key not in refusals:
                users = ", ".join(image.get("containers") or []) or "the newest release"
                messages.append(f"⚠️ WATA: {users} on {image.get('image')} would not start on config.json as it is "
                                f"({image.get('detail')}): apply {status.get('newest')} to it before it restarts")
                refusals.append(key)
        reminded = announced.get("reminded_on")
        waiting = sorted(self.waiting(status))
        closed_day = self._closed_day(config, now)
        if waiting and closed_day and reminded != closed_day:
            messages.append(f"🕗 WATA {status.get('newest')} waits for {', '.join(waiting)}: apply it from the "
                            f"System page, or with watadeploy apply on the server")
            reminded = closed_day
        changed = {"applies": applies[-KEPT_ANNOUNCED:], "config": refusals[-KEPT_ANNOUNCED:], "reminded_on": reminded}
        if changed != {key: announced.get(key) for key in changed}:
            _write_json(self._announced_path, changed)
        return messages

    @staticmethod
    def _closed_day(config: dict, now: float) -> str | None:
        """The day, in the market's timezone, once that weekday's hours are over; None otherwise."""
        try:
            hours = trading_hours(config)
            local = datetime.fromtimestamp(now, ZoneInfo(hours[0]))
            if local.weekday() >= 5 or within_hours(hours, now) or local.hour < hours[2]:
                return None
        except (KeyError, ValueError, TypeError):
            return None
        return local.date().isoformat()

    async def keep(self, ctx) -> None:
        """Watch the status: tell the open pages when it changes, and say once what is news."""
        while True:
            try:
                await self.tick(ctx)
            except Exception as e:
                logger.warning(f"Could not read the release's status: {e}")
            await asyncio.sleep(WATCH_SECONDS)

    async def tick(self, ctx) -> None:
        try:
            mtime = os.stat(self._status_path).st_mtime
        except OSError:
            return
        status = self.status()
        if mtime != self._seen_mtime:
            self._seen_mtime = mtime
            ctx.events.publish("release", applying=bool(self.applying(status)), last=(status.get("last") or {}).get("id"))
        try:
            with open(ctx.settings.config_path, encoding="utf-8") as config_file:
                config = json.load(config_file)
        except (OSError, ValueError):
            config = {}
        for message in self.news(status, config, self._clock()):
            if ctx.notifier is not None:
                try:
                    await asyncio.wait_for(ctx.notifier(message), 10)
                except Exception as e:
                    logger.warning(f"Could not say on Telegram what the deploy did: {e}")
