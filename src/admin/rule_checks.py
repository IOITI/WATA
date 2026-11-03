"""
The rule checks of the Technical Analysis pages: whether each script, at each
version, and each LuxAlgo indicator read whole, can become a signal rule as it
is (src/research/rule_check.py).

- What its source says is made here, by a checker process of the admin's own,
  one source at a time and outside the studies' lock: when a version is saved
  or a LuxAlgo source read whole, when a report is asked for, and in the
  background for a list that lacks some.
- Its run test on recorded bars is a Pine study (``rule``, src/admin/studies.py)
  asked from a page, the MCP endpoint or the agent: one at a time, only while
  no other study runs, RUNS_AN_HOUR at most. Nothing runs one unasked.
- Each report is kept by what runs (``<ta_path>/rule_checks/<digest>.json``:
  the source and its libraries, src/research/rule_check.py's digest_of), so a
  new version of a library, or a new source of LuxAlgo's, is a new report: a
  report never speaks for code it did not read. The newest KEPT are kept.
- A run test counts for a rule only when it was made as the rule runs: its
  asset, bars, inputs and history, its own pins (``of_rule``).

Nothing here trades, nor is audited: a rule check changes nothing live.
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import Counter, OrderedDict, deque
from datetime import datetime, timezone

from src.admin.errors import NotPossible
from src.admin.ta_rules import InvalidRule, RuleStore
from src.admin.ta_scripts import LIBRARY, pine_facts
from src.price_history.process import cgroup_memory
from src.research import rule_check as checks

logger = logging.getLogger(__name__)

FOLDER = "rule_checks"
LUXALGO_INDEX = "luxalgo.json"
KEPT = 2000
TESTS_KEPT = 5
RUNS_AN_HOUR = 6
QUEUED_MOST = 20
STATICS_QUEUED_MOST = 500
RETRY_SECONDS = 60
IDLE_SECONDS = 5
# What the checker process needs free beside the admin: it is small, but a backtest may be filling the container.
CHECKER_FREE_MB = 384
DIGEST = re.compile(r"^[0-9a-f]{64}$")
# The defaults of a run test asked without its bars: Capital.com's Nasdaq, the bid, minutes.
DEFAULT_SETTINGS = {"asset": "capital/US100", "side": "bid", "bars": "1"}


class NoChecker(NotPossible):
    """No Node, or no built checker (npm run build:checker): no rule check is made here."""


def stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RuleCheckStore:
    """``root``: the Technical Analysis folder (admin.persistant.ta_path)."""

    def __init__(self, root: str):
        self.root = os.path.join(root, FOLDER)
        self._lock = threading.Lock()

    def _path(self, digest: str) -> str:
        if not isinstance(digest, str) or not DIGEST.match(digest):
            raise ValueError(f"No report {digest!r}")
        return os.path.join(self.root, f"{digest}.json")

    def get(self, digest: str) -> dict | None:
        try:
            with open(self._path(digest), encoding="utf-8") as in_file:
                found = json.load(in_file)
        except (OSError, ValueError):
            return None
        return found if isinstance(found, dict) else None

    def _write(self, path: str, value) -> None:
        os.makedirs(self.root, exist_ok=True)
        partial = f"{path}.part"
        with open(partial, "w", encoding="utf-8") as out_file:
            json.dump(value, out_file, ensure_ascii=False)
        os.replace(partial, path)

    def put(self, entry: dict) -> dict:
        path = self._path(entry["digest"])
        with self._lock:
            self._write(path, entry)
            slug = (entry.get("about") or {}).get("luxalgo")
            if slug:
                index = self._luxalgo()
                index[slug] = entry["digest"]
                self._write(os.path.join(self.root, LUXALGO_INDEX), index)
            self._prune()
        return entry

    def _luxalgo(self) -> dict:
        try:
            with open(os.path.join(self.root, LUXALGO_INDEX), encoding="utf-8") as in_file:
                found = json.load(in_file)
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def luxalgo_slugs(self) -> list[str]:
        """The LuxAlgo indicators a report was made of."""
        with self._lock:
            return list(self._luxalgo())

    def of_luxalgo(self, slug: str) -> dict | None:
        """The newest report of a LuxAlgo indicator, whichever source it read."""
        with self._lock:
            digest = self._luxalgo().get(slug)
        return self.get(digest) if digest else None

    def _prune(self) -> None:
        try:
            names = [name for name in os.listdir(self.root) if name.endswith(".json") and name != LUXALGO_INDEX]
        except OSError:
            return
        if len(names) <= KEPT:
            return
        dated = sorted(names, key=lambda name: os.path.getmtime(os.path.join(self.root, name)))
        for name in dated[:len(names) - KEPT]:
            try:
                os.remove(os.path.join(self.root, name))
            except OSError:
                pass


def settings_of(bars, asset: str | None = None, side: str | None = None, inputs: dict | None = None,
                history_days: int | None = None, rule: str | None = None) -> dict:
    """What a run test is run with, as kept: its bars as a rule keeps them ({"type": "time", "timeframe": "1"})."""
    if isinstance(bars, str):
        bars = {"type": "range", "ticks": int(bars[1:])} if bars[:1] in ("R", "r") and bars[1:].isdigit() else \
            {"type": "time", "timeframe": bars}
    return {"asset": asset or DEFAULT_SETTINGS["asset"], "side": side or DEFAULT_SETTINGS["side"], "bars": bars,
            "inputs": inputs or {}, "history_days": history_days, "rule": rule}


def _bars_text(bars: dict) -> str:
    return f"R{bars.get('ticks')}" if bars.get("type") == "range" else str(bars.get("timeframe") or "1")


def view(entry: dict, test: dict | None = None) -> dict:
    """
    A report as the pages read it: what the source says and, with ``test`` (one
    of its run tests; the newest by default), what that test found, joined.
    """
    tests = entry.get("tests") or []
    chosen = test if test is not None else (tests[0] if tests else None)
    static = entry.get("static") or {}
    joined = checks.combined(static, chosen["tested"] if chosen else None)
    check = static.get("check") or {}
    return {
        "digest": entry["digest"], "about": entry.get("about"), "checked_at": entry.get("static_at"),
        "kind": check.get("kind"), "pine_version": check.get("pineVersion"), "alerts": check.get("alerts") or [],
        "alert_calls": check.get("alertCalls") or [], "candidates": check.get("candidates") or [],
        "plots": check.get("plots") or [], "whole_because": check.get("wholeBecause"),
        "tradingview_rules": [finding for finding in static.get("tradingview_rules") or [] if finding.get("source") != "WATA"],
        "findings": joined["findings"], "verdict": joined["verdict"],
        "tested": {"settings": chosen["settings"], "at": chosen["at"], **chosen["tested"]} if chosen else None,
        "tests": [{"settings": entry_["settings"], "at": entry_["at"],
                   "verdict": checks.combined(static, entry_["tested"])["verdict"]} for entry_ in tests],
    }


def summary(entry: dict | None, test: dict | None = None) -> dict | None:
    """A report in a few words, for lists and readiness: its verdict, its findings counted, its worst, when tested."""
    if entry is None:
        return None
    seen = view(entry, test)
    order = {level: index for index, level in enumerate(checks.LEVELS)}
    worst = min(seen["findings"], key=lambda finding: order.get(finding["level"], len(order)), default=None)
    return {
        "verdict": seen["verdict"], "checked_at": seen["checked_at"],
        "counts": dict(Counter(finding["level"] for finding in seen["findings"])),
        "worst": {"id": worst["id"], "level": worst["level"], "message": worst["message"]} if worst else None,
        "tested_at": seen["tested"]["at"] if seen["tested"] else None,
        "tested_bars": _bars_text(seen["tested"]["settings"]["bars"]) if seen["tested"] else None,
        "candidates": len(seen["candidates"]),
    }


async def readiness_check(rule_checks, rule: dict) -> dict | None:
    """What going live reads of a rule's script as a rule (ta_readiness.py); None where it cannot be read here."""
    if rule_checks is None:
        return None
    try:
        return await rule_checks.summary_of_rule(rule)
    except NotPossible as e:
        logger.warning(f"The rule check of {rule['id']} could not be read for its readiness: {e}")
        return None


