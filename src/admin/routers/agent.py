"""
The AI agent of the pages (src/admin/agent/, ``ctx.agent``): its state, its
conversations and their stream, what they proposed, the operator's pause.
Off unless admin.ai_agent.enabled: its state says so, everything else answers
403. The agent reads, runs backtests and proposes, as Claude does through the
MCP endpoint; applying a script or a rule saves it through the stores' own
checks, which are not audited when made by hand either. A change of settings
is previewed and saved here with the Configuration editor, as the
Configuration page saves (config editing on, the file's revision, the audit):
the agent's package never reaches the editor.

Its memory (src/admin/agent_memory.py, ``ctx.agent_memory``) is read and changed
here by the operator, whether the agent is on or not: what the operator writes
is theirs (locked unless they say otherwise), every change is kept in the
memory's journal with who made it, and none is audited, as a script saved by
hand is not.
"""

import asyncio
import json
import secrets
import time
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.admin.agent_memory import KINDS, MAX_BODY, MAX_DESCRIPTION, MAX_ENTRIES, Author
from src.admin.agent.prompts import CONFIG_REVIEW_PROMPT, DRAFT_REVIEW_PROMPT, RULE_REVIEW_PROMPT
from src.admin.agent.proposals import testable_settings
from src.admin.agent.service import MAX_ATTACHED
from src.admin.agent.uploads import LARGEST, UnknownFile
from src.admin.agent.verdicts import MAX_ASKED
from src.admin.agent_jobs import read_config
from src.admin.agent_routines import RoutineConflict, checked_fields, next_turn
from src.admin.agent_telegram import ANSWER, TASK
from src.admin.auth import client_ip, require_session, session_cookie_name
from src.admin.config_editor import ConfigConflict, ConfigInvalid
from src.admin.routers.config import changes_said, require_config_edit_enabled
from src.admin.routers.ta import RuleFields
from src.admin.ta_monitor import rules_by_id
from src.admin.ta_rules import RuleStore
from src.admin.ta_scripts import MAX_SOURCE_BYTES

router = APIRouter(prefix="/api/agent", dependencies=[Depends(require_session)])

STREAM_POLL_SECONDS = 1.0
# What the configuration's journal says of a save made from a proposal (its history on the Configuration page).
AGENT_SAVE = "saved from an AI agent's proposal"
STREAM_KEEP_ALIVE_SECONDS = 15.0


class Subject(BaseModel):
    kind: str = Field(max_length=8)
    id: str = Field(max_length=64)
    version: int | None = None
    # A signal's day (YYYY-MM-DD), an incident's moment (YYYY-MM-DD HH:MM, UTC).
    day: str | None = Field(None, max_length=10)
    at: str | None = Field(None, max_length=16)


class Ask(BaseModel):
    prompt: str = Field(max_length=8000)
    # The request's model and effort; the conversation's last, or the settings', when not given.
    model: str | None = Field(None, max_length=40)
    effort: str | None = Field(None, max_length=10)
    # Said on Telegram once answered (src/admin/agent_telegram.py).
    tell: bool = False
    # What the operator attaches to it: subjects, and files staged (kind "file", their id).
    attach: list[Subject] | None = Field(None, max_length=MAX_ATTACHED)


class NewConversation(Ask):
    subject: Subject | None = None


class TaskBudget(BaseModel):
    usd: float
    backtests: int
    hours: float


class NewTask(BaseModel):
    goal: str = Field(max_length=8000)
    subject: Subject | None = None
    attach: list[Subject] | None = Field(None, max_length=MAX_ATTACHED)
    budget: TaskBudget
    model: str | None = Field(None, max_length=40)
    effort: str | None = Field(None, max_length=10)
    # Its end said on Telegram.
    tell: bool = False


class Review(BaseModel):
    # The operator's question; the review's own when not given.
    prompt: str | None = Field(None, max_length=8000)
    model: str | None = Field(None, max_length=40)
    effort: str | None = Field(None, max_length=10)
    tell: bool = False


class ConfigReview(Review):
    config: dict


class RuleReview(Review):
    # The rule changed; none for a new one.
    rule_id: str | None = Field(None, max_length=64)
    fields: RuleFields


