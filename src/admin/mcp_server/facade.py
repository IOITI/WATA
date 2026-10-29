"""
What the MCP tools may use, and nothing else.

``ResearchServices`` is built from the admin's context by name, attribute by
attribute: the services that trade or speak for the operator (``services``,
``saxo``, ``config_editor``, ``risk_state``, ``notifier``, ``audit_log``,
``live_prices``, the credentials and sessions) are never handed over, so no
tool can reach them, whatever it is asked. The database is reached through
``ReadQueries``, a list of named queries, never the pool or a statement; the
configuration is read for a few facts only (``ConfigFacts``).
"""

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable

import pytz

from src.admin.downloads import trading_hours
from src.admin.mcp_server.jobs import Jobs
from src.admin.mcp_server.limits import McpLimits
from src.admin.ta_rules import RuleStore
from src.backtest.clock import TradingClock
from src.research.experiments import ExperimentJournal

# Who Claude's work is written as, whichever of its tokens asked: a token replaced keeps what it made.
CLAUDE = "claude"
RESEARCH_FOLDER = "_research"


def journal_of(backtests_path: str) -> ExperimentJournal:
    """The experiment journal and the lock box Claude keeps on the server, beside the backtests."""
    root = os.path.join(backtests_path, RESEARCH_FOLDER)
    return ExperimentJournal(os.path.join(root, "experiments.jsonl"), os.path.join(root, "lockbox.json"))


class ReadQueries:
    """The queries the tools may run, by name: never the pool, a statement, or anything that writes."""

    ALLOWED = frozenset({
        "backtest_turbo_days", "backtest_recorder_days", "backtest_asset_days", "capital_instrument", "capital_assets",
        "capital_feed_state", "price_assets", "price_instrument", "price_bars", "capital_bars", "journal_between",
        "closed_positions", "last_balance", "execution_rows", "ta_signals", "ta_other_signals", "ta_rule_states",
        "ta_engine_state",
        # Saxo's order requests and one position whole, never the requests' bodies (src/admin/queries.py).
        "order_attempts", "position_record",
    })

    def __init__(self, queries):
        self._queries = queries

    def __getattr__(self, name):
        if name not in self.ALLOWED:
            raise AttributeError(f"{name} is not a query the research tools may run")
        return getattr(self._queries, name)


class ConfigFacts:
    """The few things of config.json the tools need, read again at each question (the operator may edit it)."""

    def __init__(self, config_path: str):
        self._config_path = config_path

    def _config(self) -> dict:
        with open(self._config_path, encoding="utf-8") as config_file:
            return json.load(config_file)

    def market(self, now: float) -> dict:
        """Whether WATA may be trading now by its live rules (``market_hours`` on a weekday); unknown counts as open."""
        try:
            zone, start_hour, end_hour = trading_hours(self._config())
            local = TradingClock(zone).local(now)
        except (OSError, ValueError, KeyError, TypeError, pytz.UnknownTimeZoneError):
            return {"open": True, "detail": "The trading hours could not be read: taken as open"}
        is_open = local.weekday() < 5 and start_hour <= local.hour < end_hour
        return {
            "open": is_open,
            "timezone": zone,
            "local_time": local.isoformat(timespec="seconds"),
            "trading_hours": f"{start_hour:02d}:00-{end_hour:02d}:00 on weekdays",
        }

    def allowed_indices(self) -> list[str]:
        try:
            rules = (self._config().get("trade") or {}).get("rules") or []
        except (OSError, ValueError):
            return []
        for rule in rules:
            if isinstance(rule, dict) and rule.get("rule_type") == "allowed_indices":
                return sorted(((rule.get("rule_config") or {}).get("indice_ids") or {}).keys())
        return []


@dataclass
class ResearchServices:
    version: str
    timezone: str
    limits: McpLimits
    calls: Any
    tokens: Any
    config: ConfigFacts
    journal: ExperimentJournal
    backtests_path: str = ""
    # Backtests asked for and their results (src/admin/backtests.py).
    backtests: Any = None
    # The Pine scripts (src/admin/ta_scripts.py) and the signal rules (src/admin/ta_rules.py, read here).
    scripts: Any = None
    rules: Any = None
    # The database, by named queries only.
    queries: ReadQueries | None = None
    # Dukascopy's history, as files (src/admin/data.py).
    history: Any = None
    # Whether each finished day is written for a research machine (read only).
    research_export: Any = None
    # The admin's live events: a backtest's end is waited for there.
    events: Any = None
    # Pine studies, child processes one at a time (src/admin/studies.py).
    studies: Any = None
    # The AI agent's memory (src/admin/agent_memory.py): read here, never changed.
    memory: Any = None
    clock: Callable[[], float] = field(default=time.time)
    # A request's idempotency key and the run it made: a request sent again is not queued twice.
    asked: dict = field(default_factory=dict)
    # The studies that outlasted their call, until job_wait gives their answers.
    jobs: Jobs = field(default_factory=Jobs)

    @classmethod
    def from_context(cls, ctx) -> "ResearchServices":
        return cls(
            version=ctx.version,
            timezone=ctx.settings.timezone,
            limits=ctx.settings.mcp_limits,
            calls=ctx.mcp_calls,
            tokens=ctx.mcp_tokens,
            config=ConfigFacts(ctx.settings.config_path),
            journal=journal_of(ctx.settings.backtests_path),
            backtests_path=ctx.settings.backtests_path,
            # Switched off in the configuration, backtests are off for Claude too.
            backtests=ctx.backtests if ctx.settings.backtests_enabled else None,
            scripts=ctx.ta_scripts,
            rules=RuleStore(ctx.ta_scripts) if ctx.ta_scripts is not None else None,
            queries=ReadQueries(ctx.queries) if ctx.queries is not None else None,
            history=ctx.history,
            research_export=ctx.research,
            events=ctx.events,
            studies=ctx.studies,
            memory=getattr(ctx, "agent_memory", None),
        )

    def service(self):
        """The backtests, or a refusal when the operator switched them off."""
        if self.backtests is None:
            from src.admin.mcp_server.budget import Refused

            raise Refused("Backtests are switched off in the configuration (admin.backtests_enabled)")
        return self.backtests

    def now_iso(self) -> str:
        return datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def today(self) -> date:
        return TradingClock(self.timezone).day_of(self.clock())

    def free_disk_gb(self) -> float | None:
        try:
            return shutil.disk_usage(self.backtests_path or ".").free / 1024 ** 3
        except OSError:
            return None
