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

The MCP SDK is imported in the functions that use it: with the agent off, the
admin never loads it.
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Awaitable, Callable

import jsonschema
from starlette.concurrency import run_in_threadpool

from src.admin.agent.proposals import ProposalRefused
from src.admin.logs import LEVELS, LOG_SERVICES, MAX_LINES
from src.admin.mcp_server.gate import CALLER
from src.admin.mcp_server.tokens import Caller

# The research tools the agent may call, in the order it is shown them. Never the tools that remove scripts or runs
# (pine_delete, backtest_remove), set the lock box, measure the turbo model again, write down trials made elsewhere
# or export a week of bars; job_wait is waited for here. Claude's own shadow rules are kept as the MCP endpoint keeps
# them (rule_shadow_*): gated by admin.mcp.shadow_rules, never live, the endpoint's only change of a mode.
AGENT_TOOLS = (
    "status", "data_assets", "data_coverage", "data_signals", "data_trades", "data_execution", "data_position",
    "data_orders",
    "pine_list", "pine_get", "pine_check", "pine_run", "pine_signals", "pine_compare", "pine_save",
    "backtest_options", "backtest_list", "backtest_result", "backtest_trades", "backtest_days", "backtest_signals",
    "backtest_compare", "backtest_start", "backtest_derive", "backtest_wait",
    "turbo_model", "rule_list", "rule_compare", "rule_shadow_save", "rule_shadow_remove", "rule_set_reference",
    "exp_new", "exp_get", "exp_conclude",
)
PROPOSE_SCRIPT = "propose_script_change"
PROPOSE_RULE = "propose_rule_change"
PROPOSE_SETTINGS = "propose_settings_change"
PROPOSE_NEW_SCRIPT = "propose_new_script"
# The end of a research task, said by the agent (src/admin/agent/service.py chains a task's requests until it).
TASK_DONE = "task_done"
PROPOSALS = (PROPOSE_SCRIPT, PROPOSE_RULE, PROPOSE_SETTINGS, PROPOSE_NEW_SCRIPT)
OWN_TOOLS = (*PROPOSALS, TASK_DONE)
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
)


# The admin's own state, read for the agent: its health, and a service's log lines around a moment.
OPS_HEALTH = "ops_health"
OPS_LOGS = "ops_logs"
OPS_TOOLS = (OPS_HEALTH, OPS_LOGS)
# A look at a log: the lines of its window, the newest kept, at most.
LOG_LINES = 300
LOG_MINUTES = 60
_LOG_MOMENT = "%Y-%m-%d %H:%M"

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
}


@dataclass(frozen=True)
class OpsReads:
    """What the admin lets the agent read of itself (build_agent): its health, and its services' logs when allowed."""

    health: Callable[[], Awaitable[dict]]
    # read_log of src/admin/logs.py over the admin's log folder: (service, lines, level, query) -> its answer.
    logs: Callable[..., dict] | None = None


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

    def __init__(self, research, proposals, *, clock=time.time, ops: OpsReads | None = None):
        self._research = research
        self._proposals = proposals
        self._clock = clock
        self._ops = ops
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
        missing = [name for name in AGENT_TOOLS if name not in listed]
        if missing:
            raise RuntimeError(f"The research tools lack {', '.join(missing)}")
        offered = [OPS_HEALTH] if self._ops is not None else []
        if self._ops is not None and self._ops.logs is not None:
            offered.append(OPS_LOGS)
        self._definitions = ([_definition(listed[name]) for name in AGENT_TOOLS] + [dict(own) for own in OWN_DEFINITIONS]
                             + [dict(OPS_DEFINITIONS[name]) for name in offered])
        self._read_only = {name: bool(listed[name].annotations and listed[name].annotations.read_only_hint)
                           for name in AGENT_TOOLS}

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
                PROPOSE_SETTINGS: proposals.propose_settings, PROPOSE_NEW_SCRIPT: proposals.propose_new_script}[name]
        try:
            proposal = await run_in_threadpool(make, self._conversation_id, request, arguments)
        except ProposalRefused as e:
            return ToolAnswer(str(e), is_error=True)
        said = {"proposal": proposal["id"], "kind": proposal["kind"],
                "next": "The operator sees it under your answer and applies it, or not: say what it changes and why."}
        if proposal.get("same"):
            said["left_out"] = f"Already the live value: {', '.join(proposal['same'])}"
        return ToolAnswer(json.dumps(said, ensure_ascii=False), proposal=proposal)


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