class ScriptReview(Review):
    script_id: str = Field(max_length=64)
    base_version: int = Field(ge=1)
    source: str = Field(max_length=MAX_SOURCE_BYTES)


class Answer(BaseModel):
    answer: str = Field(max_length=4000)


class RoutineFields(BaseModel):
    # The routines' revision the page read: a change made meanwhile is never overwritten.
    revision: str = Field(max_length=64)
    name: str = Field(max_length=200)
    goal: str = Field(max_length=8000)
    schedule: dict
    budget: TaskBudget
    model: str | None = Field(None, max_length=40)
    effort: str | None = Field(None, max_length=10)
    telegram: bool = True
    enabled: bool = True


class Title(BaseModel):
    title: str = Field(max_length=200)


class Feedback(BaseModel):
    request: int
    value: Literal["like", "dislike"] | None = None


class Apply(BaseModel):
    # A change of settings: the revision of config.json its preview was read at.
    revision: str | None = Field(None, max_length=128)
    # A script's change from a draft reviewed: put in the operator's editor, unsaved, rather than saved.
    into_editor: bool = False


class MemoryEntry(BaseModel):
    # Held to the memory's own bounds by the store, which says why in a sentence; these only cap what is read.
    name: str = Field(max_length=80)
    kind: str = Field(max_length=20)
    description: str = Field(max_length=MAX_DESCRIPTION * 2)
    body: str = Field(max_length=MAX_BODY * 2)
    # What the operator writes is theirs unless they say otherwise: the agent reads it, never changes it.
    locked: bool = True


class MemoryChange(MemoryEntry):
    # The revision the page showed: a change made since is never written over unread.
    revision: int
    locked: bool | None = None


class MemoryLock(BaseModel):
    locked: bool


class MemoryRestore(BaseModel):
    seq: int


def _agent(request: Request):
    agent = request.app.state.ctx.agent
    if agent is None:
        raise HTTPException(status_code=403, detail="The AI agent is off: switch it on in Configuration › AI agent")
    return agent


def _by(request: Request) -> str:
    return client_ip(request, request.app.state.ctx.trusted_proxies)


# Whether a Telegram bot is set, read from the credentials at most this often (the pages read the state often).
TELEGRAM_SEEN_SECONDS = 60.0
_telegram_seen: dict = {}


async def _telegram(ctx) -> bool | None:
    """Whether the Telegram bot is set, as the Credentials page lists it; None when it cannot be told."""
    if ctx.vault is None:
        return None
    if _telegram_seen.get("vault") is ctx.vault and time.monotonic() - _telegram_seen["at"] < TELEGRAM_SEEN_SECONDS:
        return _telegram_seen["set"]
    try:
        listed = await ctx.vault.listed()
    except Exception:
        return None
    entry = next((item for item in listed.get("items") or [] if item.get("name") == "telegram.bot"), {})
    found = entry.get("where") in ("database", "old place")
    _telegram_seen.update(vault=ctx.vault, at=time.monotonic(), set=found)
    return found


@router.get("")
async def state(request: Request):
    """Whether the agent is on, the key set, the month's spending, its limits and what it is working on."""
    ctx = request.app.state.ctx
    if ctx.agent is None:
        return {"enabled": False, "model": ctx.settings.ai_agent.model}
    said = await run_in_threadpool(ctx.agent.state)
    said["key"] = await ctx.agent.has_key()
    # Whether "Tell me on Telegram" can be said: a Telegram bot is set.
    said["telegram"] = await _telegram(ctx)
    return said


@router.post("/pause")
async def pause(request: Request):
    """No new request until resumed; those under way go on to their end."""
    return {"pause": await run_in_threadpool(_agent(request).set_pause, True, _by(request))}


@router.post("/resume")
async def resume(request: Request):
    return {"pause": await run_in_threadpool(_agent(request).set_pause, False, _by(request))}


@router.get("/conversations")
async def conversations(request: Request, subject: str | None = Query(None, max_length=80)):
    """Every conversation, the last changed first; only those about ``subject`` (run:<id>, script:<id>) when given."""
    return {"items": await run_in_threadpool(_agent(request).conversations, subject)}