class RuleChecks:
    """
    ``scripts``: the Pine scripts (src/admin/ta_scripts.py); ``studies``: the
    Pine studies (src/admin/studies.py), None where the admin has none (no run
    test then); ``lockbox``: the days of Claude's research lock box, avoided by a
    test asked without its days; ``checker``: the checker's client.
    """

    def __init__(self, store: RuleCheckStore, scripts, studies, *, checker=None, node: str = "node", lockbox=None,
                 memory=cgroup_memory, clock=time.time):
        self.store = store
        self._scripts = scripts
        self._studies = studies
        self._checker = checker if checker is not None else checks.Checker(node)
        self._checker_lock = asyncio.Lock()
        self._lockbox = lockbox
        self._memory = memory
        self._clock = clock
        self._statics: OrderedDict[str, tuple] = OrderedDict()
        self._tests: OrderedDict[str, dict] = OrderedDict()
        self._testing: dict | None = None
        self._done: deque = deque()
        self._wake = asyncio.Event()
        self.last_refusal: dict | None = None

    @property
    def available(self) -> bool:
        """Whether a run test can be asked: it is a Pine study."""
        return self._studies is not None

    # ── what runs ──

    def _version(self, script_id: str, version: int | None) -> tuple[dict, str, list[dict]] | None:
        """A saved version, its libraries as a rule made of it now would pin them; None for a library."""
        meta = self._scripts.script(script_id)
        number = version or meta["version"]
        source = self._scripts.source(meta["id"], number)
        facts = pine_facts(source)
        if facts.kind == LIBRARY:
            return None
        try:
            pins = RuleStore(self._scripts).libraries_of(facts, meta["name"])
        except InvalidRule:
            # A library it imports is not in WATA: the check says so, on the script alone.
            pins = []
        libraries = [{**pin, "source": self._scripts.source(pin["id"], pin["version"])} for pin in pins]
        return {"script": {"id": meta["id"], "version": number}}, source, libraries

    def _rule(self, rule: dict) -> tuple[dict, str, list[dict]]:
        """A rule as it runs: its script's version, and its libraries at the versions it pins."""
        source = self._scripts.source(rule["script"]["id"], rule["script"]["version"])
        libraries = [{**pin, "source": self._scripts.source(pin["id"], pin["version"])} for pin in rule["libraries"]]
        return {"script": {"id": rule["script"]["id"], "version": rule["script"]["version"]}}, source, libraries

    # ── what the source says ──

    def _fresh(self, entry: dict) -> bool:
        known = self._checker.versions.get("rule_check")
        found = ((entry.get("static") or {}).get("check") or {}).get("version")
        return known is None or found == known

    async def _static(self, source: str, libraries: list[dict]) -> dict:
        async with self._checker_lock:
            found = self._memory()
            if found is not None and (found[1] - found[0]) / 1024 ** 2 < CHECKER_FREE_MB:
                raise NotPossible("The admin is short of memory for the rule check now: a backtest is running")
            try:
                return await self._checker.rule_check(source, libraries)
            except checks.CheckerMissing as e:
                raise NoChecker(str(e)) from None
            except (checks.RunnerFailed, RuntimeError, OSError) as e:
                raise NotPossible(f"The checker did not answer: {e}") from None

    async def entry(self, about: dict, source: str, libraries: list[dict], *, static: dict | None = None) -> dict:
        """The report of a source, its static part made now when it has none, or one of an older check."""
        digest = checks.digest_of(source, libraries)
        kept = await asyncio.to_thread(self.store.get, digest)
        if kept and static is None and self._fresh(kept):
            return kept
        made = static if static is not None else await self._static(source, libraries)
        entry = {**(kept or {}), "digest": digest, "about": about, "static": made, "static_at": stamp(self._clock()),
                 "tests": (kept or {}).get("tests") or []}
        return await asyncio.to_thread(self.store.put, entry)

    def _want(self, about: dict, source: str, libraries: list[dict]) -> None:
        digest = checks.digest_of(source, libraries)
        if digest not in self._statics and len(self._statics) < STATICS_QUEUED_MOST:
            self._statics[digest] = (about, source, libraries)
            self._wake.set()

    def noticed(self, about: dict, source: str, libraries: list[dict] | None = None, *, static: dict | None = None) -> None:
        """
        A source read, saved or checked elsewhere: its report kept, made in the
        background when ``static`` (what a check study already read in it) is not given.
        """
        if static is not None:
            self._background(self.entry(about, source, libraries or [], static=static))
        else:
            self._want(about, source, libraries or [])

    def noticed_version(self, script_id: str, version: int | None = None) -> None:
        """A version saved: its report made in the background."""
        try:
            found = self._version(script_id, version)
        except Exception:
            logger.exception(f"The rule check of {script_id} v{version} could not be asked")
            return
        if found is not None:
            self._want(*found)

    def _background(self, work) -> None:
        def said(done: asyncio.Future) -> None:
            if not done.cancelled() and done.exception() is not None:
                logger.warning(f"A rule check was not kept: {done.exception()}")

        asyncio.ensure_future(work).add_done_callback(said)

    # ── what the pages read ──

    async def of_version(self, script_id: str, version: int | None = None) -> dict | None:
        """The whole report of a saved version (None for a library), made when it has none."""
        found = await asyncio.to_thread(self._version, script_id, version)
        if found is None:
            return None
        return view(await self.entry(*found))

    async def of_luxalgo(self, slug: str, source: str) -> dict:
        return view(await self.entry({"luxalgo": slug}, source, []))

    def luxalgo_summary(self, slug: str) -> dict | None:
        """What the Library's cards say of an indicator: its newest report, never made here."""
        return summary(self.store.of_luxalgo(slug))

    def matching(self, entry: dict | None, rule: dict) -> dict | None:
        """The newest run test of a report made as ``rule`` runs: its asset, bars, inputs and history."""
        asset = f"{rule['asset']['provider']}/{rule['asset']['key']}"
        inputs = {entry_["id"]: entry_["value"] for entry_ in rule.get("inputs") or []}
        for test in (entry or {}).get("tests") or []:
            settings = test["settings"]
            if (settings.get("rule") == rule["id"] and settings.get("asset") == asset and settings.get("side") == rule["side"]
                    and settings.get("bars") == rule["bars"] and (settings.get("inputs") or {}) == inputs
                    and (rule.get("history_days") is None or settings.get("history_days") == rule["history_days"])):
                return test
        return None

    async def of_rule(self, rule: dict) -> dict:
        """
        A rule's report: its pins' static part, and the newest run test made as it
        runs, ``as_rule`` False when there is none (another test may be shown).
        """
        entry = await self.entry(*self._rule(rule))
        test = self.matching(entry, rule)
        return {**view(entry, test), "as_rule": test is not None}

    async def summary_of_rule(self, rule: dict) -> dict:
        """What going live reads of a rule's script: its report's verdict, worst finding, and whether tested as it runs."""
        entry = await self.entry(*self._rule(rule))
        test = self.matching(entry, rule)
        found = summary(entry, test) if test else summary({**entry, "tests": []})
        return {**found, "as_rule": test is not None}

    async def summaries(self) -> dict:
        """
        The summary of every script's newest version, and of every version a
        rule pins: from what is kept, the missing ones asked in the background.
        """
        def read():
            metas = self._scripts.scripts()
            rules = RuleStore(self._scripts).load()["rules"]
            versions, pinned = {}, {}
            for meta in metas:
                if meta.get("kind") == LIBRARY:
                    continue
                try:
                    found = self._version(meta["id"], meta["version"])
                except Exception:
                    continue
                if found is not None:
                    versions[meta["id"]] = found
            for rule in rules:
                try:
                    pinned[rule["id"]] = (rule, *self._rule(rule))
                except Exception:
                    continue
            return versions, pinned

        versions, pinned = await asyncio.to_thread(read)
        scripts, rules = {}, {}
        for script_id, (about, source, libraries) in versions.items():
            kept = await asyncio.to_thread(self.store.get, checks.digest_of(source, libraries))
            if kept is None or not self._fresh(kept):
                self._want(about, source, libraries)
            scripts[script_id] = {**summary(kept), "version": about["script"]["version"]} if kept else None
        for rule_id, (rule, about, source, libraries) in pinned.items():
            kept = await asyncio.to_thread(self.store.get, checks.digest_of(source, libraries))
            if kept is None or not self._fresh(kept):
                self._want(about, source, libraries)
            test = self.matching(kept, rule)
            found = (summary(kept, test) if test else summary({**kept, "tests": []})) if kept else None
            rules[rule_id] = {**found, "as_rule": test is not None} if found else None
        return {"scripts": scripts, "rules": rules, "state": self.state()}

    # ── run tests ──

    def _avoid(self) -> list:
        box = self._lockbox() if self._lockbox is not None else None
        if not box:
            return []
        return [[box["from"].isoformat(), box["to"].isoformat() if box.get("to") else None]]

    def ask(self, about: dict, settings: dict, *, source: str | None = None) -> dict:
        """
        A run test asked: of a saved version (``about`` {"script"}), a rule as it
        runs ({"rule"}) or a LuxAlgo source ({"luxalgo"}, its ``source``); each
        once in the queue, QUEUED_MOST at most waiting.
        """
        if not self.available:
            raise NotPossible("The run test is a Pine study, which this admin does not run")
        key = json.dumps([about, settings], sort_keys=True)
        if key in self._tests or (self._testing and self._testing["key"] == key):
            return self.state()
        if len(self._tests) >= QUEUED_MOST:
            raise NotPossible(f"{QUEUED_MOST} run tests wait already: ask again once some are done")
        self._tests[key] = {"key": key, "about": about, "settings": settings, "source": source, "asked_at": stamp(self._clock())}
        self._wake.set()
        return self.state()

    def _request(self, job: dict) -> dict:
        about, settings = job["about"], job["settings"]
        if "rule" in about:
            request = {"rule": about["rule"]}
        else:
            request = ({"source": job["source"], "name": about.get("luxalgo")} if "luxalgo" in about
                       else {"script": dict(about["script"])})
            request.update(asset=settings["asset"], side=settings["side"], bars=_bars_text(settings["bars"]),
                           inputs=settings["inputs"], history_days=settings["history_days"])
        return {**request, "avoid": self._avoid()}

    def _done_this_hour(self) -> int:
        hour_ago = self._clock() - 3600
        while self._done and self._done[0] <= hour_ago:
            self._done.popleft()
        return len(self._done)

    def state(self) -> dict:
        return {
            "available": self.available,
            "queued": [{"about": job["about"], "settings": job["settings"], "asked_at": job["asked_at"]} for job in self._tests.values()],
            "testing": {"about": self._testing["about"], "settings": self._testing["settings"]} if self._testing else None,
            "this_hour": self._done_this_hour(), "an_hour": RUNS_AN_HOUR, "checking": len(self._statics),
            "last_refusal": self.last_refusal,
        }

    async def _test(self, job: dict) -> None:
        about = job["about"]
        if "rule" in about:
            rule = await asyncio.to_thread(RuleStore(self._scripts).rule, about["rule"])
            found = self._rule(rule)
        elif "luxalgo" in about:
            found = ({"luxalgo": about["luxalgo"]}, job["source"], [])
        else:
            found = await asyncio.to_thread(self._version, about["script"]["id"], about["script"]["version"])
        if found is None:
            return
        answer = await self._studies.study("rule", self._request(job))
        if not answer.get("ok"):
            self.last_refusal = {"about": about, "message": (answer.get("error") or {}).get("message"), "at": stamp(self._clock())}
            return
        self.last_refusal = None
        await self._keep(found, answer, job["settings"])

    async def _keep(self, found: tuple[dict, str, list[dict]], answer: dict, asked: dict | None = None) -> None:
        """A run test's answer kept with its report, by the settings it ran with: the newest of each, TESTS_KEPT in all."""
        tested = answer.get("tested")
        entry = await self.entry(*found, static=answer.get("static"))
        if tested is None:
            return
        made = {key: value for key, value in (tested.get("settings") or {}).items() if value is not None}
        settings = {**(asked or {}), **made}
        tests = [test for test in entry.get("tests") or [] if test["settings"] != settings]
        entry["tests"] = [{"settings": settings, "at": stamp(self._clock()), "tested": tested}, *tests][:TESTS_KEPT]
        await asyncio.to_thread(self.store.put, entry)

    async def keep_answer(self, script_id: str, version: int | None, answer: dict) -> None:
        """A run test of a saved version made elsewhere (Claude's pine_rule_check): kept for its page."""
        found = await asyncio.to_thread(self._version, script_id, version)
        if found is not None and answer.get("ok"):
            await self._keep(found, answer)

    async def run(self) -> None:
        """What the source says made first, as asked; then the run tests, one at a time while no study runs."""
        while True:
            if self._statics:
                digest, (about, source, libraries) = self._statics.popitem(last=False)
                try:
                    await self.entry(about, source, libraries)
                except asyncio.CancelledError:
                    raise
                except NoChecker as e:
                    # Not built here (npm run build:checker): nothing to wait for.
                    logger.warning(f"No rule check is made: {e}")
                    self._statics.clear()
                except NotPossible as e:
                    logger.info(f"A rule check waits: {e}")
                    self._statics[digest] = (about, source, libraries)
                    await asyncio.sleep(RETRY_SECONDS)
                except Exception:
                    logger.exception("A rule check failed")
                continue
            if not self._tests:
                self._wake.clear()
                await self._wake.wait()
                continue
            if self._done_this_hour() >= RUNS_AN_HOUR or self._studies.busy:
                await asyncio.sleep(IDLE_SECONDS if self._studies.busy else RETRY_SECONDS)
                continue
            _, job = self._tests.popitem(last=False)
            self._testing = job
            try:
                await self._test(job)
                self._done.append(self._clock())
            except asyncio.CancelledError:
                raise
            except NotPossible as e:
                # The studies busy or out of room: tried again later.
                logger.info(f"A run test waits: {e}")
                self._tests[job["key"]] = job
                await asyncio.sleep(RETRY_SECONDS)
            except Exception:
                logger.exception("A run test failed")
            finally:
                self._testing = None

    async def close(self) -> None:
        await self._checker.stop()
