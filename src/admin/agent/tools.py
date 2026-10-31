"""
The agent's tools: an allowlist of the MCP endpoint's research tools, called in
process through the SDK's own client (as tests/test_mcp_tools_*.py call them),
so with the same guards, schemas, answers and refusals; and the agent's own,
which propose and write nothing else (src/admin/agent/proposals.py).

What differs from Claude Code's use of them:
- a study that outlasts its call hands back a job, waited for here, and
  ``backtest_wait`` waits until the runs end (WAIT_SECONDS a call), so that no
  call of the API is spent on waiting;
- the export tools answer JSON, never CSV (``format`` is not offered), and an
  answer over ANSWER_CHARS is refused as one asking for less; a script's
  source passes whole (``pine_get``), its list of versions cut;
- the caller is ``agent:<conversation>`` (``made_via`` of its runs), with a
  call log of its own: System › Claude keeps Claude Code's calls.

The agent also reads, through what the admin gives it (``OpsReads``), the
admin's health and its services' log lines, cleaned of secrets as the Logs page
shows them: never a service itself.

With its memory (src/admin/agent_memory.py, ``admin.ai_agent.memory``), it reads
the entries through the MCP endpoint's own tools, as Claude Code does, and
saves and forgets them with two of its own, as ``agent:<conversation>``.

The MCP SDK is imported in the functions that use it: with the agent off, the
admin never loads it.
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import jsonschema
from starlette.concurrency import run_in_threadpool

from src.admin.agent import charts as agent_charts
from src.admin.agent import verdicts as agent_verdicts
from src.admin.agent.proposals import ProposalRefused
from src.admin.agent.store import stamp
from src.admin.agent_memory import FORGOTTEN, KINDS, MAX_BODY, MAX_DESCRIPTION, NAME, Author, UnknownEntry
from src.admin.errors import InvalidRequest, NotPossible, UnknownRun
from src.admin.ta_rules import RuleNotFound
from src.admin.logs import LEVELS, LOG_SERVICES, MAX_LINES
from src.admin.mcp_server.gate import CALLER
from src.admin.mcp_server.tokens import Caller

# The research tools the agent may call, in the order it is shown them. Never the tools that remove scripts or runs
# (pine_delete, backtest_remove), set the lock box, measure the turbo model again, write down trials made elsewhere
# or export a week of bars; job_wait is waited for here. Claude's own shadow rules are kept as the MCP endpoint keeps
# them (rule_shadow_*): gated by admin.mcp.shadow_rules, never live, the endpoint's only change of a mode. Its memory
# is read as Claude Code reads it (memory_*), when it has its memory.
MEMORY_READS = ("memory_list", "memory_read")
AGENT_TOOLS = (
    "status", "data_assets", "data_coverage", "data_signals", "data_trades", "data_execution", "data_position",
    "data_orders",
    "pine_list", "pine_get", "pine_check", "pine_run", "pine_signals", "pine_compare", "pine_save",
    "backtest_options", "backtest_list", "backtest_result", "backtest_trades", "backtest_days", "backtest_signals",
    "backtest_compare", "backtest_start", "backtest_derive", "backtest_wait",
    "turbo_model", "rule_list", "rule_compare", "rule_shadow_save", "rule_shadow_remove", "rule_set_reference",
    "exp_new", "exp_get", "exp_conclude", *MEMORY_READS,
)
PROPOSE_SCRIPT = "propose_script_change"
PROPOSE_RULE = "propose_rule_change"
PROPOSE_SETTINGS = "propose_settings_change"
PROPOSE_NEW_SCRIPT = "propose_new_script"
PROPOSE_NEW_RULE = "propose_new_rule"
# An action on live trading, offered while admin.ai_agent.action_proposals: applied only by the operator's click.
PROPOSE_ACTION = "propose_action"
# The end of a research task, said by the agent (src/admin/agent/service.py chains a task's requests until it).
TASK_DONE = "task_done"
# A question a research task asks the operator: it waits for their answer before its next request.
ASK_OPERATOR = "ask_operator"
MAX_CHOICES = 5
PROPOSALS = (PROPOSE_SCRIPT, PROPOSE_RULE, PROPOSE_SETTINGS, PROPOSE_NEW_SCRIPT, PROPOSE_NEW_RULE, PROPOSE_ACTION)
OWN_TOOLS = (*PROPOSALS, TASK_DONE, ASK_OPERATOR)
# The own tools every agent is given: an action only while the operator lets it propose them.
GIVEN_OWN_TOOLS = tuple(name for name in OWN_TOOLS if name != PROPOSE_ACTION)
# What it shows on the pages: a chart under its answer, its verdict on the thing it concluded about.
SHOW_CHART = "show_chart"
SET_VERDICT = "set_verdict"
# Its memory's changes, its own: the MCP endpoint only reads the memory.
MEMORY_SAVE = "memory_save"
MEMORY_FORGET = "memory_forget"
MEMORY_WRITES = (MEMORY_SAVE, MEMORY_FORGET)
# The Pine studies run one at a time on the server, whoever asks: never two of them at once. A shadow rule saved is
# run on its history first.
STUDIES = frozenset({"pine_check", "pine_run", "pine_signals", "pine_compare", "pine_save", "rule_shadow_save"})
# Each starts runs: counted in the request's max_backtests.
STARTS = frozenset({"backtest_start", "backtest_derive"})
# What an answer may put into the model's context, at most (the MCP endpoint's own bound), and the longer ones.
ANSWER_CHARS = 32_000
LONG_ANSWER_CHARS = {"pine_get": 300_000}
SHOWN_VERSIONS = 20
# How long one backtest_wait waits for its runs, and each look at them.
WAIT_SECONDS = 90 * 60
WAIT_STEP_SECONDS = 40
# Arguments of the tools as Claude Code calls them, never offered to the agent.
DROPPED_ARGUMENTS = {
    "data_signals": ("format",), "data_trades": ("format",), "backtest_trades": ("format",),
    "backtest_days": ("format",), "backtest_signals": ("format",), "backtest_wait": ("seconds",),
}
DESCRIPTIONS = {
    "pine_get": "One script: its source (the current version, or ``version``), its versions (when, who saved them: "
                f"the last {SHOWN_VERSIONS}), the libraries it imports and the rules that run it.",
    "backtest_wait": "Wait until none of the runs is queued or running, up to 90 minutes (call it again if some still "
                     "are), then each one's state, place in line and progress. Your runs wait behind the operator's; "
                     "nothing is spent while you wait.",
    "memory_list": "Your memory as it stands now: each entry's name, kind, line, who wrote it and whether the operator "
                   "locked it, and whether the operator paused its changes. The list your conversation began with may "
                   "be older.",
    "memory_read": "Entries of your memory whole, ten at most: their text, who wrote and changed them, from which "
                   "conversation, and their revision, which memory_save and memory_forget need to change one.",
}

OWN_DEFINITIONS = (
    {
        "name": PROPOSE_SCRIPT,
        "description": (
            "Propose a new version of one of the operator's scripts (an indicator or a strategy, never a library), "
            "made from one of your own scripts saved with pine_save (it compiled). The operator sees the change beside "
            "their version and applies it, or not. Signal rules keep running the version they pin until changed."),
        "input_schema": {
            "type": "object",
            "properties": {
                "script_id": {"type": "string", "description": "The operator's script to change."},
                "base_version": {"type": "integer", "description": "Its version your change was made on: its current one."},
                "from_script_id": {"type": "string", "description": "Your script that holds the change (pine_save's id)."},
                "from_version": {"type": "integer", "description": "The version of yours to propose."},
                "title": {"type": "string", "description": "The change in a few words."},
                "rationale": {"type": "string", "description": "Why, and what it rests on: the runs that showed it."},
            },
            "required": ["script_id", "base_version", "from_script_id", "from_version", "title", "rationale"],
            "additionalProperties": False,
        },
    },
    {
        "name": PROPOSE_RULE,
        "description": (
            "Propose a change to one of the operator's signal rules that is not live (in shadow or off): the version of "
            "its own script it runs, values of its inputs, its days of history. The operator applies it, or not; its "
            "week in shadow then starts again. A live rule is the operator's alone: say what you would change instead."),
        "input_schema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string", "description": "The rule (rule_list)."},
                "script_version": {"type": "integer", "description": "A saved version of the rule's own script."},
                "after_proposal": {"type": "string", "description": (
                    "Or: your proposal of a new version of the rule's script (its id), applied first.")},
                "inputs": {
                    "type": "array",
                    "description": "Inputs to set, by their id in the script (in_0, in_1…).",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "value": {"type": ["number", "string", "boolean"]}},
                        "required": ["id", "value"],
                        "additionalProperties": False,
                    },
                },
                "history_days": {"type": ["integer", "null"], "minimum": 0, "maximum": 30, "description": (
                    "Its own days of bars before each engine day; null: the engine's.")},
                "title": {"type": "string", "description": "The change in a few words."},
                "rationale": {"type": "string", "description": "Why, and what it rests on: the runs that showed it."},
            },
            "required": ["rule_id", "title", "rationale"],
            "additionalProperties": False,
        },
    },
    {
        "name": PROPOSE_SETTINGS,
        "description": (
            "Propose new values for WATA's trading settings in config.json: the settings backtest_options lists whose "
            "live value comes from it (entries, the day, exits, the turbo, sizing), resting on finished runs that "
            "tested them (backtest_start with these changes). The operator previews the change as the Configuration "
            "page does (what it reaches, the services it restarts) and saves it, or not; it applies at those "
            "services' next start. Whether the window around US releases is on and its events, the timezone of the "
            "hours and the TA engine's days are theirs alone: say what you would set them to instead."),
        "input_schema": {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 12,
                    "items": {
                        "type": "object",
                        "properties": {
                            "setting": {"type": "string", "description": "Its name in backtest_options."},
                            "value": {"type": ["number", "boolean", "string"], "description": (
                                "The value proposed, in the setting's unit (a time as HH:MM).")},
                        },
                        "required": ["setting", "value"],
                        "additionalProperties": False,
                    },
                },
                "runs": {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "string"}, "description": (
                    "The finished runs it rests on: those made with the change, and the run it is set beside.")},
                "title": {"type": "string", "description": "The change in a few words."},
                "rationale": {"type": "string", "description": (
                    "Why, and what it rests on: the figures of the runs, on how many trades and days.")},
            },
            "required": ["changes", "runs", "title", "rationale"],
            "additionalProperties": False,
        },
    },
    {
        "name": PROPOSE_NEW_SCRIPT,
        "description": (
            "Propose a new script of the operator's (an indicator or a strategy) from one of your own scripts saved "
            "with pine_save (it compiled). The operator reads it and creates it, or not: theirs from then on, its "
            "first version marked as yours, which a signal rule can then run."),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_script_id": {"type": "string", "description": "Your script (pine_save's id)."},
                "from_version": {"type": "integer", "description": "The version of yours to propose."},
                "name": {"type": "string", "maxLength": 80, "description": "Its name: one no script has."},
                "description": {"type": "string", "maxLength": 500, "description": "What it does, in a sentence or two."},
                "title": {"type": "string", "description": "The proposal in a few words."},
                "rationale": {"type": "string", "description": "Why, and what it rests on: the runs that showed it."},
            },
            "required": ["from_script_id", "from_version", "name", "title", "rationale"],
            "additionalProperties": False,
        },
    },
    {
        "name": PROPOSE_NEW_RULE,
        "description": (
            "Propose a new signal rule of the operator's, in shadow (its signals journaled, never sent) or off: one of "
            "their scripts (script_id, script_version: its current one by default), or the new script you proposed "
            "before it in this conversation (after_proposal), on the bars the Capital.com feed records, resting on "
            "the finished runs made from it. The operator creates it, or not; its week in shadow starts then. Never "
            "live: going live is theirs, once it is ready. Your own shadow rules are rule_shadow_save's."),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxLength": 80, "description": "Its name: one no rule has."},
                "script_id": {"type": "string", "description": "The operator's script it runs."},
                "script_version": {"type": "integer", "description": "Its version; the current one by default."},
                "after_proposal": {"type": "string", "description": (
                    "Or: your proposal of a new script in this conversation, which it runs once created.")},
                "asset": {"type": "string", "description": "capital/<EPIC>, as data_assets names it."},
                "indice": {"type": "string", "description": "The indice its signals are for (us100...)."},
                "bars": {"type": "string", "description": "10S to a day (10S, 45S, 1, 5, 60) or R<ticks>."},
                "side": {"type": "string", "enum": ["bid", "ask"]},
                "inputs": {"type": "object", "description": "By id: {\"in_0\": 9}."},
                "alerts": {"type": "object", "description": (
                    "By title: {\"Long\": \"long\", \"Short\": \"short\", \"Exit\": \"close-position\"}, or ignore, "
                    "message.")},
                "alert_calls": {"type": "string", "enum": ["ignore", "message"]},
                "history_days": {"type": ["integer", "null"], "minimum": 0, "maximum": 30},
                "mode": {"type": "string", "enum": ["shadow", "off"]},
                "runs": {"type": "array", "maxItems": 6, "items": {"type": "string"}, "description": (
                    "The finished runs made from it that it rests on.")},
                "title": {"type": "string", "description": "The proposal in a few words."},
                "rationale": {"type": "string", "description": "Why, and what it rests on: the runs that showed it."},
            },
            "required": ["name", "asset", "indice", "alerts", "title", "rationale"],
            "additionalProperties": False,
        },
    },
    {
        "name": TASK_DONE,
        "description": (
            "End the research task you were left to work on alone, once its goal is reached or cannot be with what "
            "is left: your conclusion, the runs worth keeping, what to do next. Then answer with the conclusion: no "
            "request follows. Only in a research task, never in a conversation with the operator."),
        "input_schema": {
            "type": "object",
            "properties": {
                "conclusion": {"type": "string", "maxLength": 4000, "description": (
                    "What the task found, and on how many trades and days it rests.")},
                "runs_kept": {"type": "array", "maxItems": 20, "items": {"type": "string"}, "description": (
                    "The runs that show it (their ids).")},
                "next_steps": {"type": "string", "maxLength": 2000, "description": "What to try next, if anything."},
            },
            "required": ["conclusion"],
            "additionalProperties": False,
        },
    },
    {
        "name": ASK_OPERATOR,
        "description": (
            "Ask the operator a question in a research task you work on alone, when their answer would change what "
            "you do next (which of two directions is worth your budget, whether a result is what they meant): they are "
            "told on Telegram and on the admin's pages, and the task waits for their answer, its hours not counting "
            "meanwhile, two days at most. End your answer after it: the next request starts with what they said. One "
            "question a request, and only in a research task: in a conversation, ask in your answer."),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "maxLength": 1000, "description": "The question, in a sentence or two."},
                "choices": {"type": "array", "maxItems": MAX_CHOICES, "items": {"type": "string", "maxLength": 120},
                            "description": "Short answers to choose from, if any: the operator may also write their own."},
                "why": {"type": "string", "maxLength": 1000, "description": "Why it matters: what each answer leads to."},
            },
            "required": ["question", "why"],
            "additionalProperties": False,
        },
    },
)


ACTION_DEFINITION = {
    "name": PROPOSE_ACTION,
    "description": (
        "Propose an action on live trading, which the operator applies, or not, from a card under your answer, "
        "through the page's own confirmation, checks and audit: pause_entries or resume_entries (new entries, as the "
        "dashboard pauses them), close_position (position_id: one open now), restart (services, among those System "
        "restarts: the admin last, each once the one before works again, held back as System holds it), rule_live "
        "(rule_id: one of the operator's rules in shadow; its readiness is checked again) or rule_out_of_live (a live "
        "rule). Only when it would help, one at a time, with why in its rationale: what you read that calls for it, "
        "and what it costs. Not applied after 30 minutes: the situation may have changed."),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["pause_entries", "resume_entries", "close_position", "restart",
                                                  "rule_live", "rule_out_of_live"]},
            "position_id": {"type": "string", "maxLength": 64, "description": "close_position's."},
            "services": {"type": "array", "maxItems": 10, "items": {"type": "string"}, "description": (
                "restart's: trader, position_monitor, web_server, scheduler, telegram, watchlist_manager, "
                "price_recorder, capital_feed, ta_engine, admin.")},
            "rule_id": {"type": "string", "maxLength": 64, "description": "rule_live's and rule_out_of_live's."},
            "title": {"type": "string", "description": "The action in a few words."},
            "rationale": {"type": "string", "description": "Why now: what you read that calls for it."},
        },
        "required": ["action", "title", "rationale"],
        "additionalProperties": False,
    },
}
CHART_DEFINITION = {
    "name": SHOW_CHART,
    "description": (
        "Draw a chart under your answer from figures your tools gave you in this conversation, never figures of your "
        "own: a line over time (points: {t: YYYY-MM-DD or an ISO moment, <series key>: value or null}, 2 to "
        f"{agent_charts.MAX_POINTS:,}) or bars by category (categories: {{label, <series key>: value}}, up to "
        f"{agent_charts.MAX_CATEGORIES}), {agent_charts.MAX_SERIES} series at most; a line's references are drawn "
        "across it (a limit, a range's ends). Bars of one series may be coloured by their sign (tone sign: daily "
        "results). source "
        "names the tool calls and runs the figures come from: it is shown under the chart. "
        f"{agent_charts.MAX_CHARTS} charts a request at most. Say in your answer what the chart shows, without "
        "listing its figures again."),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 120},
            "kind": {"type": "string", "enum": [agent_charts.LINE, agent_charts.BAR]},
            "unit": {"type": "string", "maxLength": 20, "description": "The values' unit: %, €, points, s..."},
            "series": {
                "type": "array", "minItems": 1, "maxItems": agent_charts.MAX_SERIES,
                "items": {
                    "type": "object",
                    "properties": {"key": {"type": "string", "pattern": agent_charts.KEY.pattern},
                                   "label": {"type": "string", "minLength": 1, "maxLength": 60}},
                    "required": ["key", "label"],
                    "additionalProperties": False,
                },
            },
            "points": {"type": "array", "maxItems": agent_charts.MAX_POINTS, "items": {"type": "object"},
                       "description": "A line's points: {t, <series key>: value}, a value null for a gap."},
            "categories": {"type": "array", "maxItems": agent_charts.MAX_CATEGORIES, "items": {"type": "object"},
                           "description": "Bars' categories: {label, <series key>: value}."},
            "references": {
                "type": "array", "maxItems": agent_charts.MAX_REFERENCES,
                "items": {"type": "object", "properties": {"value": {"type": "number"},
                                                           "label": {"type": "string", "maxLength": 40}},
                          "required": ["value"], "additionalProperties": False},
            },
            "tone": {"type": "string", "enum": ["series", "sign"], "description": "Bars only."},
            "zero": {"type": "boolean", "description": "A line only: zero kept on the axis (a result, a balance)."},
            "source": {"type": "string", "minLength": 1, "maxLength": 300},
        },
        "required": ["title", "kind", "series", "source"],
        "additionalProperties": False,
    },
}
VERDICT_DEFINITION = {
    "name": SET_VERDICT,
    "description": (
        "Say your conclusion on a backtest run, a signal rule, a trading day or a position in one line: the admin "
        "shows it on that thing's page beside a link to this conversation, until a later verdict replaces it. tone: "
        "positive (it holds, ready to go live, a day as it should be), mixed (in part, not yet), negative (it does not "
        "hold, not ready, something went wrong), neutral (nothing to judge). Set it once you concluded, on what you "
        "concluded about, and say it in your answer too."),
    "input_schema": {
        "type": "object",
        "properties": {
            "about": {"type": "string", "pattern": agent_verdicts.ABOUT.pattern, "description": (
                "run:<run id>, rule:<rule id>, day:<YYYY-MM-DD>, or position:<position id>.")},
            "tone": {"type": "string", "enum": list(agent_verdicts.TONES)},
            "line": {"type": "string", "minLength": 1, "maxLength": agent_verdicts.MAX_LINE},
        },
        "required": ["about", "tone", "line"],
        "additionalProperties": False,
    },
}


_NAME_SCHEMA = {"type": "string", "pattern": NAME.pattern, "description": (
    "2 to 60 lowercase letters, digits and dashes, as your memory's list names it.")}
MEMORY_DEFINITIONS = (
    {
        "name": MEMORY_SAVE,
        "description": (
            "Save an entry of your memory, kept for later conversations and shown to the operator on the Memory page: "
            "a new one, or a change to one you read (with the revision memory_read gave). One thing an entry, its "
            "description the line your memory's list shows. Refused for an entry the operator locked, while they "
            "paused its changes, or with what looks like a secret."),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {**_NAME_SCHEMA, "description": "A new name, or the entry to change: 2 to 60 lowercase "
                                                        "letters, digits and dashes."},
                "kind": {"type": "string", "enum": list(KINDS), "description": (
                    "preference: what the operator wants or refuses; lesson: how WATA, its data and its tools "
                    "behave; finding: what a study concluded, with the runs, trades and days it rests on; "
                    "reference: where something worth going back to is.")},
                "description": {"type": "string", "maxLength": MAX_DESCRIPTION, "description": (
                    "One line: what it is and when it matters.")},
                "body": {"type": "string", "maxLength": MAX_BODY, "description": (
                    "Markdown: the thing itself and what it rests on (runs, days, the operator's words), dated when "
                    "it can change.")},
                "revision": {"type": "integer", "description": "To change an entry: the revision memory_read gave."},
            },
            "required": ["name", "kind", "description", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": MEMORY_FORGET,
        "description": (
            "Forget an entry of your memory that proved wrong or no longer matters: the operator can bring it back. "
            "Refused for an entry the operator locked, or while they paused its changes."),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": _NAME_SCHEMA,
                "revision": {"type": "integer", "description": "The revision memory_read gave."},
                "why": {"type": "string", "maxLength": 500, "description": (
                    "Why it goes: the operator reads it in the memory's history.")},
            },
            "required": ["name", "revision", "why"],
            "additionalProperties": False,
        },
    },
)


# The admin's own state, read for the agent (src/admin/agent_ops.py): its health, a service's log lines around a
# moment, trading now as the dashboard reads it, what changed, the TA engine's last minutes, what waits to deploy.
OPS_HEALTH = "ops_health"
OPS_LOGS = "ops_logs"
OPS_TRADING = "ops_trading_now"
OPS_CHANGES = "ops_changes"
OPS_ENGINE = "ops_engine"
OPS_RELEASE = "ops_release"
OPS_TOOLS = (OPS_HEALTH, OPS_LOGS, OPS_TRADING, OPS_CHANGES, OPS_ENGINE, OPS_RELEASE)
# A look at a log: the lines of its window, the newest kept, at most.
LOG_LINES = 300
LOG_MINUTES = 60
_LOG_MOMENT = "%Y-%m-%d %H:%M"
# A look at what changed: the hours before its end, by default and at most.
CHANGES_HOURS = 24
CHANGES_MAX_HOURS = 168

OPS_DEFINITIONS = {
    OPS_HEALTH: {
        "name": OPS_HEALTH,
        "description": (
            "The admin's health now, as System shows it: each service (the trader, the position monitor, the web "
            "server, the feeds, the TA engine, the database, the queue, the backups, Saxo's login) with its status "
            "(ok, warning, down, unknown) and what it says, and the queues. Reads only."),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    OPS_LOGS: {
        "name": OPS_LOGS,
        "description": (
            "One service's log lines around a moment, cleaned of secrets as the admin's Logs page shows them: "
            f"``minutes`` before and after it ({LOG_MINUTES} at most), the newest {LOG_LINES} kept. ``around`` is "
            "the moment as the lines print it (UTC on the server). Only what the current log file still holds: it "
            "rotates at 2 MiB. Reads only."),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": list(LOG_SERVICES), "description": (
                    "; ".join(f"{key}: {name}" for key, name in LOG_SERVICES.items()))},
                "around": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$",
                           "description": "YYYY-MM-DD HH:MM, as the log's lines print it."},
                "minutes": {"type": "integer", "minimum": 1, "maximum": LOG_MINUTES, "default": 10},
                "level": {"type": "string", "enum": list(LEVELS), "description": "This level and above only."},
                "contains": {"type": "string", "maxLength": 200, "description": "Lines holding this text only."},
            },
            "required": ["service", "around"],
            "additionalProperties": False,
        },
    },
    OPS_TRADING: {
        "name": OPS_TRADING,
        "description": (
            "Trading now, as the dashboard reads it: the US market's day (closed, an early close with when entries "
            "end and what is held is sold; closed_today counts the closed dates and US holidays alone, and on a "
            "Saturday or a Sunday of the market's weekday the US market is closed all the same: no signal comes and "
            "Saxo quotes no turbo), the trading hours and their timezone, the window around a US release in "
            "force or next, the pause of new entries (the operator's, or the account guard's), Saxo refusing to renew "
            "WATA's login, today's figures (trades, result) against their limits, the cooldown after a loss, the day's "
            "breakers' figures, and each open position with its live value and the sales Saxo refused. A long or short "
            "signal is refused, in the trader's order: entries paused; Saxo's login not renewed; a closed day, outside "
            "the trading hours, from the risky minutes or an early close's end; inside a release's window; the day's "
            "result at or above stop_entries_above_percent or at or below max_day_loss_percent; max_trades_per_day "
            "reached; the cooldown running; max_consecutive_losses losses in a row, or the day "
            "max_drawdown_from_day_best_percent points under its best (a limit of 0 or null is off). The signal's own "
            "age, confidence and indice are judged when it comes. Reads only."),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    OPS_CHANGES: {
        "name": OPS_CHANGES,
        "description": (
            "What changed, newest first: config.json's saves key by key (secrets masked; how each was saved: by hand, "
            "from one of your proposals, a backup restored), the operator's actions recorded by the admin (pauses, "
            "closes, restarts, rules taken live or out of it, credentials set by name, deploys asked), and the last "
            f"deploy and how each container came out of it. ``hours`` before ``until`` (now by default), "
            f"{CHANGES_MAX_HOURS} at most. Who did it is never said. Reads only."),
        "input_schema": {
            "type": "object",
            "properties": {
                "until": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$",
                          "description": "YYYY-MM-DD HH:MM (UTC): the end of the look, now when not given."},
                "hours": {"type": "number", "minimum": 0.25, "maximum": CHANGES_MAX_HOURS, "default": CHANGES_HOURS},
            },
            "additionalProperties": False,
        },
    },
    OPS_ENGINE: {
        "name": OPS_ENGINE,
        "description": (
            "The TA engine's last minutes, as the Engine monitor's Now panel reads them: its last tick, how long its "
            "ticks and its rules' runs took against their limits, the feed's lag, its memory, the live signals and "
            "those not sent, each check of what can cost a live signal (failed or not), and the notes the page says "
            "beside them. Reads only."),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    OPS_RELEASE: {
        "name": OPS_RELEASE,
        "description": (
            "What waits to be deployed: the newest release installed on the server, each container that waits for "
            "it (what changes in it) and what holds it back now (positions open, the market open, a scheduler job "
            "near...), whether a deploy is under way, and how the last one ended, container by container. status "
            "already says which release each container runs. Reads only."),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


@dataclass(frozen=True)
class OpsReads:
    """What the admin lets the agent read of itself (src/admin/agent_ops.py): each read offered when given."""

    health: Callable[[], Awaitable[dict]]
    # read_log of src/admin/logs.py over the admin's log folder: (service, lines, level, query) -> its answer.
    logs: Callable[..., dict] | None = None
    # The dashboard's overview, shaped for the agent.
    trading: Callable[[], Awaitable[dict]] | None = None
    # (since, until) -> what changed between them.
    changes: Callable[[datetime, datetime], Awaitable[dict]] | None = None
    # The Engine monitor's Now panel.
    engine: Callable[[], Awaitable[dict]] | None = None
    # What waits to be deployed.
    release: Callable[[], Awaitable[dict]] | None = None

    def offered(self) -> list[str]:
        """The reads given, in the order the agent is shown them."""
        given = {OPS_HEALTH: self.health, OPS_LOGS: self.logs, OPS_TRADING: self.trading, OPS_CHANGES: self.changes,
                 OPS_ENGINE: self.engine, OPS_RELEASE: self.release}
        return [name for name in OPS_TOOLS if given[name] is not None]


@dataclass
class ToolAnswer:
    """What a tool answered the model, and what the request made with it."""

    text: str
    is_error: bool = False
    runs: list[str] = field(default_factory=list)
    proposal: dict | None = None
    script: dict | None = None
    experiment: str | None = None
    # One of Claude's shadow rules saved (rule_shadow_save).
    rule: dict | None = None
    # A research task said to be done (task_done): its conclusion.
    task_done: dict | None = None
    # A question a research task asks the operator (ask_operator).
    question: dict | None = None
    # An entry of its memory saved or forgotten: its name and what was done.
    memory: dict | None = None
    # A chart drawn under the answer (show_chart), as the pages draw it.
    chart: dict | None = None
    # A verdict set on a run, a rule, a day or a position (set_verdict).
    verdict: dict | None = None


def _definition(tool) -> dict:
    schema = json.loads(json.dumps(tool.input_schema))
    for name in DROPPED_ARGUMENTS.get(tool.name, ()):
        schema.get("properties", {}).pop(name, None)
        if name in schema.get("required", []):
            schema["required"].remove(name)
    schema.pop("title", None)
    return {"name": tool.name, "description": DESCRIPTIONS.get(tool.name, tool.description or tool.name),
            "input_schema": schema}


def _parsed(text: str):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


class AgentTools:
    """The tools, built once over the admin's research services; a session for each request."""

    def __init__(self, research, proposals, *, clock=time.time, ops: OpsReads | None = None, memory=None,
                 verdicts=None, actions: bool = False):
        self._research = research
        self._proposals = proposals
        self._clock = clock
        self._ops = ops
        # Its memory (src/admin/agent_memory.py), None when it has none (admin.ai_agent.memory).
        self._memory = memory
        # Its verdicts shown on the pages (src/admin/agent/verdicts.py): none, none offered.
        self._verdicts = verdicts
        # Whether it may propose actions on live trading (admin.ai_agent.action_proposals).
        self._actions = actions
        self._server = None
        self._definitions: list[dict] | None = None
        self._read_only: dict[str, bool] = {}
        # The studies of every request, one at a time (the server would refuse a second one after 15 s).
        self._study = asyncio.Lock()

    async def start(self) -> None:
        from mcp import Client

        from src.admin.mcp_server.server import build_server

        self._server = build_server(self._research)
        async with Client(self._server) as client:
            listed = {tool.name: tool for tool in (await client.list_tools()).tools}
        reads = [name for name in AGENT_TOOLS if self._memory is not None or name not in MEMORY_READS]
        missing = [name for name in reads if name not in listed]
        if missing:
            raise RuntimeError(f"The research tools lack {', '.join(missing)}")
        offered = self._ops.offered() if self._ops is not None else []
        memory = [dict(own) for own in MEMORY_DEFINITIONS] if self._memory is not None else []
        shown = [dict(CHART_DEFINITION)] + ([dict(VERDICT_DEFINITION)] if self._verdicts is not None else [])
        actions = [dict(ACTION_DEFINITION)] if self._actions else []
        self._definitions = ([_definition(listed[name]) for name in reads] + [dict(own) for own in OWN_DEFINITIONS]
                             + actions + shown + memory + [dict(OPS_DEFINITIONS[name]) for name in offered])
        self._read_only = {name: bool(listed[name].annotations and listed[name].annotations.read_only_hint)
                           for name in reads}

    def definitions(self) -> list[dict]:
        if self._definitions is None:
            raise RuntimeError("The agent's tools are not started")
        return self._definitions

    def is_read(self, name: str) -> bool:
        """A tool that only reads, and may run beside others: never a study, whose server runs one at a time."""
        return name in OPS_TOOLS or (self._read_only.get(name, False) and name not in STUDIES)

    @asynccontextmanager
    async def session(self, conversation_id: str, task: bool = False):
        """
        The calls of one request, as ``agent:<conversation>``, through a client
        opened and closed in its task; ``task``: a request of a research task,
        which may say it is done.
        """
        from mcp import Client

        caller = Caller(name=f"agent:{conversation_id}", token_id="agent", expires_at="")
        token = CALLER.set(caller)
        try:
            async with Client(self._server) as client:
                yield ToolSession(self, client, conversation_id, caller.name, task)
        finally:
            CALLER.reset(token)


