"""
A backtest configuration made again after each trading day (the Backtests page's "Make it again each evening").

A configuration is every run whose result is decided by the same things but its days
(src/admin/backtest_configurations.py). Switched on, once the market is closed by the live rules, its newest
finished run is asked again (``BacktestService.extend``) over its days stretched to the day just over (``day``):
from the day the operator chose, a year of days at most (and the period limit of the runs), then the first day moves
on (``nightly_window``). Its settings, script and libraries stay as that run resolved them, whatever config.json and
the scripts say now: that is the configuration. The signals WATA received, or those a rule raised, are read again
for the new days.

Once the new run is done, the configuration's nightly runs before it are let go, unless kept: among the favourites,
a reference Edge Watch judges against (never moved: it judges from its own last day), the run a stress, a fill-noise
one or a model check was made from, or one the AI agent gave a verdict on. A run made by hand is never let go. What
each nightly run said stays in the configuration's history, the newest first; a line on Telegram too, when the
operator asked for one. One that failed leaves the run before it, and is asked again the next day or by hand.

A task of the admin application, like paper trading's (src/admin/paper.py): one nightly run at a time, asked only
while no run of the operator waits, at the lowest priority as every run. Nothing here trades: no audit entry.
"""

import asyncio
import json
import logging
import time
from datetime import date, datetime, timedelta

import pytz

from src.admin.backtest_configurations import (
    DIGEST_VERSION,
    NIGHTLY_MOST_DAYS,
    UnknownConfiguration,
    base_name,
    checked_id,
    differences,
    nightly_window,
)
from src.admin.backtest_requests import newest_raised
from src.admin.backtests import JOURNAL, MAX_NAME_LENGTH, MAX_RAISED_SIGNALS, MODELLED, NIGHTLY, TA_SIGNALS
from src.admin.downloads import market_is_open, trading_hours, trading_timezone
from src.admin.errors import DatabaseUnavailable, InvalidRequest, LineFull, NotPossible, UnknownRun
from src.admin.paper import debriefed_day
from src.admin.processes import CANCELLED, DONE, FAILED, QUEUED, RUNNING, utc
from src.backtest.clock import TradingClock

logger = logging.getLogger(__name__)

TICK_SECONDS = 300
# Why a run of a configuration that ended is kept by its next nightly run: what the operator, Edge Watch or the AI
# agent rests on.
KEPT_FAVOURITE, KEPT_REFERENCE, KEPT_JUDGED, KEPT_VERDICT = "favourite", "reference", "judged", "verdict"
# What the next nightly run waits for: its day's hours to be over, the operator's runs, its own run, the next day.
WAITS_MARKET, WAITS_LINE, WAITS_UNDER_WAY, WAITS_FAILED = "market", "line", "under_way", "failed"
# Shown in a configuration's row: its newest runs.
ROW_RUNS = 8
# A failure said on Telegram: its first line, at most.
SAID_ERROR = 200
DERIVED = (("stress_of", "stress"), ("noise_of", "noise"), ("model_check_of", "model_check"))


def _day(text) -> date | None:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _next_weekday(day: date) -> date:
    following = day + timedelta(days=1)
    while following.weekday() >= 5:
        following += timedelta(days=1)
    return following


def _derived_kind(run: dict) -> str | None:
    """What a run made from another is of it: a stress, a fill-noise one, a model check; None for a run of its own."""
    return next((kind for key, kind in DERIVED if run.get(key)), None)


