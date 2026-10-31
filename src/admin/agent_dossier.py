"""
A signal rule's go-live dossier asked by the admin itself
(``admin.ai_agent.dossier_when_ready``, off unless set): once the market is
closed by the live rules, an operator's rule in shadow whose readiness is met
(src/admin/ta_readiness.py, the checks the Signal rules page and the go-live
route hold) gets one dossier for what it runs now: a conversation about the
rule (its subject of src/admin/agent/service.py) with the dossier's prompt,
within the agent's own limits, its verdict set on the rule's row and its first
lines said on Telegram by the agent's teller. A change of what the rule runs
(``run_changed_at``) is a new rule to judge: its readiness starts again, and so
does its dossier. Going live stays the operator's, through the rule's dialog.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

from src.admin.agent.prompts import DOSSIER_PROMPT
from src.admin.agent.store import stamp
from src.admin.agent_jobs import JobFile, read_config
from src.admin.agent_telegram import DOSSIER
from src.admin.downloads import market_is_open
from src.admin.errors import InvalidRequest, NotPossible
from src.admin.ta_readiness import unmet_readiness

logger = logging.getLogger(__name__)

TICK_SECONDS = 15 * 60
READY_SECONDS = 10
FILE = "dossiers.json"
KEPT = 200


def judged_as(rule: dict) -> str:
    """What a dossier is about: the rule as it runs now (a change of what it runs makes another)."""
    return f"{rule['id']}:{rule.get('run_changed_at') or rule.get('mode_changed_at') or ''}"


class RuleDossiers:
    def __init__(self, *, agent, config_path: str, path: str, rules, states, paper, timezone_name: str, teller=None,
                 clock=time.time):
        self._agent = agent
        self._config_path = config_path
        self._file = JobFile(path, "rules", KEPT)
        # () -> the signal rules; async () -> the engine's state of each, by id; () -> each rule's paper days, by id.
        self._rules = rules
        self._states = states
        self._paper = paper
        self._timezone = timezone_name
        self._teller = teller
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
                logger.warning(f"The AI agent's go-live dossiers could not catch up: {e}")
            await asyncio.sleep(TICK_SECONDS)

    async def catch_up(self, now: float | None = None) -> str | None:
        """One ready rule's dossier asked, the market closed; the conversation asked, or None."""
        settings = self._agent.settings
        if not settings.dossier_when_ready:
            return None
        now = self._clock() if now is None else now
        config = await run_in_threadpool(read_config, self._config_path)
        if market_is_open(config, now):
            return None
        rules = await run_in_threadpool(self._rules)
        states = await self._states()
        paper = await run_in_threadpool(self._paper)
        asked = await run_in_threadpool(self.asked)
        moment = datetime.fromtimestamp(now, timezone.utc)
        for rule in rules:
            if rule.get("mode") != "shadow" or rule.get("made_by") == "claude" or judged_as(rule) in asked:
                continue
            if unmet_readiness(rule, states.get(rule["id"]), paper.get(rule["id"]), moment, self._timezone):
                continue
            try:
                meta = await self._agent.create(
                    {"kind": "rule", "id": rule["id"]}, DOSSIER_PROMPT, "dossier",
                    job={"kind": "dossier", "rule": rule["id"], "max_usd": settings.max_request_usd,
                         "backtests": settings.max_backtests, "title": f"Go-live dossier of {rule['name']}"})
            except NotPossible as e:
                # Paused, without a key, the month spent: asked again at the next look.
                logger.info(f"The go-live dossier of {rule['name']} waits: {e}")
                return None
            except InvalidRequest as e:
                logger.info(f"The go-live dossier of {rule['name']} was not asked: {e}")
                continue
            await run_in_threadpool(self._file.put, judged_as(rule), {"at": stamp(now), "conversation": meta["id"]})
            if self._teller is not None:
                await run_in_threadpool(self._teller.add, meta["id"], DOSSIER, request=0, label=rule["name"])
            # One a look: each is a study of its own.
            return meta["id"]
        return None


def dossiers_of(ctx, teller) -> RuleDossiers:
    """The dossiers over the admin's context: its rules, the engine's states, the paper days."""
    from src.admin.ta_rules import RuleStore

    def rules() -> list[dict]:
        return RuleStore(ctx.ta_scripts).load()["rules"] if ctx.ta_scripts is not None else []

    async def states() -> dict:
        return await ctx.queries.ta_rule_states()

    def paper() -> dict:
        return (ctx.backtests.paper(with_reports=False).get("rules") or {}) if ctx.backtests is not None else {}

    return RuleDossiers(agent=ctx.agent, config_path=ctx.settings.config_path,
                        path=os.path.join(ctx.settings.agent_path, FILE), rules=rules, states=states, paper=paper,
                        timezone_name=ctx.settings.timezone, teller=teller)
