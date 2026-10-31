"""
Whether LuxAlgo's indicators run on WATA ("Runs on WATA?"): each source the
admin reads whole is checked as WATA checks a script (a Pine study's check,
src/research/study.py: TradingView's rules, a compile and a run of PineTS on
made-up bars), and what it declares is read from it (its alertcondition()
titles, its alert() calls, whether it is a strategy()). Kept by slug and by
the source's sha256 (``luxalgo_compat.json`` beside the scripts' origins), so
that a source is never checked twice.

- A source is checked only whole: signed out, LuxAlgo gives the first lines of
  most indicators, noted ``cut`` and asked again once the admin signs in.
- One check at a time, through the studies, which run one at a time: a check
  starts only while no study runs, so it never holds up Claude's or the
  agent's; CHECKS_AN_HOUR at most, sparing LuxAlgo's hourly calls and the
  admin's processor.
- Nothing here trades or is sent anywhere but to LuxAlgo (a source asked for)
  and to the study's child (the source checked).
"""

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone

from src.admin.errors import NotPossible
from src.admin.luxalgo import SLUG, LuxAlgoRefused, LuxAlgoUnknown

logger = logging.getLogger(__name__)

FILE = "luxalgo_compat.json"
CHECKS_AN_HOUR = 20
# Slugs waiting at most, and asked at once.
QUEUED_MOST = 200
ASKED_MOST = 50
# How long a check waits when the studies are busy, or LuxAlgo did not answer, before it is tried again.
RETRY_SECONDS = 60
IDLE_SECONDS = 5
RUNS, NO_ALERTS, FAILS, CUT = "runs", "no_alerts", "fails", "cut"
STATES = (RUNS, NO_ALERTS, FAILS, CUT)
# What an entry keeps of the first errors.
ERRORS_KEPT = 3


def stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _code_and_strings(source: str) -> list[tuple[str, str]]:
    """The source as pieces of code and of string literals, its comments left out."""
    pieces, at, length = [], 0, len(source)
    code = []
    while at < length:
        char = source[at]
        if char == "/" and source.startswith("//", at):
            end = source.find("\n", at)
            at = length if end < 0 else end
            continue
        if char in "\"'":
            if code:
                pieces.append(("code", "".join(code)))
                code = []
            end, text = at + 1, []
            while end < length and source[end] != char and source[end] != "\n":
                if source[end] == "\\" and end + 1 < length:
                    text.append(source[end + 1])
                    end += 2
                    continue
                text.append(source[end])
                end += 1
            pieces.append(("string", "".join(text)))
            at = end + 1
            continue
        code.append(char)
        at += 1
    if code:
        pieces.append(("code", "".join(code)))
    return pieces


def _arguments(pieces: list[tuple[str, str]], start: int, offset: int) -> list[list[tuple[str, str]]]:
    """The arguments of the call whose "(" is at ``offset`` of the code piece ``start``, each as its pieces."""
    depth, arguments, current = 0, [], []
    index, position = start, offset
    while index < len(pieces):
        kind, text = pieces[index]
        if kind == "string":
            current.append(("string", text))
            index, position = index + 1, 0
            continue
        while position < len(text):
            char = text[position]
            position += 1
            if char in "([":
                depth += 1
                if depth == 1 and char == "(":
                    continue
            elif char in ")]":
                depth -= 1
                if depth == 0:
                    arguments.append(current)
                    return arguments
            elif char == "," and depth == 1:
                arguments.append(current)
                current = []
                continue
            current.append(("code", char))
        index, position = index + 1, 0
    return arguments


def _string_of(argument: list[tuple[str, str]]) -> str | None:
    """The literal an argument is (or names, ``title = "…"``), None otherwise."""
    code = "".join(text for kind, text in argument if kind == "code").strip()
    strings = [text for kind, text in argument if kind == "string"]
    if len(strings) == 1 and code in ("", "title =", "title="):
        return strings[0]
    return None


