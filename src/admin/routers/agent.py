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
"""

import asyncio
import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.admin.auth import client_ip, require_session, session_cookie_name
from src.admin.config_editor import ConfigConflict, ConfigInvalid
from src.admin.routers.config import changes_said, require_config_edit_enabled
from src.admin.ta_monitor import rules_by_id

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


class NewConversation(Ask):
    subject: Subject | None = None


class TaskBudget(BaseModel):
    usd: float
    backtests: int
    hours: float


class NewTask(BaseModel):
    goal: str = Field(max_length=8000)
    subject: Subject | None = None
    budget: TaskBudget
    model: str | None = Field(None, max_length=40)
    effort: str | None = Field(None, max_length=10)


class Title(BaseModel):
    title: str = Field(max_length=200)


class Feedback(BaseModel):
    request: int
    value: Literal["like", "dislike"] | None = None


class Apply(BaseModel):
    # A change of settings: the revision of config.json its preview was read at.
    revision: str | None = Field(None, max_length=128)


def _agent(request: Request):
    agent = request.app.state.ctx.agent
    if agent is None:
        raise HTTPException(status_code=403, detail="The AI agent is off: switch it on in Configuration › AI agent")
    return agent


def _by(request: Request) -> str:
    return client_ip(request, request.app.state.ctx.trusted_proxies)


@router.get("")
async def state(request: Request):
    """Whether the agent is on, the key set, the month's spending, its limits and what it is working on."""
    ctx = request.app.state.ctx
    if ctx.agent is None:
        return {"enabled": False, "model": ctx.settings.ai_agent.model}
    said = await run_in_threadpool(ctx.agent.state)
    said["key"] = await ctx.agent.has_key()
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


@router.post("/conversations", status_code=201)
async def create(body: NewConversation, request: Request):
    subject = body.subject.model_dump() if body.subject is not None else None
    return await _agent(request).create(subject, body.prompt, _by(request), body.model, body.effort)


@router.post("/tasks", status_code=201)
async def create_task(body: NewTask, request: Request):
    """A research task: a conversation the agent works on alone, request after request, within its budget."""
    subject = body.subject.model_dump() if body.subject is not None else None
    return await _agent(request).create_task(body.goal, subject, _by(request), body.budget.model_dump(), body.model,
                                             body.effort)


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
    return await _agent(request).ask(conversation_id, body.prompt, _by(request), body.model, body.effort)


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


@router.get("/artefacts")
async def artefacts(request: Request):
    """What the conversations made: runs, Claude's scripts, experiments, proposals and what became of them."""
    return {"items": await run_in_threadpool(_agent(request).artefacts)}