async def _tell(request: Request, meta: dict, kind: str) -> dict:
    """What becomes of the request just asked (or of the task) said on Telegram once it ends."""
    teller = request.app.state.ctx.teller
    if teller is not None:
        await run_in_threadpool(teller.add, meta["id"], kind, request=meta["request"] if kind == ANSWER else None)
    return meta


def _attach(body) -> list[dict] | None:
    return [entry.model_dump() for entry in body.attach] if body.attach else None


@router.post("/conversations", status_code=201)
async def create(body: NewConversation, request: Request):
    subject = body.subject.model_dump() if body.subject is not None else None
    meta = await _agent(request).create(subject, body.prompt, _by(request), body.model, body.effort,
                                        attach=_attach(body))
    return await _tell(request, meta, ANSWER) if body.tell else meta


@router.post("/tasks", status_code=201)
async def create_task(body: NewTask, request: Request):
    """A research task: a conversation the agent works on alone, request after request, within its budget."""
    subject = body.subject.model_dump() if body.subject is not None else None
    meta = await _agent(request).create_task(body.goal, subject, _by(request), body.budget.model_dump(), body.model,
                                             body.effort, attach=_attach(body))
    return await _tell(request, meta, TASK) if body.tell else meta


# ── files of the operator's computer, attached to a request (src/admin/agent/uploads.py) ──

@router.post("/files", status_code=201)
async def stage_file(request: Request, file: UploadFile = File(...)):
    """A file chosen, checked and kept until the request that sends it (an hour at most): what it is."""
    agent = _agent(request)
    too_large = HTTPException(status_code=413, detail=f"A file is {LARGEST // (1024 * 1024)} MB at most")
    declared = request.headers.get("content-length") or ""
    if declared.isdigit() and int(declared) > LARGEST + 64 * 1024:
        raise too_large
    data = await file.read(LARGEST + 1)
    if len(data) > LARGEST:
        raise too_large
    return await run_in_threadpool(agent.uploads.stage, file.filename or "file", data)


@router.delete("/files/{file_id}", status_code=204)
async def unstage_file(file_id: str, request: Request):
    """A file chosen and not sent, removed (its chip's ×)."""
    try:
        await run_in_threadpool(_agent(request).uploads.remove, file_id)
    except UnknownFile:
        raise HTTPException(status_code=404, detail="No such file waiting to be sent") from None
    return Response(status_code=204)