def declared(source: str) -> dict:
    """What a source declares: its alertcondition() titles, its alert() calls, whether it is a strategy()."""
    pieces = _code_and_strings(source or "")
    titles: list[str] = []
    alert_calls = 0
    strategy = False
    for index, (kind, text) in enumerate(pieces):
        if kind != "code":
            continue
        position = 0
        while True:
            found = _next_call(text, position)
            if found is None:
                break
            name, opening = found
            position = opening + 1
            if name == "alertcondition":
                arguments = _arguments(pieces, index, opening)
                named = [_string_of(argument) for argument in arguments
                         if "".join(t for k, t in argument if k == "code").strip().startswith("title")]
                title = next((said for said in named if said), None)
                if title is None and len(arguments) > 1:
                    title = _string_of(arguments[1])
                if title and title not in titles:
                    titles.append(title)
            elif name == "alert":
                alert_calls += 1
            elif name == "strategy":
                strategy = True
    return {"alertconditions": titles, "alert_calls": alert_calls, "strategy": strategy}


def _next_call(text: str, position: int) -> tuple[str, int] | None:
    """The next call of alertcondition, alert or strategy in a piece of code, and where its "(" is."""
    best = None
    for name in ("alertcondition", "alert", "strategy"):
        at = position
        while True:
            at = text.find(name, at)
            if at < 0:
                break
            before = text[at - 1] if at > 0 else " "
            rest = text[at + len(name):]
            stripped = rest.lstrip(" ")
            if (before.isalnum() or before in "_.") or not stripped.startswith("("):
                at += len(name)
                continue
            opening = at + len(name) + (len(rest) - len(stripped))
            if best is None or at < best[2]:
                best = (name, opening, at)
            break
    return (best[0], best[1]) if best else None


def entry_of(found: dict, checked: dict | None, at: float, *, cut: str | None = None) -> dict:
    """What is kept of a check: its state, and what made it."""
    entry = {"slug": found["slug"], "name": found.get("name"), "checked_at": stamp(at)}
    if cut is not None:
        return {**entry, "state": CUT, "sha256": None, "why": cut}
    source = found["source"]
    said = declared(source)
    rules = (checked or {}).get("tradingview_rules") or []
    rules_errors = [finding.get("message") for finding in rules
                    if isinstance(finding, dict) and finding.get("severity") == "error"][:ERRORS_KEPT]
    compiles = bool(checked and checked.get("ok"))
    error = None
    if not compiles:
        failed = (checked or {}).get("error") or {}
        error = failed.get("message") if isinstance(failed, dict) else str(failed or "it did not compile")
    if not compiles:
        state = FAILS
    elif said["alertconditions"] or said["alert_calls"]:
        state = RUNS
    else:
        state = NO_ALERTS
    return {
        **entry, "state": state, "sha256": hashlib.sha256(source.encode()).hexdigest(), "compiles": compiles,
        "error": (error or "")[:300] or None, "rules_errors": rules_errors, **said,
        "alerts_on_made_up_bars": (checked or {}).get("alerts_on_made_up_bars") or {},
        "plots": len((checked or {}).get("plots") or []), "inputs": len((checked or {}).get("inputs") or []),
    }


