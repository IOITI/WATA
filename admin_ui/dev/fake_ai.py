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
import base64
import json
import re
import secrets
from datetime import datetime, timezone

from src.admin.agent import provider as claude
from src.admin.agent_briefing import MorningBriefing
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
_SHARED_RULE = re.compile(r"The operator shares the signal rule .*?\(([0-9a-f]{12})\), as your tools")
_SHARED_REVIEW = re.compile(r"The operator shares (a change, to review before it is saved:|an unsaved draft of the script,) ")
_DRAFT_OF = re.compile(r"What changed from version (\d+) of script ([0-9a-f]{12})")
_TASK = "A research task the operator left you"
_TASK_GOES_ON = "The research task goes on"
_DEBRIEF = "Write the debrief of this trading day"
_BRIEFING = "Write the morning briefing"
_TRIAGE = "An alert fired"
# A PNG says its width and height in its first 24 bytes (its IHDR chunk).
_PNG = b"\x89PNG\r\n\x1a\n"
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
                rule, review, draft = _SHARED_RULE.search(said), _SHARED_REVIEW.search(said), _DRAFT_OF.search(said)
                if found and subject is None:
                    subject = ("run" if found.group(1) == "backtest run" else "script", found.group(2))
                elif rule and subject is None:
                    # Before the trading ones: "the signal rule" starts as "the signal" does.
                    subject = ("rule", rule.group(1), '"readiness": []' in said)
                elif trading and subject is None:
                    subject = (trading.group(1), trading.group(2).strip())
                elif draft and subject is None:
                    subject = ("draft", draft.group(2), int(draft.group(1)))
                elif review and subject is None:
                    subject = ("change", "")
        done = self._done(messages)
        asked = self._asked(messages)
        shared, files = self._given(messages)
        # The conversation's first words: its memory's list, when it has one.
        self._opening = next((block.get("text", "") for block in _blocks_of(messages[0])
                              if block.get("type") == "text"), "") if messages else ""
        if asked.startswith(_DEBRIEF):
            blocks = await self._debrief(subject, done, on_event)
        elif asked.startswith(_BRIEFING):
            blocks = await self._briefing(on_event)
        elif asked.startswith(_TRIAGE):
            blocks = await self._triage(done, on_event)
        elif asked.startswith(_TASK_GOES_ON):
            blocks = await self._task_going_on(done, on_event)
        elif asked.startswith(_TASK):
            blocks = await self._task(done, on_event)
        elif (files or len(shared) > 1) and not done:
            # Several things attached, or files: it says what it was given.
            blocks = await self._attached(shared, files, on_event)
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
    def _given(messages) -> tuple[list[str], list[dict]]:
        """What the request under way was given: the things shared (each as it is named) and the files attached."""
        for message in reversed(messages):
            blocks = _blocks_of(message) if message["role"] == "user" else []
            if not any(block.get("type") == "text" for block in blocks):
                continue
            shared = [block["text"].removeprefix("The operator shares ").split(", as your tools", 1)[0]
                      for block in blocks if block.get("type") == "text" and block["text"].startswith("The operator shares ")]
            return shared, [block for block in blocks if block.get("type") in ("image", "document")]
        return [], []

    async def _attached(self, shared: list[str], files: list[dict], on_event) -> list:
        lines = [f"- {thing}" for thing in shared]
        for block in files:
            source = block.get("source") or {}
            data = source.get("data") or ""
            if source.get("type") == "text":
                rows = data.count("\n") + (0 if data.endswith("\n") else 1)
                lines.append(f"- {block.get('title')}, a text of {rows} line{'' if rows == 1 else 's'}")
                continue
            weight = f"{max(1, len(data) * 3 // 4 // 1024)} kB"
            if block["type"] == "document":
                lines.append(f"- {block.get('title')}, a PDF of {weight}")
                continue
            head = base64.b64decode(data[:32] + "=" * (-len(data[:32]) % 4))
            drawn = (f", {int.from_bytes(head[16:20], 'big')} × {int.from_bytes(head[20:24], 'big')} px"
                     if head.startswith(_PNG) and len(head) >= 24 else "")
            lines.append(f"- an image of {weight}{drawn}")
        return await self._turn(on_event, "I read what was attached before anything else.", text=(
            "**What I was given** (made up by the development admin):\n\n" + "\n".join(lines)
            + "\n\nAsk about any of them: they stay in this conversation, and each later request reads them again."))

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
        if subject and subject[0] == "rule":
            return await self._dossier(subject[1], subject[2], done, on_event)
        if subject and subject[0] == "draft":
            return await self._draft(subject[1], subject[2], done, on_event)
        if subject and subject[0] == "change":
            return await self._turn(on_event, "The change is shared whole: I weigh what a backtest can test.", text=(
                "**It cannot be told yet** (made up by the development admin): the setting it changes rests on 12 "
                "trades over 9 days at the new value, too few to tell it from the live one. Nothing is saved; save it "
                "from the page, or wait for more days."))
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
        if "show_chart" not in done:
            return await self._turn(on_event, "Its days say more drawn than in a table.", [("show_chart", {
                "title": "Its days, against random entries (made up)", "kind": "line", "unit": "%",
                "series": [{"key": "run", "label": "The run"}, {"key": "random", "label": "Random entries, median"}],
                "points": [{"t": f"2026-10-{day:02d}", "run": round(0.6 * (day - 12) - (day % 3), 2),
                            "random": round(0.1 * (day - 12), 2)} for day in range(13, 29) if day % 7 not in (3, 4)],
                "references": [{"value": 0, "label": "Even"}], "zero": True,
                "source": f"backtest_days of run {run_id} (made up by the development admin)"})])
        if "set_verdict" not in done:
            return await self._turn(on_event, "I say my verdict where the run is shown.", [("set_verdict", {
                "about": f"run:{run_id}", "tone": "mixed", "line": "Rests on too few days to be believed (made up)."})])
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
        if "propose_new_rule" not in done and done.get("propose_new_script", "").startswith("{"):
            made = json.loads(done["propose_new_script"]).get("proposal")
            # A new rule rests on finished runs: the development admin's newest stands for runs of its script.
            listed = self._backtests.list_runs(limit=20) if self._backtests is not None else []
            finished = [run["id"] for run in listed if (run.get("status") or {}).get("state") == "done"][:1]
            # Its first two alerts' titles, as the script declares them: the first long, the second short.
            titles = re.findall(r'alertcondition\([^,]+,\s*"([^"]+)"', script["source"])[:2] or ["Long", "Short"]
            if made and finished:
                return await self._turn(on_event, "And a rule of yours that runs it, in shadow.", [
                    ("propose_new_rule", {"name": f"{script['name']}, slower {secrets.token_hex(2)}", "after_proposal": made,
                                          "asset": "capital/US100", "indice": "us100", "bars": "5",
                                          "alerts": dict(zip(titles, ("long", "short"))), "runs": finished,
                                          "title": "Run it in shadow",
                                          "rationale": "Made up by the development admin: a week in shadow first."})])
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

    async def _dossier(self, rule_id: str, ready: bool, done: dict, on_event) -> list:
        if "set_verdict" not in done:
            verdict = ({"tone": "positive", "line": "Ready: its week in shadow done, its paper days in range (made up)."}
                       if ready else
                       {"tone": "mixed", "line": "Not yet: 3 of its 5 weekdays in shadow, its report on 9 trades (made up)."})
            return await self._turn(on_event, "Its paper days and its report say enough for a verdict.", [("set_verdict", {
                "about": f"rule:{rule_id}", **verdict})])
        if ready and "propose_action" not in done:
            return await self._turn(on_event, "It is ready, and going live is the operator's: I propose it.", [
                ("propose_action", {"action": "rule_live", "rule_id": rule_id, "title": "Take it live",
                                    "rationale": "Made up by the development admin: its week in shadow is done and "
                                                 "its paper days stayed inside its reference's range."})])
        if ready:
            return await self._turn(on_event, "I have its case.", text=(
                "## Ready\n\nIts week in shadow is done, its paper days stayed inside its reference's range and the "
                "engine runs it without fault (made up by the development admin). I proposed taking it live: **it "
                "waits for you**, through the page's own confirmation."))
        return await self._turn(on_event, "I have its case.", text=(
            "## Not ready yet\n\nIts paper days stay inside its reference's range, but they are **three**, and its "
            "report rests on 9 trades (made up by the development admin). Two more weekdays in shadow, then ask again."))

    async def _draft(self, script_id: str, base: int, done: dict, on_event) -> list:
        if "propose_script_change" not in done:
            script = self._scripts.script(script_id)
            source = script["source"].rstrip("\n") + "\n// Fixed by the AI agent (made up)\n"
            fix = self._scripts.create(f"{script['name']} fixed by Claude {secrets.token_hex(2)}", source, made_by="claude")
            return await self._turn(on_event, "The draft compiles; I fix one thing and propose it for your editor.", [
                ("propose_script_change", {"script_id": script_id, "base_version": script["version"],
                                           "from_script_id": fix["id"], "from_version": 1, "title": "One line fixed",
                                           "rationale": "Made up by the development admin: put it in, then save."})])
        return await self._turn(on_event, "I reviewed the draft.", text=(
            "## The draft\n\nIt **compiles**, and its alerts are unchanged (made up). I propose one line fixed: put "
            "it in your editor below, then save it there, or not."))

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
        if "ask_operator" not in done:
            return await self._turn(on_event, "Two directions, one budget: I ask which first.", [
                ("ask_operator", {"question": "Fast lines or slow ones first?", "choices": ["Fast", "Slow"],
                                  "why": "Each takes half the budget (made up by the development admin)."})])
        return await self._turn(on_event, "A first look is done: the task waits for the operator's answer.", text=(
            "I looked at the runs there are, and asked which direction first: the task goes on with your answer."))

    async def _task_going_on(self, done: dict, on_event) -> list:
        if "task_done" not in done:
            return await self._turn(on_event, "Enough to conclude within the budget.", [
                ("task_done", {"conclusion": "No run here rests on enough days to be believed (made up).",
                               "runs_kept": [], "next_steps": "Record more days, then run the strongest script again."})])
        return await self._turn(on_event, "The task is done.", text=(
            "## Conclusion\n\nNo run here rests on enough days to be believed. **Record more days**, then run the "
            "strongest script again over all of them."))

    async def _briefing(self, on_event) -> list:
        return await self._turn(on_event, "The day ahead is shared whole: I write its briefing.", text=(
            "An ordinary day ahead: the US market opens as usual, no release keeps entries out (made up by the "
            "development admin).\n\n1. **Today**: open from 15:30 Paris; no early close.\n2. **What runs**: no rule "
            "is live; one rule in shadow, two days short of its week.\n3. **What needs you**: Saxo's login lapses in "
            "three days.\n4. **Yesterday**: two trades, one won (its debrief)."))

    async def _triage(self, done: dict, on_event) -> list:
        if "ops_trading_now" not in done:
            return await self._turn(on_event, "Trading as the dashboard reads it, first.", [("ops_trading_now", {})])
        if "propose_action" not in done:
            return await self._turn(on_event, "The sale keeps being refused: I propose a pause while it is looked at.", [
                ("propose_action", {"action": "pause_entries", "title": "Pause new entries while the sale is refused",
                                    "rationale": "Made up by the development admin: Saxo refuses the sale again and "
                                                 "again; a new entry would add a second position to watch."})])
        return await self._turn(on_event, "I have what I need.", text=(
            "**Watch it.** Saxo keeps refusing the sale of one position (made up); the position monitor tries it again "
            "by itself. Nothing else is wrong. I proposed a pause of new entries while it lasts: it waits for you."))

    async def _debrief(self, subject, done: dict, on_event) -> list:
        day = subject[1] if subject and subject[0] == "trading day" else None
        if day and "set_verdict" not in done:
            return await self._turn(on_event, "The day is shared whole: its verdict first, where the Report shows it.", [
                ("set_verdict", {"about": f"day:{day}", "tone": "positive", "line": "A calm day, sold by its rules (made up)."})])
        return await self._turn(on_event, "I write its account.", text=(
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


def seed_routines(store, settings) -> None:
    """A weekly routine, so that the Routines page shows one at the start (made up)."""
    from src.admin.agent_routines import checked_fields

    if store.load()["routines"]:
        return
    store.save(checked_fields({"name": "Weekly edge check", "goal": "Run the reference runs again with the week's days, "
                               "and say which rule lost its edge.", "schedule": {"every": "week", "weekday": 5,
                                                                                 "time": "10:00"},
                               "budget": {"usd": 3, "backtests": 6, "hours": 2}}, settings),
               store.load()["revision"], "dev")


class FakeMorningBriefing(MorningBriefing):
    """The morning briefing at any hour: the day of today, its window now."""

    def _window(self, config: dict, now: float, minutes_before: int):
        return datetime.fromtimestamp(now, timezone.utc).date().isoformat(), now - 60, now + 3600


class FakeDailyDebrief(DailyDebrief):
    """The daily debrief at any hour: the fake's made-up market is closed, the last finished weekday debriefed."""

    def _market_is_open(self, config: dict, now: float) -> bool:
        return False