@router.get("/conversations/{conversation_id}/files/{file_id}")
async def attached_file(conversation_id: str, file_id: str, request: Request):
    """A file attached to a conversation, as it was sent: an image shown, any other downloaded."""
    try:
        entry, data = await run_in_threadpool(_agent(request).attachment, conversation_id, file_id)
    except UnknownFile:
        raise HTTPException(status_code=404, detail="No such file in this conversation") from None
    image = entry.get("type") == "image"
    return Response(content=data, media_type="text/plain; charset=utf-8" if entry.get("type") == "text"
                    else entry["media_type"], headers={
                        "Content-Disposition": f"{'inline' if image else 'attachment'}; "
                                               f"filename*=UTF-8''{quote(entry['name'])}",
                        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"})


# ── a change reviewed before it is saved: nothing is saved here ──

# A rule's parts a review says the change of.
_RULE_PARTS = ("name", "asset", "side", "bars", "script", "inputs", "alerts", "alert_calls", "indice", "history_days")
MAX_REVIEWED = 100


def _part(rule: dict | None, part: str):
    """A rule's part as a save compares it: its script by id and version, an input by id and value, nothing empty."""
    value = (rule or {}).get(part)
    if part == "script" and isinstance(value, dict):
        return {"id": value.get("id"), "version": value.get("version")}
    if part == "inputs":
        return [{"id": entry.get("id"), "value": entry.get("value")} for entry in value or []]
    if part == "alerts":
        return [{"title": entry.get("title"), "action": entry.get("action")} for entry in value or []]
    if isinstance(value, dict):
        return {key: item for key, item in value.items() if item is not None}
    return value


async def _reviewed(request: Request, body: Review, subject: dict, prompt: str, files: dict | None = None) -> dict:
    meta = await _agent(request).create(subject, body.prompt or prompt, _by(request), body.model, body.effort,
                                        files=files)
    return await _tell(request, meta, ANSWER) if body.tell else meta


@router.post("/reviews/config", status_code=201)
async def review_config(body: ConfigReview, request: Request):
    """
    The agent asked to review a change of config.json before the operator
    saves it: the change as the Configuration page previews it (values
    masked), which of its settings a backtest can test, the services it
    restarts and its warnings. Nothing is saved.
    """
    ctx = request.app.state.ctx
    _agent(request)
    rules = await run_in_threadpool(rules_by_id, ctx.ta_scripts)
    live_indices = sorted({rule["indice"] for rule in rules.values() if rule.get("mode") == "live"})
    try:
        preview = await run_in_threadpool(ctx.config_editor.preview, body.config, live_indices)
    except ConfigInvalid as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    changes = preview["changes"]
    if not changes:
        raise HTTPException(status_code=422, detail="Nothing changed: there is nothing to review")
    subject = {"kind": "change", "id": secrets.token_hex(4),
               "label": f"a change of config.json, {len(changes)} key{'s' if len(changes) != 1 else ''}",
               "change": {"of": "config", "changes": changes[:MAX_REVIEWED], "services": preview["impact"]["services"],
                          "warnings": preview["warnings"], "testable": testable_settings(changes)}}
    return await _reviewed(request, body, subject, CONFIG_REVIEW_PROMPT)


@router.post("/reviews/rule", status_code=201)
async def review_rule(body: RuleReview, request: Request):
    """
    The agent asked to review a change of a signal rule (or a new one) before
    the operator saves it: what it changes, the rule as it would be. Its
    script and libraries are pinned as a save pins them. Nothing is saved.
    """
    ctx = request.app.state.ctx
    _agent(request)
    store = RuleStore(ctx.ta_scripts)
    loaded = await run_in_threadpool(store.load)
    before = RuleStore.find(loaded["rules"], body.rule_id) if body.rule_id else None
    after = body.fields.fields()
    await run_in_threadpool(store.pin, after["script"]["id"], after["script"]["version"])
    changes = [{"part": part, "before": _part(before, part), "after": _part(after, part)} for part in _RULE_PARTS
               if before is None or json.dumps(_part(before, part), sort_keys=True, default=str)
               != json.dumps(_part(after, part), sort_keys=True, default=str)]
    if before is not None and not changes:
        raise HTTPException(status_code=422, detail="Nothing changed: there is nothing to review")
    rule = {key: before[key] for key in ("id", "name", "mode")} if before else None
    subject = {"kind": "change", "id": secrets.token_hex(4),
               "label": f"a change of the rule {before['name']}" if before else f"a new rule, {after['name']}",
               "change": {"of": "rule", "rule": rule, "changes": changes, "after": after}}
    return await _reviewed(request, body, subject, RULE_REVIEW_PROMPT)


@router.post("/reviews/script", status_code=201)
async def review_script(body: ScriptReview, request: Request):
    """
    The agent asked to review an unsaved draft of a script, made on one of
    its versions: kept with the conversation, its difference and its check
    shared. Nothing is saved; a fix the agent proposes is put in the editor.
    """
    ctx = request.app.state.ctx
    _agent(request)
    script = await run_in_threadpool(ctx.ta_scripts.script, body.script_id)
    subject = {"kind": "draft", "id": secrets.token_hex(4), "label": f"a draft of {script['name']}",
               "script_id": script["id"], "base_version": body.base_version}
    return await _reviewed(request, body, subject, DRAFT_REVIEW_PROMPT, files={"draft.pine": body.source})


# ── routines: research tasks that come back by themselves ──


def _routines(request: Request):
    _agent(request)
    routines = request.app.state.ctx.routines
    if routines is None:
        raise HTTPException(status_code=503, detail="The routines are not available")
    return routines


async def _routine_saved(request: Request, body: RoutineFields, routine_id: str | None = None) -> dict:
    ctx = request.app.state.ctx
    routines = _routines(request)
    fields = checked_fields(body.model_dump(exclude={"revision"}), ctx.agent.settings)
    try:
        return await run_in_threadpool(routines.store.save, fields, body.revision, _by(request), routine_id,
                                       ctx.agent.settings.max_routines)
    except RoutineConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


@router.get("/routines")
async def routines_list(request: Request):
    """Each routine, its last runs and when it next comes back (the market's close may still hold it back)."""
    ctx = request.app.state.ctx
    routines = _routines(request)
    loaded = await run_in_threadpool(routines.store.load)
    config = await run_in_threadpool(read_config, ctx.settings.config_path)
    now = time.time()
    return {**loaded, "max": ctx.agent.settings.max_routines,
            "routines": [{**routine, "next": next_turn(routine["schedule"], config, now)}
                         for routine in loaded["routines"]]}


@router.post("/routines", status_code=201)
async def routine_create(body: RoutineFields, request: Request):
    return await _routine_saved(request, body)


@router.put("/routines/{routine_id}")
async def routine_change(routine_id: str, body: RoutineFields, request: Request):
    return await _routine_saved(request, body, routine_id)


@router.delete("/routines/{routine_id}")
async def routine_remove(routine_id: str, request: Request, revision: str = Query(..., max_length=64)):
    try:
        return await run_in_threadpool(_routines(request).store.remove, routine_id, revision)
    except RoutineConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


@router.post("/routines/{routine_id}/run", status_code=201)
async def routine_run(routine_id: str, request: Request):
    """A routine's task started now, whatever the hour: its conversation."""
    return {"conversation": await _routines(request).run_now(routine_id)}


@router.post("/conversations/{conversation_id}/task/answer")
async def answer_task(conversation_id: str, body: Answer, request: Request):
    """The operator's answer to the question a research task asked: it goes on, its next request starting with it."""
    return await run_in_threadpool(_agent(request).answer_question, conversation_id, body.answer, _by(request))


@router.post("/conversations/{conversation_id}/task/stop")
async def stop_task(conversation_id: str, request: Request):
    """No request of the task follows; the one under way stops between two tool calls."""
    return await run_in_threadpool(_agent(request).stop_task, conversation_id, _by(request))


@router.get("/context")
async def context(request: Request, model: str | None = Query(None, max_length=40)):
    """A new conversation's context window: the system prompt and the tools, before anything is asked."""
    return await _agent(request).context(None, model)


@router.get("/conversations/{conversation_id}/context")
async def conversation_context(conversation_id: str, request: Request, model: str | None = Query(None, max_length=40)):
    """What the conversation's next request would put in the model's context window, part by part."""
    return await _agent(request).context(conversation_id, model)


@router.get("/conversations/{conversation_id}")
async def conversation(conversation_id: str, request: Request):
    return await run_in_threadpool(_agent(request).conversation, conversation_id)


@router.patch("/conversations/{conversation_id}")
async def rename(conversation_id: str, body: Title, request: Request):
    return await run_in_threadpool(_agent(request).rename, conversation_id, body.title)


@router.delete("/conversations/{conversation_id}", status_code=204)
async def remove(conversation_id: str, request: Request):
    await run_in_threadpool(_agent(request).remove, conversation_id)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/requests")
async def ask(conversation_id: str, body: Ask, request: Request):
    meta = await _agent(request).ask(conversation_id, body.prompt, _by(request), body.model, body.effort,
                                     attach=_attach(body))
    return await _tell(request, meta, ANSWER) if body.tell else meta


@router.post("/conversations/{conversation_id}/stop")
async def stop(conversation_id: str, request: Request):
    """Stopped between two tool calls: a backtest started keeps running, a script saved stays."""
    return await run_in_threadpool(_agent(request).stop, conversation_id)


@router.post("/conversations/{conversation_id}/retry")
async def retry(conversation_id: str, request: Request):
    return await run_in_threadpool(_agent(request).retry, conversation_id)


@router.post("/conversations/{conversation_id}/feedback", status_code=204)
async def feedback(conversation_id: str, body: Feedback, request: Request):
    await run_in_threadpool(_agent(request).feedback, conversation_id, body.request, body.value)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/proposals/{proposal_id}/apply")
async def apply(conversation_id: str, proposal_id: str, request: Request, body: Apply | None = None):
    """
    The proposal applied: a new version of the script, a new script, the rule
    changed (refused once it is live), or settings saved to config.json as the
    Configuration page saves them (``revision``: the one its preview was read at).
    """
    agent = _agent(request)
    if body is not None and body.into_editor:
        return await run_in_threadpool(agent.into_editor, conversation_id, proposal_id, _by(request))
    if body is None or body.revision is None:
        return await run_in_threadpool(agent.settle, conversation_id, proposal_id, True, _by(request))
    return await _save_settings(request, agent, conversation_id, proposal_id, body.revision)


async def _proposed_config(request: Request, agent, conversation_id: str, proposal_id: str) -> tuple[dict, dict, str]:
    """The change of settings, the configuration with it (as the editor loads it, secrets masked), and its revision."""
    from src.admin.agent.proposals import Proposals

    proposal = await run_in_threadpool(agent.settings_proposal, conversation_id, proposal_id)
    loaded = await run_in_threadpool(request.app.state.ctx.config_editor.load)
    return proposal, Proposals.settings_config(proposal, loaded["config"]), loaded["revision"]


@router.post("/conversations/{conversation_id}/proposals/{proposal_id}/preview",
             dependencies=[Depends(require_config_edit_enabled)])
async def preview(conversation_id: str, proposal_id: str, request: Request):
    """A change of settings as a save of the Configuration page is previewed: each key, what it reaches, warnings."""
    ctx = request.app.state.ctx
    _, config, revision = await _proposed_config(request, _agent(request), conversation_id, proposal_id)
    rules = await run_in_threadpool(rules_by_id, getattr(ctx, "ta_scripts", None))
    live_indices = sorted({rule["indice"] for rule in rules.values() if rule.get("mode") == "live"})
    try:
        said = await run_in_threadpool(ctx.config_editor.preview, config, live_indices)
    except ConfigInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"revision": revision, **said}


async def _save_settings(request: Request, agent, conversation_id: str, proposal_id: str, revision: str) -> dict:
    require_config_edit_enabled(request)
    ctx = request.app.state.ctx
    proposal, config, _ = await _proposed_config(request, agent, conversation_id, proposal_id)
    by = _by(request)
    try:
        # Refused (409) when the file was saved since the preview: its revision moved on.
        saved = await run_in_threadpool(ctx.config_editor.save, config, revision, by, AGENT_SAVE)
    except ConfigInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ConfigConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    ctx.audit(request, "configuration saved", f"From the AI agent's proposal {proposal['title']!r}. "
                                              f"{changes_said(saved)} Previous version kept as {saved['backup']}.")
    settled = await run_in_threadpool(agent.settings_saved, conversation_id, proposal_id, by, saved)
    return {**settled, "impact": saved["impact"], "restart_required": bool(saved["impact"]["services"])}


@router.post("/conversations/{conversation_id}/proposals/{proposal_id}/dismiss")
async def dismiss(conversation_id: str, proposal_id: str, request: Request):
    return await run_in_threadpool(_agent(request).settle, conversation_id, proposal_id, False, _by(request))


@router.get("/conversations/{conversation_id}/stream")
async def stream(conversation_id: str, request: Request):
    """
    Server-sent events of one conversation: ``changed`` (read it again), ``delta``
    (a piece of text, at its offset in its block) and ``writing`` (a tool call's
    input growing). Built as /api/events: the stream asks whether the browser
    left, and ends with the session.
    """
    ctx = request.app.state.ctx
    agent = _agent(request)
    token = request.cookies.get(session_cookie_name(ctx.settings.cookie_secure))
    queue = agent.subscribe(conversation_id)

    async def frames():
        try:
            yield "retry: 3000\n\n"
            yield "event: connected\ndata: {}\n\n"
            quiet_since = time.monotonic()
            while ctx.sessions.is_alive(token, ctx.credentials.version()) and not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), STREAM_POLL_SECONDS)
                except asyncio.TimeoutError:
                    if time.monotonic() - quiet_since >= STREAM_KEEP_ALIVE_SECONDS:
                        quiet_since = time.monotonic()
                        yield ": keep-alive\n\n"
                    continue
                quiet_since = time.monotonic()
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            agent.unsubscribe(conversation_id, queue)

    return StreamingResponse(frames(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@router.get("/verdicts")
async def verdicts(request: Request, about: list[str] = Query(default=[], max_length=MAX_ASKED)):
    """What the agent last concluded on each of these things (run:<id>, rule:<id>, day:<date>, position:<id>)."""
    agent = _agent(request)
    if agent.verdicts is None:
        return {"items": {}}
    return {"items": await run_in_threadpool(agent.verdicts.of, [entry for entry in about if len(entry) <= 80])}


@router.get("/artefacts")
async def artefacts(request: Request):
    """What the conversations made: runs, Claude's scripts, experiments, proposals and what became of them."""
    return {"items": await run_in_threadpool(_agent(request).artefacts)}


# ── its memory ──

def _memory(request: Request):
    memory = request.app.state.ctx.agent_memory
    if memory is None:
        raise HTTPException(status_code=404, detail="The AI agent's memory is not kept here")
    return memory


def _operator(request: Request) -> Author:
    return Author.operator(_by(request))


@router.get("/memory")
async def memory_state(request: Request):
    """Every entry whole, the pause of the agent's changes, whether the agent uses its memory, and the bounds."""
    ctx = request.app.state.ctx
    memory = _memory(request)
    return {
        "entries": await run_in_threadpool(memory.entries), "pause": await run_in_threadpool(memory.pause),
        # The agent on and its memory with it: off, the entries are kept and Claude Code still reads them.
        "agent": ctx.agent is not None, "used": ctx.agent is not None and ctx.agent.memory is not None,
        "kinds": list(KINDS), "limits": {"entries": MAX_ENTRIES, "description": MAX_DESCRIPTION, "body": MAX_BODY},
    }


@router.get("/memory/history")
async def memory_history(request: Request, name: str | None = Query(None, max_length=80)):
    """The memory's changes, newest first (only one entry's when ``name`` is given), with their conversations' titles."""
    ctx = request.app.state.ctx
    lines = await run_in_threadpool(_memory(request).history, name)
    titles = {}
    if ctx.agent is not None:
        titles = {meta["id"]: meta.get("title") for meta in await run_in_threadpool(ctx.agent.store.metas)}
    return {"items": [{**line, "title_of": titles.get(line.get("conversation"))} for line in lines]}


@router.post("/memory", status_code=201)
async def memory_create(body: MemoryEntry, request: Request):
    memory, author = _memory(request), _operator(request)
    entry, _ = await run_in_threadpool(lambda: memory.save(
        author, body.name, kind=body.kind, description=body.description, body=body.body, locked=body.locked,
        create=True))
    return entry


@router.put("/memory/{entry}")
async def memory_change(entry: str, body: MemoryChange, request: Request):
    """An entry changed, or renamed to ``body.name``, from the revision the page showed (409 once it moved on)."""
    memory, author = _memory(request), _operator(request)
    changed, _ = await run_in_threadpool(lambda: memory.save(
        author, entry, kind=body.kind, description=body.description, body=body.body, revision=body.revision,
        locked=body.locked, new_name=body.name))
    return changed


@router.post("/memory/{entry}/lock")
async def memory_lock(entry: str, body: MemoryLock, request: Request):
    memory, author = _memory(request), _operator(request)
    return await run_in_threadpool(memory.set_lock, author, entry, body.locked)


@router.delete("/memory/{entry}", status_code=204)
async def memory_forget(entry: str, request: Request, revision: int = Query(...)):
    """Forgotten from the revision the page showed: the history keeps it, and it may come back."""
    memory, author = _memory(request), _operator(request)
    await run_in_threadpool(lambda: memory.forget(author, entry, revision=revision))
    return Response(status_code=204)


@router.post("/memory/restore")
async def memory_restore(body: MemoryRestore, request: Request):
    """An entry as a line of the history left it, back: a version, or one forgotten."""
    memory, author = _memory(request), _operator(request)
    return await run_in_threadpool(memory.restore, author, body.seq)


@router.post("/memory/pause")
async def memory_pause(request: Request):
    """The agent reads its memory and changes nothing until resumed: at once, whatever is under way."""
    memory, author = _memory(request), _operator(request)
    return {"pause": await run_in_threadpool(memory.set_pause, author, True)}


@router.post("/memory/resume")
async def memory_resume(request: Request):
    memory, author = _memory(request), _operator(request)
    return {"pause": await run_in_threadpool(memory.set_pause, author, False)}