class CompatStore:
    """``root``: the Technical Analysis folder (admin.persistant.ta_path), beside the scripts' origins."""

    def __init__(self, root: str):
        self._path = os.path.join(root, FILE)
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as in_file:
                found = json.load(in_file)
        except (FileNotFoundError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def all(self) -> dict[str, dict]:
        with self._lock:
            return self._read()

    def get(self, slug: str) -> dict | None:
        return self.all().get(slug)

    def record(self, entry: dict) -> dict:
        if not SLUG.match(str(entry.get("slug") or "")):
            raise ValueError(f"No indicator {entry.get('slug')!r}")
        with self._lock:
            kept = self._read()
            kept[entry["slug"]] = entry
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            partial = f"{self._path}.part"
            with open(partial, "w", encoding="utf-8") as out_file:
                json.dump(kept, out_file, ensure_ascii=False, indent=1)
            os.replace(partial, self._path)
        return entry


class CompatChecker:
    """
    The checks asked, one at a time (a task of the admin's lifespan, ``run``).
    ``luxalgo``: the admin's client (src/admin/luxalgo.py); ``studies``: the
    Pine studies (src/admin/studies.py), None where the admin has none.
    """

    def __init__(self, store: CompatStore, luxalgo, studies, clock=time.time):
        self.store = store
        self._luxalgo = luxalgo
        self._studies = studies
        self._clock = clock
        self._queue: OrderedDict[str, None] = OrderedDict()
        self._done: deque = deque()
        self._wake = asyncio.Event()
        self._checking: str | None = None

    @property
    def available(self) -> bool:
        return self._studies is not None

    def ask(self, slugs: list[str]) -> dict:
        """Slugs to check: each once in the queue, QUEUED_MOST at most waiting."""
        if not self.available:
            raise NotPossible("The checks need the Pine studies, which this admin does not run")
        asked = [slug for slug in dict.fromkeys(slugs) if isinstance(slug, str) and SLUG.match(slug)][:ASKED_MOST]
        added = 0
        for slug in asked:
            if slug not in self._queue and slug != self._checking and len(self._queue) < QUEUED_MOST:
                self._queue[slug] = None
                added += 1
        self._wake.set()
        return {**self.state(), "added": added}

    def noticed(self, slug: str, source: str) -> None:
        """A source the admin read whole elsewhere: checked when it is not as kept."""
        if not self.available or not SLUG.match(slug):
            return
        kept = self.store.get(slug)
        if kept and kept.get("sha256") == hashlib.sha256(source.encode()).hexdigest():
            return
        if slug not in self._queue and slug != self._checking and len(self._queue) < QUEUED_MOST:
            self._queue[slug] = None
            self._wake.set()

    def signed_in(self) -> None:
        """The admin signed in to LuxAlgo: the sources found cut are asked for again, whole now."""
        if not self.available:
            return
        cut = [slug for slug, entry in self.store.all().items() if entry.get("state") == CUT]
        for slug in cut:
            if slug not in self._queue and len(self._queue) < QUEUED_MOST:
                self._queue[slug] = None
        self._wake.set()

    def _done_this_hour(self) -> int:
        hour_ago = self._clock() - 3600
        while self._done and self._done[0] <= hour_ago:
            self._done.popleft()
        return len(self._done)

    def state(self) -> dict:
        return {"available": self.available, "queued": list(self._queue), "checking": self._checking,
                "this_hour": self._done_this_hour(), "an_hour": CHECKS_AN_HOUR}

    async def check(self, slug: str) -> dict | None:
        """One indicator checked now: its entry, or None when there was nothing to do (the same source, unknown)."""
        try:
            found = await self._luxalgo.source(slug)
        except LuxAlgoUnknown:
            return None
        except LuxAlgoRefused as e:
            if "sign in" not in str(e):
                raise
            return self.store.record(entry_of({"slug": slug}, None, self._clock(), cut=str(e)[:300]))
        kept = self.store.get(slug)
        if kept and kept.get("sha256") == hashlib.sha256(found["source"].encode()).hexdigest():
            return None
        checked = await self._studies.study("check", {"source": found["source"], "name": found.get("name") or slug})
        return self.store.record(entry_of(found, checked, self._clock()))

    async def run(self) -> None:
        """The queue checked one at a time, while no study runs and the hour has room."""
        while True:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            if self._done_this_hour() >= CHECKS_AN_HOUR or self._studies.busy:
                await asyncio.sleep(IDLE_SECONDS if self._studies.busy else RETRY_SECONDS)
                continue
            slug, _ = self._queue.popitem(last=False)
            self._checking = slug
            try:
                if await self.check(slug) is not None:
                    self._done.append(self._clock())
            except asyncio.CancelledError:
                raise
            except (NotPossible, LuxAlgoRefused) as e:
                # The studies busy or out of room, or LuxAlgo not answering: tried again later.
                logger.info(f"The check of LuxAlgo's {slug} waits: {e}")
                self._queue[slug] = None
                await asyncio.sleep(RETRY_SECONDS)
            except Exception:
                logger.exception(f"The check of LuxAlgo's {slug} failed")
            finally:
                self._checking = None