class ToolSession:
    def __init__(self, tools: AgentTools, client, conversation_id: str, caller: str, task: bool = False):
        self._tools = tools
        self._client = client
        self._conversation_id = conversation_id
        self._caller = caller
        self._task = task
        # The charts this request drew.
        self._charts = 0

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    def offers(self, name: str) -> bool:
        """Whether the agent is given this tool (a read the admin gives, its memory's)."""
        return any(entry["name"] == name for entry in self._tools.definitions())

    async def call(self, name: str, arguments, request: int, progress=lambda detail: None,
                   stopping=lambda: False) -> ToolAnswer:
        definition = next((entry for entry in self._tools.definitions() if entry["name"] == name), None)
        if definition is None:
            return ToolAnswer(f"No tool {name!r}: use the tools you were given", is_error=True)
        if not isinstance(arguments, dict):
            return ToolAnswer(json.dumps({"INVALID_JSON": json.dumps(arguments)}), is_error=True)
        try:
            jsonschema.validate(arguments, definition["input_schema"])
        except jsonschema.ValidationError as e:
            return ToolAnswer(f"INVALID_JSON: {e.message}"[:2000], is_error=True)
        if name == TASK_DONE:
            return self._task_done(arguments)
        if name == ASK_OPERATOR:
            return self._ask_operator(arguments)
        if name == SHOW_CHART:
            return self._chart(arguments)
        if name == SET_VERDICT:
            return await self._verdict(arguments, request)
        if name in MEMORY_WRITES:
            return await self._remember(name, arguments, request)
        if name in OWN_TOOLS:
            return await self._propose(name, arguments, request)
        if name in OPS_TOOLS:
            return await self._ops(name, arguments)
        arguments = {key: value for key, value in arguments.items() if key not in DROPPED_ARGUMENTS.get(name, ())}
        if name == "backtest_wait":
            return await self._wait(arguments, progress, stopping)
        if name in STUDIES:
            async with self._tools._study:
                return await self._mcp(name, arguments)
        return await self._mcp(name, arguments)

    async def _mcp(self, name: str, arguments: dict) -> ToolAnswer:
        result = await self._client.call_tool(name, arguments)
        text = "\n".join(getattr(block, "text", "") for block in result.content) if result.content else ""
        if result.is_error:
            return ToolAnswer(text or f"{name} failed", is_error=True)
        answer = _parsed(text)
        if isinstance(answer, dict) and answer.get("state") == "running" and answer.get("job"):
            text, is_error = await self._job(name, answer["job"])
            if is_error:
                return ToolAnswer(text, is_error=True)
            answer = _parsed(text)
        try:
            text = self._fitted(name, text, answer)
        except _TooLong as e:
            return ToolAnswer(str(e), is_error=True)
        return self._made(name, text, answer)

    async def _ops(self, name: str, arguments: dict) -> ToolAnswer:
        ops = self._tools._ops
        if name == OPS_HEALTH:
            health = await ops.health()
            said = {"status": health.get("status"), "checked_at": health.get("checked_at"),
                    "services": [{key: entry.get(key) for key in ("id", "name", "status", "detail")}
                                 for entry in health.get("services") or []],
                    "queues": health.get("queues")}
            return self._fitted_answer(name, json.dumps(said, ensure_ascii=False, default=str))
        if name == OPS_CHANGES:
            until = (datetime.strptime(arguments["until"], _LOG_MOMENT).replace(tzinfo=timezone.utc)
                     if arguments.get("until") else datetime.fromtimestamp(self._tools._clock(), timezone.utc))
            hours = min(float(arguments.get("hours", CHANGES_HOURS)), CHANGES_MAX_HOURS)
            said = await ops.changes(until - timedelta(hours=hours), until)
            return self._fitted_answer(name, json.dumps(said, ensure_ascii=False, default=str))
        if name in (OPS_TRADING, OPS_ENGINE, OPS_RELEASE):
            read = {OPS_TRADING: ops.trading, OPS_ENGINE: ops.engine, OPS_RELEASE: ops.release}[name]
            try:
                said = await read()
            except Exception as e:
                # The database or a file out of reach: said as a refusal the agent can work around.
                return ToolAnswer(f"{name} could not be read now: {e}"[:500], is_error=True)
            return self._fitted_answer(name, json.dumps(said, ensure_ascii=False, default=str))
        around = datetime.strptime(arguments["around"], _LOG_MOMENT)
        span = timedelta(minutes=arguments.get("minutes", 10))
        found = await run_in_threadpool(ops.logs, arguments["service"], MAX_LINES, arguments.get("level"),
                                        arguments.get("contains"))

        def moment(entry: dict) -> datetime | None:
            try:
                return datetime.strptime(entry["timestamp"][:19], "%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                return None

        window = [entry for entry in found.get("entries") or []
                  if (at := moment(entry)) is not None and abs(at - around) <= span]
        kept = window[-LOG_LINES:]
        said = {"service": arguments["service"], "file": found.get("exists"),
                "from": (around - span).strftime(_LOG_MOMENT), "to": (around + span).strftime(_LOG_MOMENT),
                "lines": len(window), "shown": len(kept), "entries": kept}
        text = json.dumps(said, ensure_ascii=False)
        # The newest lines kept when they are long: the cut said.
        while len(text) > ANSWER_CHARS and said["entries"]:
            said["entries"] = said["entries"][len(said["entries"]) // 4 or 1:]
            said["shown"] = len(said["entries"])
            said["cut"] = "the oldest lines of the window were left out: ask for fewer minutes or a level"
            text = json.dumps(said, ensure_ascii=False)
        return ToolAnswer(text)

    def _fitted_answer(self, name: str, text: str) -> ToolAnswer:
        try:
            return ToolAnswer(self._fitted(name, text, None))
        except _TooLong as e:
            return ToolAnswer(str(e), is_error=True)

    async def _job(self, name: str, job_id: str) -> tuple[str, bool]:
        """A study that outlasted its call: waited for to its end, a stop included (pine_save writes)."""
        from src.admin.mcp_server.budget import said

        while True:
            try:
                answer = await self._tools._research.jobs.wait(job_id, self._caller, 45)
            except Exception as e:
                return str(said(name, e)[2]), True
            if not (isinstance(answer, dict) and answer.get("state") == "running"):
                return (answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)), False

    async def _wait(self, arguments: dict, progress, stopping) -> ToolAnswer:
        """backtest_wait, looked at every WAIT_STEP_SECONDS until the runs end, WAIT_SECONDS at most, or a stop."""
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            answer = await self._mcp("backtest_wait", {**arguments, "seconds": WAIT_STEP_SECONDS})
            states = _parsed(answer.text) if not answer.is_error else None
            if not isinstance(states, dict) or not states.get("unfinished") or time.monotonic() >= deadline or stopping():
                return answer
            progress(_waiting(states))

    def _fitted(self, name: str, text: str, answer) -> str:
        limit = LONG_ANSWER_CHARS.get(name, ANSWER_CHARS)
        if name == "pine_get" and isinstance(answer, dict) and len(answer.get("versions") or []) > SHOWN_VERSIONS:
            answer = {**answer, "versions": answer["versions"][-SHOWN_VERSIONS:]}
            text = json.dumps(answer, ensure_ascii=False, separators=(",", ":"))
        if len(text) > limit:
            raise _TooLong(len(text), limit)
        return text

    def _made(self, name: str, text: str, answer) -> ToolAnswer:
        made = ToolAnswer(text)
        if not isinstance(answer, dict):
            return made
        if name in STARTS and answer.get("id"):
            made.runs.append(answer["id"])
        if name == "pine_save" and answer.get("saved"):
            made.script = {"id": answer.get("id"), "name": answer.get("name"), "version": answer.get("version")}
        if name == "exp_new" and answer.get("id"):
            made.experiment = answer["id"]
        if name == "rule_shadow_save" and answer.get("saved"):
            made.rule = {"id": answer.get("id"), "name": answer.get("name"), "mode": answer.get("mode")}
        return made

    async def _propose(self, name: str, arguments: dict, request: int) -> ToolAnswer:
        proposals = self._tools._proposals
        make = {PROPOSE_SCRIPT: proposals.propose_script, PROPOSE_RULE: proposals.propose_rule,
                PROPOSE_SETTINGS: proposals.propose_settings, PROPOSE_NEW_SCRIPT: proposals.propose_new_script,
                PROPOSE_NEW_RULE: proposals.propose_new_rule}.get(name)
        if name == PROPOSE_ACTION:
            # A close is proposed on a position as WATA has it now (read here: the proposals read no database).
            position = None
            if arguments.get("action") == "close_position" and arguments.get("position_id"):
                found = await self._tools._research.queries.position_record(str(arguments["position_id"]))
                position = (found or {}).get("position")
            make = lambda conversation, asked, given: proposals.propose_action(  # noqa: E731
                conversation, asked, given, position=position)
        try:
            proposal = await run_in_threadpool(make, self._conversation_id, request, arguments)
        except ProposalRefused as e:
            return ToolAnswer(str(e), is_error=True)
        said = {"proposal": proposal["id"], "kind": proposal["kind"],
                "next": "The operator sees it under your answer and applies it, or not: say what it changes and why."}
        if proposal.get("same"):
            said["left_out"] = f"Already the live value: {', '.join(proposal['same'])}"
        return ToolAnswer(json.dumps(said, ensure_ascii=False), proposal=proposal)


    async def _remember(self, name: str, arguments: dict, request: int) -> ToolAnswer:
        """An entry of its memory saved or forgotten, as ``agent:<conversation>``: refused with a sentence otherwise."""
        memory = self._tools._memory
        author = Author.agent(self._conversation_id, request)
        try:
            if name == MEMORY_SAVE:
                entry, action = await run_in_threadpool(lambda: memory.save(
                    author, arguments["name"], kind=arguments["kind"], description=arguments["description"],
                    body=arguments["body"], revision=arguments.get("revision")))
            else:
                entry = await run_in_threadpool(lambda: memory.forget(
                    author, arguments["name"], revision=arguments["revision"], why=arguments["why"]))
                action = FORGOTTEN
        except (InvalidRequest, NotPossible, UnknownEntry) as e:
            return ToolAnswer(str(e), is_error=True)
        if action is None:
            return ToolAnswer(json.dumps({"unchanged": entry["name"], "revision": entry["revision"]}))
        said = {action: entry["name"], "next": "Say in your answer what you saved or forgot."}
        if action != FORGOTTEN:
            said["revision"] = entry["revision"]
        return ToolAnswer(json.dumps(said, ensure_ascii=False), memory={"name": entry["name"], "action": action})

    def _chart(self, arguments: dict) -> ToolAnswer:
        if self._charts >= agent_charts.MAX_CHARTS:
            return ToolAnswer(f"This request drew {agent_charts.MAX_CHARTS} charts already: say the rest in words",
                              is_error=True)
        try:
            chart = agent_charts.checked_chart(arguments)
        except agent_charts.ChartRefused as e:
            return ToolAnswer(str(e), is_error=True)
        self._charts += 1
        said = {"shown": chart["title"], "next": "It is drawn under your answer: say what it shows."}
        return ToolAnswer(json.dumps(said, ensure_ascii=False), chart=chart)

    async def _verdict(self, arguments: dict, request: int) -> ToolAnswer:
        """A verdict on a thing that exists: a run, a rule, a day, a position WATA knows."""
        about = arguments["about"]
        kind, _, thing = about.partition(":")
        research = self._tools._research
        try:
            if kind == "run":
                if research.backtests is None:
                    raise LookupError("Backtests are switched off: there is no run to judge")
                await run_in_threadpool(research.backtests.get, thing)
            elif kind == "rule":
                if research.rules is None:
                    raise LookupError("There is no signal rule here")
                await run_in_threadpool(research.rules.rule, thing)
            elif kind == "day":
                datetime.strptime(thing, "%Y-%m-%d")
            elif await research.queries.position_record(thing) is None:
                raise LookupError(f"WATA has no position {thing}")
        except (UnknownRun, RuleNotFound) as e:
            return ToolAnswer(f"Nothing to judge: {e}", is_error=True)
        except ValueError:
            return ToolAnswer("A day is day:YYYY-MM-DD", is_error=True)
        except LookupError as e:
            return ToolAnswer(str(e), is_error=True)
        verdict = await run_in_threadpool(lambda: self._tools._verdicts.save(
            about, arguments["tone"], arguments["line"], conversation=self._conversation_id, request=request,
            at=stamp(self._tools._clock())))
        said = {"set": about, "next": "Its page shows it now: say it in your answer too."}
        return ToolAnswer(json.dumps(said), verdict={key: verdict[key] for key in ("about", "tone", "line")})

    def _ask_operator(self, arguments: dict) -> ToolAnswer:
        if not self._task:
            return ToolAnswer("This conversation is not a research task: ask the operator in your answer", is_error=True)
        question = {"question": arguments["question"].strip(), "why": arguments["why"].strip(),
                    "choices": [choice.strip() for choice in arguments.get("choices") or [] if choice.strip()]}
        said = {"asked": True, "next": "End your answer now: the task waits for the operator's answer, and your next "
                                        "request starts with it."}
        return ToolAnswer(json.dumps(said), question=question)

    def _task_done(self, arguments: dict) -> ToolAnswer:
        if not self._task:
            return ToolAnswer("This conversation is not a research task: answer the operator", is_error=True)
        done = {"conclusion": arguments["conclusion"].strip(), "runs_kept": list(dict.fromkeys(arguments.get("runs_kept") or [])),
                "next_steps": (arguments.get("next_steps") or "").strip() or None}
        said = {"noted": True, "next": "Answer now with your conclusion: the task ends with this request."}
        return ToolAnswer(json.dumps(said), task_done=done)


class _TooLong(Exception):
    def __init__(self, length: int, limit: int):
        super().__init__(f"The answer would be {length:,} characters, over the {limit:,} you read at once: ask for fewer "
                         "rows (limit, offset), a shorter period or less detail")


def _waiting(states: dict) -> str:
    """What a wait is at, for the pages: each run's state, its place in line and its progress."""
    said = []
    for run in states.get("runs") or []:
        state = run.get("state") or "?"
        if state == "queued" and run.get("position"):
            said.append(f"{run.get('id')}: {_ordinal(run['position'])} in line")
        elif state == "running" and isinstance(run.get("progress"), dict) and run["progress"].get("total"):
            done = run["progress"]
            said.append(f"{run.get('id')}: {done.get('phase') or 'running'}, {done.get('done', 0)} of {done['total']}")
        else:
            said.append(f"{run.get('id')}: {state}")
    return "; ".join(said)


def _ordinal(number: int) -> str:
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"
