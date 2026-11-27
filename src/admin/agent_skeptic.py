"""
A kept conclusion read again by a skeptic (``admin.ai_agent.skeptic_review``, off unless set).

Once the market is closed by the live rules, each experiment of the journal concluded kept with a candidate run, and
not reviewed yet, gets one conversation of the agent about its candidate (the subject ``run:<candidate>`` of
src/admin/agent/service.py) with the skeptic's prompt (src/admin/agent/prompts.py ``skeptic_prompt``): try to break
the conclusion with what the run already holds. Its first request only reads (a job asked ``read_only``: the tool
session refuses every write but its verdict and a chart), starts no backtest and costs ``skeptic_max_usd`` at most.
Once that conversation is over, the journal keeps a ``reviewed`` event with the verdict it set on the run
(src/research/experiments.py ``record_review``), shown beside the experiment, and told to later research tasks by
their brief. A review changes neither the conclusion nor its forward check.

One review a look, the oldest conclusion first; the agent's pause, its key and the month's budget hold it back, and it
is asked again at the next look. What was asked is kept in ``skeptic.json`` beside the conversations. It lives
outside src/admin/agent/, as the go-live dossiers do.
"""

import asyncio
import logging
import os
import time

from starlette.concurrency import run_in_threadpool

from src.admin.agent.prompts import skeptic_prompt
from src.admin.agent.store import UnknownConversation, stamp
from src.admin.agent.transcript import UNDER_WAY
from src.admin.agent_jobs import JobFile, read_config
from src.admin.downloads import market_is_open
from src.admin.errors import InvalidRequest, NotPossible, UnknownRun
from src.research.experiments import JournalError

logger = logging.getLogger(__name__)

TICK_SECONDS = 15 * 60
READY_SECONDS = 10
FILE = "skeptic.json"
KEPT = 400
SKEPTIC = "skeptic"
MAX_TITLE = 80
# Reviews asked in a day (UTC) at most: each spends up to skeptic_max_usd of the month's budget.
MAX_REVIEWS_A_DAY = 3


class SkepticReviews:
    def __init__(self, *, agent, journal, verdicts, config_path: str, path: str, clock=time.time):
        self._agent = agent
        # The experiment journal (src/research/experiments.py) and the agent's verdicts (src/admin/agent/verdicts.py).
        self._journal = journal
        self._verdicts = verdicts
        self._config_path = config_path
        self._file = JobFile(path, "experiments", KEPT)
        self._clock = clock

    def asked(self) -> dict:
        return self._file.read()

    async def keep(self) -> None:
        """Until cancelled: a look every quarter of an hour, once the agent's tools started."""
        while True:
            if not self._agent.ready:
                await asyncio.sleep(READY_SECONDS)
                continue
            try:
                await self.catch_up()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"The AI agent's skeptic's reviews could not catch up: {e}")
            await asyncio.sleep(TICK_SECONDS)

    async def catch_up(self, now: float | None = None) -> str | None:
        """The reviews over written in the journal; then one kept conclusion's review asked, the market closed."""
        await run_in_threadpool(self.settle)
        settings = self._agent.settings
        if not settings.skeptic_review:
            return None
        now = self._clock() if now is None else now
        config = await run_in_threadpool(read_config, self._config_path)
        if self._market_is_open(config, now):
            return None
        asked = await run_in_threadpool(self.asked)
        today = stamp(now)[:10]
        if sum(1 for entry in asked.values() if entry.get("conversation") and str(entry.get("at") or "").startswith(today)) \
                >= MAX_REVIEWS_A_DAY:
            return None
        experiments = await run_in_threadpool(self._journal.experiments)
        for experiment in sorted(experiments, key=lambda found: found.get("concluded_at") or ""):
            if (experiment.get("verdict") != "kept" or not experiment.get("candidate") or experiment["id"] in asked
                    or experiment.get("review")):
                continue
            try:
                meta = await self._agent.create(
                    {"kind": "run", "id": experiment["candidate"]}, skeptic_prompt(experiment), SKEPTIC,
                    settings.skeptic_model, settings.skeptic_effort,
                    job={"kind": SKEPTIC, "experiment": experiment["id"], "read_only": True, "backtests": 0,
                         "max_usd": settings.skeptic_max_usd,
                         "title": f"Skeptic's review of {experiment.get('title') or experiment['id']}"[:MAX_TITLE]})
            except NotPossible as e:
                # Paused, without a key, the month spent: asked again at the next look.
                logger.info(f"The skeptic's review of experiment {experiment['id']} waits: {e}")
                return None
            except (InvalidRequest, UnknownRun) as e:
                # Its candidate is gone: never asked again.
                logger.info(f"The skeptic's review of experiment {experiment['id']} was not asked: {e!r}")
                await run_in_threadpool(self._file.put, experiment["id"], {
                    "at": stamp(now), "conversation": None, "candidate": experiment["candidate"],
                    "refused": str(e) if isinstance(e, InvalidRequest) else f"No backtest {experiment['candidate']}"})
                continue
            await run_in_threadpool(self._file.put, experiment["id"], {
                "at": stamp(now), "conversation": meta["id"], "candidate": experiment["candidate"], "said": None})
            # One a look: each is a conversation of its own.
            return meta["id"]
        return None

    def _market_is_open(self, config: dict, now: float) -> bool:
        return market_is_open(config, now)

    def settle(self) -> int:
        """
        Each review whose conversation is over: the verdict it set on its candidate written in the journal (none, when
        it set none), and written again once a later request of it sets one. How many were written.
        """
        written = 0
        for experiment_id, entry in self.asked().items():
            conversation = entry.get("conversation")
            if not conversation or (entry.get("said") or {}).get("tone"):
                continue
            try:
                meta = self._agent.conversation(conversation)
            except UnknownConversation:
                meta = None
            if meta is not None and meta.get("status") in UNDER_WAY:
                continue
            verdict = (self._verdicts.all() if self._verdicts is not None else {}).get(f"run:{entry['candidate']}")
            mine = verdict if verdict and verdict.get("conversation") == conversation else None
            if entry.get("said") is not None and mine is None:
                # Written already without a verdict, and none since.
                continue
            tone, line = (mine or {}).get("tone"), (mine or {}).get("line")
            try:
                self._journal.record_review(experiment_id, conversation=conversation, tone=tone, line=line)
            except JournalError as e:
                logger.warning(f"The skeptic's review of experiment {experiment_id} could not be written: {e}")
                continue
            self._file.put(experiment_id, {**entry, "said": {"tone": tone, "at": stamp(self._clock())}})
            written += 1
        return written


def skeptic_of(ctx) -> SkepticReviews:
    """The skeptic's reviews over the admin's context: the server's journal, the agent's verdicts."""
    from src.admin.agent.verdicts import VerdictStore
    from src.research.experiments import server_journal

    return SkepticReviews(agent=ctx.agent, journal=server_journal(ctx.settings.backtests_path),
                          verdicts=VerdictStore(ctx.settings.agent_path), config_path=ctx.settings.config_path,
                          path=os.path.join(ctx.settings.agent_path, FILE))
