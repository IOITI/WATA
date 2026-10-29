"""
A made-up Claude API for the development admin (FAKE_AI=1): the AI agent's
real service, tools, proposals and files (src/admin/agent/), with answers
scripted here and streamed piece by piece as the API streams them. Nothing
reaches Anthropic.

A run shared: its edge read, an experiment opened, a stress test of it started
and waited for, a tighter stop proposed for config.json, then an answer with a
table and links. A script shared: a copy of it saved as Claude's with an input
changed, proposed as a new version and as a new script, and the change of a
rule that runs it (one not live), proposed after it. A position, a signal, a
day or an incident shared: an account of it, the health and logs read for an
incident. A research task: a look at the backtests, then, at its next request,
task_done and its conclusion. A daily debrief: an account of the day. Anything
else: the backtests listed, then an answer.

Its memory (seed_memory, at the start): a run shared has the entries its
memory's list names read first, and a finding about the run saved before the
answer.
"""

import asyncio
import json
import re
import secrets

from src.admin.agent import provider as claude
from src.admin.agent_debrief import DailyDebrief
from src.admin.agent_memory import Author
from src.admin.agent.settings import EFFORTS
from src.admin.ta_rules import LIVE, RuleStore

KEY = "sk-ant-dev-" + "0" * 32
# A piece of text every so often: slow enough to see it come.
PIECE_SECONDS = 0.04
# Before an answer's first piece, as the API thinks before it streams: time enough to stop a request.
FIRST_PIECE_SECONDS = 1.5
# A thinking block as a count sees it: the whole reasoning, of which the pages show a short note.
THOUGHT_TOKENS = 450
_SHARED = re.compile(r"The operator shares the (backtest run|Pine script) .*?\(([0-9a-f-]{12,22})\)")
_SHARED_TRADING = re.compile(r"The operator shares the (position|signal|trading day|incident) ([^\n]*?), as your tools")
_TASK = "A research task the operator left you"
_TASK_GOES_ON = "The research task goes on"
_DEBRIEF = "Write the debrief of this trading day"
# An entry of the memory's list, as a conversation's first words give it (src/admin/agent/prompts.py memory_index).
_LISTED = re.compile(r"^- \[[a-z]+\] ([a-z0-9-]+): ", re.M)
# What the development admin's memory holds at its start.
SEEDED = (
    ("operator", "answer-short", "preference", "The verdict first, five lines at most: I read the answers on a phone",
     "Start with whether the result can be believed and on how many trades and days, then at most four lines.", True),
    ("agent", "range-ticks", "lesson", "R60 is 6 points on capital/US100, 0.06 on dukascopy/us100",
     "Dukascopy's files hold thousandths: 6 points there are **R6000**. Seen 2026-10-21 (made up).", False),
    ("agent", "kernel-vwap-no-edge", "finding", "The kernel and VWAP script showed no edge over 90 days",
     "No edge at any horizon (t 1.1), 122 trades on made-up turbos, 2026-10-21. Rejected (made up).", False),
)


def _blocks_of(message) -> list:
    content = message.get("content")
    return content if isinstance(content, list) else [{"type": "text", "text": content or ""}]