def _seconds(status: dict) -> float | None:
    try:
        started = datetime.fromisoformat(str(status.get("started_at")).replace("Z", "+00:00"))
        finished = datetime.fromisoformat(str(status.get("finished_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return max(0.0, (finished - started).total_seconds())


def _short_day(text) -> str:
    found = _day(text)
    return f"{found.day} {found:%b}" if found else str(text)


def said_line(name: str, run: dict, state: str) -> str | None:
    """What a nightly run's end says on Telegram; None for one that was stopped."""
    said = f"Nightly backtest “{name}” to {_short_day(run.get('to'))}"
    if state == FAILED:
        error = ((run.get("status") or {}).get("error") or "").strip().splitlines()
        return f"{said} failed: {(error[0] if error else 'it did not finish')[:SAID_ERROR]}"
    if state != DONE:
        return None
    headline = run.get("headline") or {}
    total, trades, days = headline.get("total_percent"), headline.get("trades") or 0, headline.get("days_tested")
    if total is None:
        return f"{said}: done."
    said += f": {total:+.1f} % in {trades} trade{'' if trades == 1 else 's'}"
    if days is not None:
        said += f" over {days} day{'' if days == 1 else 's'}"
    if headline.get("beaten") is not None:
        said += f", beats random {headline['beaten']:.0f} %"
    return said + "."


def _template(runs: list[dict]) -> dict | None:
    """The run a nightly run is made from: the newest finished run of one combination made by itself."""
    return next((run for run in runs if run["kind"] == "run" and (run.get("status") or {}).get("state") == DONE
                 and not _derived_kind(run)), None)


def _latest(runs: list[dict]) -> dict | None:
    """What a configuration's row shows: its newest finished run made by itself (a sweep's own, for a sweep's)."""
    return next((run for run in runs if (run.get("status") or {}).get("state") == DONE and not _derived_kind(run)), None)


def _covers(runs: list[dict], first: date, last: date) -> bool:
    """Whether a finished run of them covers those days already, whoever asked for it."""
    return any(run["kind"] == "run" and (run.get("status") or {}).get("state") == DONE and not _derived_kind(run)
               and (run.get("from") or "9999") <= first.isoformat() and (run.get("to") or "") >= last.isoformat()
               for run in runs)


def why_no_nightly(runs: list[dict]) -> str | None:
    """Why a configuration cannot be made again each evening; None when it can."""
    if _template(runs) is not None:
        return None
    if all(run["kind"] == "sweep" for run in runs):
        return "A sweep is not made again each evening: run one of its combinations as a run of its own"
    if all(_derived_kind(run) for run in runs):
        return "Its runs were made from another that is gone: ask for a run of its own first"
    return "It has no finished run to make again yet"


class NightlyBacktests:
    def __init__(self, *, backtests, queries, config_path: str, notify=None, verdict_runs=None, clock=time.time):
        self._backtests = backtests
        self._store = backtests.configurations
        self._queries = queries
        self._config_path = config_path
        # async (text) -> said on Telegram; None: nothing is said.
        self._notify = notify
        # () -> the ids of the runs the AI agent gave a verdict on, which a nightly run keeps; None: none.
        self._verdict_runs = verdict_runs
        self._clock = clock
        # The configurations whose nightly run is being asked (its signals read meanwhile): never asked twice at once.
        self._asking: set[str] = set()
        self._wakeup = asyncio.Event()
        self._telling: set[asyncio.Task] = set()
        backtests.on_finished(self._finished)

    # ── the live rules ──

    def _config(self) -> dict:
        """Read again each time: the trading hours may have been edited."""
        with open(self._config_path, encoding="utf-8") as config_file:
            return json.load(config_file)

    def day(self, now: float | None = None) -> date:
        """The newest weekday whose trading hours are over: the day a nightly run asked now goes to."""
        return debriefed_day(self._config(), self._clock() if now is None else now)

    def _moment(self, now: float) -> tuple[date, bool, date]:
        """The day a nightly run asked now goes to, whether the market is open, and the market's day now."""
        config = self._config()
        zone = trading_hours(config)[0]
        return debriefed_day(config, now), market_is_open(config, now), datetime.fromtimestamp(now, pytz.timezone(zone)).date()

    def most_days(self, turbos: str | None) -> int:
        """The days a nightly run covers at most: a year, held to the longest period a run takes."""
        limit = self._backtests.modelled_period_days if turbos == MODELLED else self._backtests.max_period_days
        return min(NIGHTLY_MOST_DAYS, limit)

    def _window(self, entry: dict, template: dict, day: date) -> tuple[date, date]:
        anchor = _day((entry.get("nightly") or {}).get("from")) or _day(template.get("from")) or day
        return nightly_window(anchor, day, self.most_days(template.get("turbos")))

    # ── the configurations ──

    def _runs(self) -> list[dict]:
        """Every run as the list describes it, newest first (paper runs left out)."""
        found = []
        for run_id in self._backtests.run_ids():
            try:
                found.append(self._backtests.listed(run_id))
            except UnknownRun:
                continue
        return found

    @staticmethod
    def _groups(runs: list[dict]) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for run in runs:
            if run.get("configuration"):
                groups.setdefault(run["configuration"], []).append(run)
        return groups

    def _name(self, configuration: str, runs: list[dict], entry: dict) -> str:
        """The operator's, else the name of its oldest run without what "Run again with changes" added."""
        if entry.get("name"):
            return entry["name"]
        oldest = next((run for run in reversed(runs) if not _derived_kind(run)), runs[-1] if runs else {})
        return base_name(oldest.get("name")) or configuration

    def _verdicts(self) -> set[str]:
        if self._verdict_runs is None:
            return set()
        try:
            return set(self._verdict_runs())
        except Exception as e:
            logger.warning(f"The AI agent's verdicts could not be read for the nightly backtests: {e}")
            return set()

    @staticmethod
    def _kept(run: dict, runs: list[dict], references: set[str], verdicts: set[str]) -> list[str]:
        """Why a run that ended is kept by the next nightly run of its configuration."""
        kept = []
        if run.get("favourite"):
            kept.append(KEPT_FAVOURITE)
        if run["id"] in references:
            kept.append(KEPT_REFERENCE)
        if any(other.get(key) == run["id"] for other in runs for key, _ in DERIVED):
            kept.append(KEPT_JUDGED)
        if run["id"] in verdicts:
            kept.append(KEPT_VERDICT)
        return kept

    def _line(self, run: dict, runs: list[dict], references: set[str], verdicts: set[str]) -> dict:
        """A run of a configuration as its row and its page list it."""
        headline = run.get("headline") or {}
        status = run.get("status") or {}
        nightly = bool(run.get("nightly"))
        return {
            "id": run["id"], "kind": run["kind"], "name": run.get("name"), "from": run.get("from"), "to": run.get("to"),
            "state": status.get("state"), "error": status.get("error"), "created_at": status.get("created_at"),
            "finished_at": status.get("finished_at"), "seconds": _seconds(status), "position": run.get("position"),
            "total_percent": headline.get("total_percent"), "trades": headline.get("trades"),
            "days": headline.get("days_tested"), "beaten": headline.get("beaten"), "profit_loss": headline.get("profit_loss"),
            # What rests on it: a nightly run is kept by the next for it, any run is spared a removal of them all.
            "nightly": nightly, "kept": self._kept(run, runs, references, verdicts),
            "derived": _derived_kind(run), "remade_by": run.get("remade_by"), "remade_from": run.get("remade_from"),
            "stale": run.get("stale"), "favourite": bool(run.get("favourite")), "made_by": run.get("made_by"),
            "experiment": run.get("experiment"), "tags": run.get("tags") or [],
        }

    def _nightly(self, runs: list[dict], entry: dict, moment: tuple[date, bool, date]) -> dict | None:
        """Where a configuration's nightly re-run stands: on or off, its last ask, and the days of the next."""
        nightly = entry.get("nightly") or {}
        template = _template(runs)
        if template is None and not nightly:
            return None
        day, market_open, today = moment
        most = self.most_days((template or {}).get("turbos"))
        anchor = _day(nightly.get("from")) or _day((template or {}).get("from")) or day
        last = entry.get("last")
        said_last = None
        if last:
            run = next((found for found in runs if found["id"] == last.get("run")), None) if last.get("run") else None
            status = (run or {}).get("status") or {}
            said_last = {
                "day": last.get("day"), "run": last.get("run"), "asked_at": last.get("asked_at"),
                "state": status.get("state"), "error": last.get("error") or status.get("error"),
                "seconds": _seconds(status), "by_hand": bool(last.get("by_hand")),
            }
        # Whether a run covers already what one asked now would ("Bring it up to date" then has nothing to do).
        up_to_date = _covers(runs, *nightly_window(anchor, day, most))
        # Open, the next run goes to the market's day now, once its hours are over.
        target = today if market_open else day
        first, to = nightly_window(anchor, target, most)
        covered = _covers(runs, first, to)
        if covered:
            target = _next_weekday(target)
            first, to = nightly_window(anchor, target, most)
        if said_last and said_last["state"] in (QUEUED, RUNNING) and self._backtests.is_active(said_last["run"] or ""):
            waits = WAITS_UNDER_WAY
        elif covered:
            waits = WAITS_MARKET
        elif said_last and said_last["day"] == to.isoformat() and (
                said_last["state"] in (FAILED, CANCELLED) or (said_last["error"] and not said_last["run"])):
            waits = WAITS_FAILED
        elif market_open:
            waits = WAITS_MARKET
        elif self._backtests.operator_waiting():
            waits = WAITS_LINE
        else:
            waits = None
        return {
            "on": bool(nightly.get("on")), "from": anchor.isoformat(), "since": nightly.get("since"),
            "telegram": bool(nightly.get("telegram")), "most_days": most, "last": said_last, "up_to_date": up_to_date,
            "next": {"day": target.isoformat(), "from": first.isoformat(), "to": to.isoformat(), "waits": waits},
        }

    def _described(self, configuration: str, runs: list[dict], index: dict, moment, references: set[str],
                   verdicts: set[str], *, every_run: bool) -> dict:
        entry = index.get(configuration) or {}
        latest = _latest(runs)
        shown = latest or next((run for run in runs if not _derived_kind(run)), runs[0])
        judging = [run for run in runs if latest is not None and _derived_kind(run)
                   and latest["id"] in (run.get(key) for key, _ in DERIVED)]
        lines = [self._line(run, runs, references, verdicts) for run in runs]
        return {
            "id": configuration,
            "strategy": self._backtests.identity(shown["id"])[1],
            "name": self._name(configuration, runs, entry), "named": bool(entry.get("name")),
            "kind": shown["kind"], "indice": shown.get("indice"), "turbos": shown.get("turbos"),
            "source": shown.get("source"), "chart": shown.get("chart"),
            "runs": lines if every_run else lines[:ROW_RUNS], "count": len(runs),
            "latest": latest, "judging": judging,
            "tags": list(dict.fromkeys(tag for run in runs for tag in run.get("tags") or [])),
            "favourite": any(run.get("favourite") for run in runs),
            # Who made its runs (an agent's name; ``mine``: the operator made one), and for which experiments.
            "made_by": sorted({run["made_by"] for run in runs if run.get("made_by")}),
            "mine": any(not run.get("made_by") for run in runs),
            "experiments": sorted({run["experiment"] for run in runs if run.get("experiment")}),
            # What removing it takes: its runs that neither wait, run nor are a reference Edge Watch judges against.
            "removable": [run["id"] for run in runs if not self._backtests.is_active(run["id"]) and run["id"] not in references],
            "updated_at": (runs[0].get("status") or {}).get("created_at"),
            "nightly": self._nightly(runs, entry, moment),
            "why_no_nightly": why_no_nightly(runs),
            "distinct": [],
        }

    def _settings_of(self, runs: list[dict]) -> dict:
        shown = _latest(runs) or runs[0]
        try:
            return self._backtests.settings_of(shown["id"])
        except UnknownRun:
            return {}

    def _distinct(self, groups: dict[str, list[dict]], described: dict[str, dict]) -> None:
        """Configurations of one strategy: each says the settings it differs from the others in, with its value."""
        by_strategy: dict[str, list[str]] = {}
        for configuration, found in described.items():
            by_strategy.setdefault(found["strategy"], []).append(configuration)
        for siblings in by_strategy.values():
            if len(siblings) < 2:
                continue
            settings = {configuration: self._settings_of(groups[configuration]) for configuration in siblings}
            for configuration in siblings:
                names: dict = {}
                for other in siblings:
                    if other != configuration:
                        for difference in differences(settings[configuration], settings[other]):
                            names.setdefault(difference["name"], difference["here"])
                described[configuration]["distinct"] = [{"name": name, "value": value} for name, value in names.items()]

    def _all(self, now: float, *, whole: str | None = None) -> tuple[dict[str, list[dict]], dict[str, dict], tuple]:
        groups = self._groups(self._runs())
        index = self._store.index()
        moment = self._moment(now)
        references, verdicts = set(self._backtests.references().values()), self._verdicts()
        described = {configuration: self._described(configuration, found, index, moment, references, verdicts,
                                                    every_run=configuration == whole)
                     for configuration, found in groups.items()}
        self._distinct(groups, described)
        return groups, described, moment

    def configurations(self, now: float | None = None) -> dict:
        """Every configuration, the newest run first, with its newest runs, its latest result and its nightly re-run."""
        _, described, (day, market_open, _) = self._all(self._clock() if now is None else now)
        items = sorted(described.values(), key=lambda found: found["runs"][0]["id"], reverse=True)
        return {"items": items, "day": day.isoformat(), "market_open": market_open, "most_days": self.most_days(None)}

    def page(self, configuration: str, now: float | None = None) -> dict:
        """One configuration with every run of it, what its nightly runs said, and those of its strategy."""
        checked_id(configuration)
        groups, described, (day, market_open, _) = self._all(self._clock() if now is None else now, whole=configuration)
        if configuration not in described:
            raise UnknownConfiguration(configuration)
        shown = described[configuration]
        mine = self._settings_of(groups[configuration])
        related = [
            {"id": other["id"], "name": other["name"], "count": other["count"], "latest": other["latest"],
             "differences": differences(mine, self._settings_of(groups[other["id"]]))}
            for other in described.values() if other["id"] != configuration and other["strategy"] == shown["strategy"]
        ]
        return {"configuration": shown, "history": self._store.history(configuration), "related": related,
                "day": day.isoformat(), "market_open": market_open}

    # ── what the operator changes ──

    def update(self, configuration: str, *, name=..., nightly: dict | None = None) -> dict:
        """
        A configuration named (``name``; None: its runs' name again), or its nightly re-run switched on or off
        (``nightly``: ``on``, ``from`` its first day, ``telegram``). Not audited: nothing here trades. From the routes'
        threads: ``wake`` it on the event loop afterwards.
        """
        checked_id(configuration)
        runs = self._groups(self._runs()).get(configuration)
        if not runs:
            raise UnknownConfiguration(configuration)
        template = _template(runs)
        if name is not ... and name is not None:
            if not isinstance(name, str) or not name.strip():
                raise InvalidRequest("A configuration's name is a text")
            name = " ".join(name.split())
            if len(name) > MAX_NAME_LENGTH:
                raise InvalidRequest(f"A configuration's name is {MAX_NAME_LENGTH} characters at most")
        if nightly is not None:
            if nightly.get("on") and template is None:
                raise NotPossible(why_no_nightly(runs))
            anchor = nightly.get("from")
            if anchor is not None and _day(anchor) is None:
                raise InvalidRequest("Its first day is a day, YYYY-MM-DD")
            if anchor is not None and _day(anchor) > self.day():
                raise InvalidRequest("Its first day is one whose trading hours are over")

        def change(entry: dict) -> None:
            entry.setdefault("seed_run", runs[-1]["id"])
            entry.setdefault("digest_version", DIGEST_VERSION)
            if name is not ...:
                if name:
                    entry["name"] = name
                else:
                    entry.pop("name", None)
            if nightly is not None:
                kept = dict(entry.get("nightly") or {})
                if nightly.get("on") and not kept.get("on"):
                    kept["since"] = utc(self._clock())
                if "on" in nightly:
                    kept["on"] = bool(nightly["on"])
                if nightly.get("from") is not None:
                    kept["from"] = nightly["from"]
                if template is not None and not kept.get("from"):
                    kept["from"] = template["from"]
                if "telegram" in nightly:
                    kept["telegram"] = bool(nightly["telegram"])
                entry["nightly"] = kept

        self._store.update(configuration, change)
        return self.page(configuration)

    # ── asking for a nightly run ──

    def _under_way(self, configuration: str) -> bool:
        last = self._store.entry(configuration).get("last") or {}
        return bool(last.get("run")) and self._backtests.is_active(last["run"])

    async def bring_up_to_date(self, configuration: str) -> dict:
        """
        A configuration's nightly run asked now ("Bring it to <day> now"), whether its nightly re-run is on or not, at
        any hour: the operator's own run, in line with the others (refused when the line is full).
        """
        checked_id(configuration)
        runs = self._groups(self._runs()).get(configuration)
        if not runs:
            raise UnknownConfiguration(configuration)
        template = _template(runs)
        if template is None:
            raise NotPossible(why_no_nightly(runs))
        if configuration in self._asking or self._under_way(configuration):
            raise NotPossible("Its nightly run is being made already")
        day = self.day()
        first, last = self._window(self._store.entry(configuration), template, day)
        if _covers(runs, first, last):
            raise NotPossible(f"A run of it covers {first.isoformat()} to {last.isoformat()} already")
        await self._ask(configuration, runs, template, day, by_hand=True)
        return self.page(configuration)

    async def _ask(self, configuration: str, runs: list[dict], template: dict, day: date, *, by_hand: bool) -> dict | None:
        """
        A configuration's nightly run asked, its signals read for the new days when they are the journal's or a rule's,
        and its ask kept (``last``). Asked by itself, one the backtests refuse (no entry signal, a period too long) is
        kept as such, not asked again for that day; by hand, the refusal is raised. A full line (LineFull) and the
        database (DatabaseUnavailable) are raised, nothing kept: asked again at the next look.
        """
        entry = self._store.entry(configuration)
        first, last = self._window(entry, template, day)
        name = self._name(configuration, runs, entry)[:MAX_NAME_LENGTH]
        request = self._backtests.request_of(template["id"])
        source = request.get("source") or {}
        self._asking.add(configuration)
        try:
            journal = positions = raised = None
            if source.get("kind") == JOURNAL:
                journal = await self._queries.journal_between(first - timedelta(days=1), last + timedelta(days=1))
                try:
                    positions = await self._queries.closed_positions(first - timedelta(days=1), last + timedelta(days=1))
                except DatabaseUnavailable:
                    positions = None
            elif source.get("kind") == TA_SIGNALS:
                rule = source.get("rule") or {}
                zone = TradingClock(trading_timezone(self._config())).zone
                first, raised = await newest_raised(self._queries, rule.get("id"), rule.get("name") or rule.get("id"),
                                                    first, last, zone, MAX_RAISED_SIGNALS)
            made = self._backtests.extend(template["id"], first, last, name=name, nightly={"configuration": configuration},
                                          journal=journal, positions=positions, raised=raised)
        except LineFull:
            raise
        except (InvalidRequest, NotPossible) as e:
            if by_hand:
                raise
            self._keep_last(configuration, day, None, by_hand, error=str(e))
            logger.info(f"The nightly backtest of {name!r} for {day.isoformat()} is not made: {e}")
            return None
        finally:
            self._asking.discard(configuration)
        self._keep_last(configuration, day, made["id"], by_hand)
        return made

    def _keep_last(self, configuration: str, day: date, run_id: str | None, by_hand: bool, *, error: str | None = None) -> None:
        last = {"day": day.isoformat(), "run": run_id, "asked_at": utc(self._clock()), "error": error,
                "settled": run_id is None, "by_hand": by_hand}

        def change(entry: dict) -> None:
            entry.setdefault("digest_version", DIGEST_VERSION)
            entry["last"] = last

        self._store.update(configuration, change)

    def _last_state(self, last: dict) -> str | None:
        """The state of a configuration's last nightly run: interrupted when the admin stopped it; None when gone."""
        try:
            return self._backtests.state(last["run"])["state"]
        except UnknownRun:
            return None

    def due(self, now: float | None = None) -> list[tuple[str, list[dict], dict]]:
        """
        The configurations whose nightly run is due: switched on, no finished run of theirs covering tonight's days,
        and not asked for that day already (one cut by a restart of the admin, or removed, is asked again). The
        oldest asked first.
        """
        day = self.day(now)
        groups = self._groups(self._runs())
        found = []
        for configuration, entry in self._store.index().items():
            if not (entry.get("nightly") or {}).get("on") or configuration not in groups or configuration in self._asking:
                continue
            runs = groups[configuration]
            template = _template(runs)
            if template is None:
                continue
            first, last = self._window(entry, template, day)
            if _covers(runs, first, last):
                continue
            asked = entry.get("last") or {}
            if asked.get("day") == day.isoformat():
                if asked.get("run") is None:
                    # Refused for that day: the next day's, or by hand.
                    continue
                if self._last_state(asked) in (QUEUED, RUNNING, FAILED, CANCELLED, DONE):
                    continue
            found.append((asked.get("asked_at") or "", configuration, runs, template))
        return [(configuration, runs, template) for _, configuration, runs, template in sorted(found, key=lambda item: item[0])]

    async def catch_up(self, now: float | None = None) -> int:
        """
        What ended settled; then, the market closed, no run of the operator waiting and no nightly run under way,
        one nightly run asked when one is due. How many were asked (0 or 1).
        """
        now = self._clock() if now is None else now
        self.settle()
        if market_is_open(self._config(), now) or self._asking or self._backtests.operator_waiting():
            return 0
        if any(self._under_way(configuration) for configuration in self._store.index()):
            return 0
        day = self.day(now)
        for configuration, runs, template in self.due(now):
            try:
                made = await self._ask(configuration, runs, template, day, by_hand=False)
            except LineFull as e:
                logger.info(f"The nightly backtests wait: {e}")
                return 0
            except DatabaseUnavailable:
                logger.info("The nightly backtests wait for the database")
                return 0
            if made is not None:
                return 1
        return 0

    # ── once a nightly run ended ──

    def _finished(self, run_id: str, state: str, request: dict) -> None:
        """Told by the service once a run ends, on the event loop: a nightly run is settled at once."""
        if isinstance(request.get(NIGHTLY), dict):
            self.settle()
            self.wake()

    def settle(self) -> int:
        """
        Each configuration's last nightly run that ended and was not settled yet: done, its line kept and the nightly
        runs before it let go (those kept aside); said on Telegram when asked. Looked at again each tick: idempotent.
        How many were settled. A configuration whose runs are all gone is forgotten.
        """
        settled = 0
        runs = None
        for configuration, entry in self._store.index().items():
            last = entry.get("last") or {}
            if not last.get("run") or last.get("settled"):
                continue
            state = self._last_state(last)
            if state in (QUEUED, RUNNING):
                continue
            if runs is None:
                runs = self._runs()
            self._settle(configuration, entry, last, state, runs)
            settled += 1
        if runs is None:
            runs = self._runs()
        self._forget_gone(runs)
        return settled

    def _forget_gone(self, runs: list[dict]) -> None:
        """
        What was kept of a configuration none of whose runs is left, its first run gone too: forgotten, so that a
        run of the same configuration made later does not bring its nightly re-run back by itself. Nothing while no
        run can be read at all.
        """
        if not runs:
            return
        known = set(self._groups(runs))
        gone = {configuration for configuration, entry in self._store.index().items()
                if configuration not in known and not self._backtests.exists(entry.get("seed_run"))}
        self._store.forget(gone)

    def _settle(self, configuration: str, entry: dict, last: dict, state: str | None, runs: list[dict]) -> None:
        mine = [run for run in runs if run.get("configuration") == configuration]
        run = next((found for found in mine if found["id"] == last["run"]), None)
        if state == DONE and run is not None:
            headline = run.get("headline") or {}
            status = run.get("status") or {}
            self._store.add_history(configuration, {
                "run": run["id"], "name": run.get("name"), "made_at": status.get("finished_at"),
                "from": run.get("from"), "to": run.get("to"), "days": headline.get("days_tested"),
                "trades": headline.get("trades"), "total_percent": headline.get("total_percent"),
                "profit_loss": headline.get("profit_loss"), "beaten": headline.get("beaten"),
                "worst_drawdown": headline.get("worst_drawdown"), "win_rate": headline.get("win_rate"),
                "seconds": _seconds(status), "version": self._backtests.made_with_version(run["id"]),
                "by_hand": bool(last.get("by_hand")),
            })
            references, verdicts = set(self._backtests.references().values()), self._verdicts()
            for older in mine:
                if older["id"] >= run["id"] or not older.get("nightly") or self._backtests.is_active(older["id"]):
                    continue
                if self._kept(older, runs, references, verdicts):
                    continue
                try:
                    self._backtests.delete(older["id"])
                except (UnknownRun, NotPossible):
                    pass
        self._store.update(configuration, lambda found: (found.get("last") or {}).update(settled=True))
        if (entry.get("nightly") or {}).get("telegram") and run is not None:
            said = said_line(self._name(configuration, mine, entry), run, state)
            if said:
                self._tell(said)

    def _tell(self, text: str) -> None:
        if self._notify is None:
            return
        try:
            task = asyncio.get_running_loop().create_task(self._said(text))
        except RuntimeError:
            return
        self._telling.add(task)
        task.add_done_callback(self._telling.discard)

    async def _said(self, text: str) -> None:
        try:
            await self._notify(text)
        except Exception as e:
            logger.warning(f"A nightly backtest's line could not be said on Telegram: {e}")

    # ── the task ──

    def wake(self) -> None:
        """Look again at once: a run ended, a configuration was switched on."""
        self._wakeup.set()

    async def keep(self) -> None:
        """Until cancelled: settle what ended and ask what is due, every few minutes or once woken."""
        while True:
            self._wakeup.clear()
            try:
                await self.catch_up()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"The nightly backtests could not catch up: {e}")
            try:
                await asyncio.wait_for(self._wakeup.wait(), TICK_SECONDS)
            except asyncio.TimeoutError:
                pass