class FakeClaude:
    def __init__(self, scripts, backtests=None):
        self._scripts = scripts
        self._rules = RuleStore(scripts)
        # The live settings, for a change of settings that is not one already.
        self._backtests = backtests
        # The agent's conversations (agent_of sets it): the proposals a turn follows up.
        self.store = None
        self._opening = ""

    async def close(self) -> None:
        pass

    async def model_info(self, model_id: str) -> dict:
        return {"label": claude.label_of(model_id), "window": 1_000_000, "max_output": 128_000, "efforts": list(EFFORTS)}

    async def count_tokens(self, params: dict) -> dict:
        """A token for four characters, a thinking block worth THOUGHT_TOKENS: made up, as the answers are."""
        tokens = 3 + len(json.dumps(params.get("system") or "")) // 4 + len(json.dumps(params.get("tools") or [])) // 4
        for message in params["messages"]:
            for block in _blocks_of(message):
                tokens += THOUGHT_TOKENS if block.get("type") == "thinking" else len(json.dumps(block)) // 4
        return {"input_tokens": tokens}

    async def stream(self, params, on_event) -> dict:
        on_event({"kind": "model", "model": params["model"]})
        await asyncio.sleep(FIRST_PIECE_SECONDS)
        messages = params["messages"]
        subject = None
        for message in messages:
            for block in _blocks_of(message):
                said = block.get("text", "") if block.get("type") == "text" else ""
                found, trading = _SHARED.search(said), _SHARED_TRADING.search(said)
                if found and subject is None:
                    subject = ("run" if found.group(1) == "backtest run" else "script", found.group(2))
                elif trading and subject is None:
                    subject = (trading.group(1), trading.group(2).strip())
        done = self._done(messages)
        asked = self._asked(messages)
        # The conversation's first words: its memory's list, when it has one.
        self._opening = next((block.get("text", "") for block in _blocks_of(messages[0])
                              if block.get("type") == "text"), "") if messages else ""
        if asked.startswith(_DEBRIEF):
            blocks = await self._debrief(on_event)
        elif asked.startswith(_TASK_GOES_ON):
            blocks = await self._task_going_on(done, on_event)
        elif asked.startswith(_TASK):
            blocks = await self._task(done, on_event)
        else:
            blocks = await self._next(subject, done, on_event)
        stop = "tool_use" if any(block["type"] == "tool_use" for block in blocks) else "end_turn"
        written = sum(len(json.dumps(block)) for block in blocks)
        return {"id": f"msg_{secrets.token_hex(6)}", "type": "message", "role": "assistant", "model": params["model"],
                "content": blocks, "stop_reason": stop,
                "usage": {"input_tokens": 1200 + 400 * len(messages), "output_tokens": written // 4 + THOUGHT_TOKENS,
                          "output_tokens_details": {"thinking_tokens": THOUGHT_TOKENS},
                          "cache_read_input_tokens": 9000, "cache_creation_input_tokens": 600}}

    @staticmethod
    def _asked(messages) -> str:
        """The prompt of the request under way: the last text the operator's side sent."""
        for message in reversed(messages):
            if message["role"] != "user":
                continue
            texts = [block.get("text", "") for block in _blocks_of(message) if block.get("type") == "text"]
            if texts:
                return texts[-1]
        return ""

    @staticmethod
    def _done(messages) -> dict:
        """The tools this request called, with their answers, since its prompt."""
        start = 0
        for index, message in enumerate(messages):
            if message["role"] == "user" and any(block.get("type") == "text" for block in _blocks_of(message)):
                start = index
        calls, answers = {}, {}
        for message in messages[start:]:
            for block in _blocks_of(message):
                if block.get("type") == "tool_use":
                    calls[block["id"]] = block["name"]
                elif block.get("type") == "tool_result":
                    answers[calls.get(block["tool_use_id"], "?")] = block.get("content")
        return answers

    async def _say(self, on_event, index: int, kind: str, text: str) -> None:
        on_event({"kind": "block", "index": index, "type": "thinking" if kind == "thinking" else "text", "name": None})
        for start in range(0, len(text), 18):
            on_event({"kind": kind, "index": index, "text": text[start:start + 18]})
            await asyncio.sleep(PIECE_SECONDS)

    async def _call(self, on_event, index: int, name: str, arguments: dict) -> dict:
        on_event({"kind": "block", "index": index, "type": "tool_use", "name": name})
        on_event({"kind": "input", "index": index, "length": len(json.dumps(arguments))})
        await asyncio.sleep(PIECE_SECONDS * 4)
        return {"type": "tool_use", "id": f"toolu_{secrets.token_hex(8)}", "name": name, "input": arguments}

    async def _turn(self, on_event, note: str, calls=(), text: str | None = None) -> list:
        blocks = [{"type": "thinking", "thinking": note, "signature": "fake"}]
        await self._say(on_event, 0, "thinking", note)
        if text:
            await self._say(on_event, 1, "text", text)
            blocks.append({"type": "text", "text": text})
        for offset, (name, arguments) in enumerate(calls, start=len(blocks)):
            blocks.append(await self._call(on_event, offset, name, arguments))
        return blocks

    async def _next(self, subject, done: dict, on_event) -> list:
        if subject and subject[0] == "run":
            return await self._run(subject[1], done, on_event)
        if subject and subject[0] == "script":
            return await self._script(subject[1], done, on_event)
        if subject and subject[0] == "incident":
            return await self._incident(subject[1], done, on_event)
        if subject:
            return await self._trading(subject, on_event)
        if "backtest_list" not in done:
            return await self._turn(on_event, "I look at the backtests there are first.", [("backtest_list", {"limit": 10})])
        return await self._turn(on_event, "I have what I need.", text=(
            "## What is worth trying\n\nThe runs listed are few and short: **none can be believed yet** "
            "(under 30 trades or 20 days each).\n\n- Run the strongest script over every recorded day\n"
            "- Stress it before keeping anything\n"))

    async def _run(self, run_id: str, done: dict, on_event) -> list:
        listed = _LISTED.findall(self._opening)
        if listed and "memory_read" not in done:
            return await self._turn(on_event, "My memory names what I learned before: I read what bears on a run.", [
                ("memory_read", {"names": listed[:2]})])
        if "backtest_result" not in done:
            return await self._turn(on_event, "I read the edge of its entries before its total.", [
                ("backtest_result", {"run_id": run_id, "part": "edge"})])
        if "exp_new" not in done:
            return await self._turn(on_event, "A stress test counts as a trial: I open an experiment for it.", [
                ("exp_new", {"title": "Does it survive worse fills?", "family": "stress",
                             "hypothesis": "The run's result holds with each order later and each fill dearer"})])
        if "backtest_derive" not in done:
            experiment = json.loads(done["exp_new"]).get("id", "") if done["exp_new"].startswith("{") else ""
            return await self._turn(on_event, "I stress the run: later orders, dearer fills.", [
                ("backtest_derive", {"run_id": run_id, "kind": "stress", "experiment_id": experiment})])
        derived = done["backtest_derive"]
        stress_id = json.loads(derived).get("id") if derived.startswith("{") else None
        if stress_id and "backtest_wait" not in done:
            return await self._turn(on_event, "I wait for the stress test to end.", [("backtest_wait", {"run_ids": [stress_id]})])
        if "propose_settings_change" not in done and self._backtests is not None:
            stop = self._backtests.live_settings().stoploss_percent
            tighter = round(max(-99.0, min(-0.5, stop + 2 if stop <= -2.5 else stop - 2)), 1)
            return await self._turn(on_event, "Its losers ran far: I propose a tighter stop, resting on this run.", [
                ("propose_settings_change", {
                    "changes": [{"setting": "stoploss_percent", "value": tighter}], "runs": [run_id],
                    "title": f"A stop at {tighter} %",
                    "rationale": "Made up by the development admin: the run's losers ran past the stop before it sold."})])
        name = f"run-{run_id[-6:]}-few-days"
        if "memory_save" not in done:
            return await self._turn(on_event, "What I found holds for later conversations: I keep it.", [
                ("memory_save", {"name": name, "kind": "finding",
                                 "description": f"Run {run_id} rests on too few days to be believed",
                                 "body": f"Under 30 trades and 20 days (made up by the development admin), "
                                         f"[the run](/backtests/{run_id})."})])
        link = f"[the stress test](/backtests/{stress_id})" if stress_id else "the stress test (refused)"
        return await self._turn(on_event, "I have its edge and its stress test.", text=(
            f"I kept it in my memory as [{name}](/agent/memory?find={name}).\n\n"
            f"## Not believable yet\n\nIt rests on **too few days** to be believed, whatever its total; see {link}.\n\n"
            "| Check | Result |\n| --- | --- |\n| Trades | under 30 |\n| Days | under 20 |\n| Stress | read above |\n\n"
            "- Run it again over every recorded day\n- Then look at the edge of its entries before its total\n"))

    async def _script(self, script_id: str, done: dict, on_event) -> list:
        script = self._scripts.script(script_id)
        if "propose_script_change" not in done:
            source = re.sub(r"input\.int\((\d+)", lambda found: f"input.int({int(found.group(1)) + 2}", script["source"], count=1)
            if source == script["source"]:
                source = script["source"].rstrip("\n") + "\n// Tuned by the AI agent (made up)\n"
            # As pine_save would have saved it, the studies aside.
            draft = self._scripts.create(f"{script['name']} by Claude {secrets.token_hex(2)}", source, made_by="claude")
            return await self._turn(on_event, "A slower first input should trade less noise: I propose it.", [
                ("propose_script_change", {"script_id": script_id, "base_version": script["version"],
                                           "from_script_id": draft["id"], "from_version": 1,
                                           "title": "A slower first input",
                                           "rationale": "Made up by the development admin: an input two steps slower."})])
        if "propose_new_script" not in done:
            draft = json.loads(done["propose_script_change"]) if done["propose_script_change"].startswith("{") else {}
            proposal = self._proposal_of(draft.get("proposal"))
            if proposal is not None:
                return await self._turn(on_event, "It could also stand as a script of its own, beside yours.", [
                    ("propose_new_script", {
                        "from_script_id": proposal["draft"]["id"], "from_version": proposal["draft"]["version"],
                        "name": f"{script['name']}, slower {secrets.token_hex(2)}",
                        "description": "The same, its first input two steps slower (made up).",
                        "title": "A slower copy, as a script of its own",
                        "rationale": "Made up by the development admin: to run beside the original."})])
        # The operator's rules first: Claude Code's shadow rules are its own.
        rules = sorted(self._rules.load()["rules"], key=lambda rule: rule.get("made_by") == "claude")
        rule = next((rule for rule in rules if rule["script"]["id"] == script_id and rule["mode"] != LIVE), None)
        if rule and "propose_rule_change" not in done:
            proposal = json.loads(done["propose_script_change"]).get("proposal") if done["propose_script_change"].startswith("{") else None
            return await self._turn(on_event, f"The rule {rule['name']} runs it: I propose it runs the new version.", [
                ("propose_rule_change", {"rule_id": rule["id"], "after_proposal": proposal or "",
                                         "title": "Run the new version", "rationale": "Once the new version is applied."})])
        return await self._turn(on_event, "I proposed what I would change.", text=(
            "## A change to try\n\nI propose **a slower first input**, as a new version of the script: review it "
            "below and apply it, or not. Signal rules keep their version until changed."))

    def _proposal_of(self, proposal_id: str | None) -> dict | None:
        """One of the conversation's proposals, from its files (the conversation is the newest)."""
        if not proposal_id or self.store is None:
            return None
        for meta in self.store.metas():
            found = self.store.proposals(meta["id"]).get(proposal_id)
            if found is not None:
                return found
        return None

    async def _trading(self, subject, on_event) -> list:
        kind, label = subject
        return await self._turn(on_event, f"The {kind} is shared whole: I read what it says.", text=(
            f"## The {kind}, {label}\n\nWhat it shows (made up by the development admin): the **orders went out "
            "on time**, Saxo's answers were quick, and the result follows from the exit rules.\n\n"
            "- Nothing points at a fault of WATA's\n- Look at the turbo's quote cadence if it happens again\n"))

    async def _incident(self, label: str, done: dict, on_event) -> list:
        if "ops_health" not in done:
            return await self._turn(on_event, "The health first, then its lines around the moment.", [("ops_health", {})])
        return await self._turn(on_event, "I have the health and the lines around the moment.", text=(
            f"## {label}\n\nWhat the lines show (made up): **a stream reconnected** and the service went on. "
            "Nothing to act on; if it comes back, restart the service from System when the market is closed."))

    async def _task(self, done: dict, on_event) -> list:
        if "backtest_list" not in done:
            return await self._turn(on_event, "A research task: I start by looking at the runs there are.", [
                ("backtest_list", {"limit": 10})])
        return await self._turn(on_event, "A first look is done: the next request goes on from here.", text=(
            "I looked at the runs there are. Next: read the strongest one's edge, then decide."))

    async def _task_going_on(self, done: dict, on_event) -> list:
        if "task_done" not in done:
            return await self._turn(on_event, "Enough to conclude within the budget.", [
                ("task_done", {"conclusion": "No run here rests on enough days to be believed (made up).",
                               "runs_kept": [], "next_steps": "Record more days, then run the strongest script again."})])
        return await self._turn(on_event, "The task is done.", text=(
            "## Conclusion\n\nNo run here rests on enough days to be believed. **Record more days**, then run the "
            "strongest script again over all of them."))

    async def _debrief(self, on_event) -> list:
        return await self._turn(on_event, "The day is shared whole: I write its account.", text=(
            "A calm day: two trades, one won, one lost, both sold by their rules (made up by the development admin).\n\n"
            "1. **What happened**: a long sold at its target, a short at its stop; one refusal of Saxo, tried again.\n"
            "2. **What stands out**: nothing beyond the refusal.\n3. **Next**: nothing to look at."))


def agent_of(ctx):
    """The agent of the development admin: real (its health and logs read as the admin's), but for its answers."""
    from src.admin.context import agent_of as real_agent_of

    async def check_key(key: str, model_id: str):
        return True, f"Works: {claude.label_of(model_id)} answers (made up)"

    claude.check_key = check_key
    agent = real_agent_of(ctx)
    fake = FakeClaude(ctx.ta_scripts, ctx.backtests)
    fake.store = agent.store
    agent._provider = fake
    return agent


def seed_memory(memory) -> None:
    """A few entries, the operator's and the agent's, once: what the Memory page and a run shared start with."""
    if memory.entries():
        return
    for author, name, kind, description, body, locked in SEEDED:
        who = Author.operator("127.0.0.1") if author == "operator" else Author.agent("20261021-090000-0a0b0c", 0)
        memory.save(who, name, kind=kind, description=description, body=body, locked=locked)


class FakeDailyDebrief(DailyDebrief):
    """The daily debrief at any hour: the fake's made-up market is closed, the last finished weekday debriefed."""

    def _market_is_open(self, config: dict, now: float) -> bool:
        return False
