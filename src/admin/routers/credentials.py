"""
The credentials kept sealed in PostgreSQL (src/vault/, ``ctx.vault``): what
each is and whether it is set, set or replaced, a token rotated, the Telegram
bot and the Claude API key tried, the TradingView webhook's address shown. Every change is audited
(its name and last four characters, never a value) and applies when the
services that read it start again.
"""

import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.admin.auth import client_ip, require_session
from src.admin.routers.actions import require_actions_enabled
from src.admin.routers.config import require_config_edit_enabled
from src.vault import credentials as catalog
from src.vault import store

router = APIRouter(prefix="/api/credentials", dependencies=[Depends(require_session)])

TELEGRAM_TEST = "🧪 WATA Admin: this bot works. Restart the services that send through it to use it."


class Parts(BaseModel):
    parts: dict[str, str] = Field(default_factory=dict)


def _vault(request: Request):
    vault = request.app.state.ctx.vault
    if vault is None:
        raise HTTPException(status_code=503, detail="The credentials are not available in this admin")
    return vault


def _said(name: str, fingerprint: str | None) -> str:
    return f"{catalog.BY_NAME[name].label}{f', ending …{fingerprint}' if fingerprint else ''}"


@router.get("")
async def credentials(request: Request):
    return await _vault(request).listed()


@router.put("/{name}", dependencies=[Depends(require_config_edit_enabled)])
async def set_credential(name: str, body: Parts, request: Request):
    """Set or replace a credential the operator enters; its services apply it at their next start."""
    ctx = request.app.state.ctx
    result = await _vault(request).set(name, body.parts, by=client_ip(request, ctx.trusted_proxies))
    ctx.audit(request, "credential set", f"{_said(name, result['fingerprint'])}; read by {', '.join(result['services'])}")
    return result


@router.post("/{name}/rotate", dependencies=[Depends(require_config_edit_enabled)])
async def rotate_credential(name: str, request: Request):
    """A new token, the one before still accepted for a while."""
    ctx = request.app.state.ctx
    result = await _vault(request).rotate(name, by=client_ip(request, ctx.trusted_proxies))
    ctx.audit(request, "token rotated", f"{_said(name, result['fingerprint'])}; read by {', '.join(result['services'])}")
    return result


@router.delete("/{name}", dependencies=[Depends(require_config_edit_enabled)])
async def remove_credential(name: str, request: Request):
    ctx = request.app.state.ctx
    if name not in catalog.BY_NAME:
        raise HTTPException(status_code=404, detail=f"{name} is not one of WATA's credentials")
    result = await _vault(request).remove(name)
    ctx.audit(request, "credential removed", f"{catalog.BY_NAME[name].label}; read by {', '.join(result['services'])}")
    return result


@router.post("/webhook.token/show", dependencies=[Depends(require_actions_enabled)])
async def show_webhook(request: Request):
    """The address TradingView's alerts are sent to, token included: shown on request, and audited."""
    ctx = request.app.state.ctx
    value = await _vault(request).open("webhook.token")
    if not value:
        raise HTTPException(status_code=404, detail="No TradingView webhook token is set")
    ctx.audit(request, "webhook address shown")
    domain = os.getenv("WATA_API_DOMAIN", "").strip()
    return {
        "token": value["token"],
        "url": f"https://{domain}/webhook?token={value['token']}" if domain else None,
        "path": f"/webhook?token={value['token']}",
    }


@router.post("/telegram.bot/test", dependencies=[Depends(require_actions_enabled)])
async def test_telegram(request: Request):
    """
    A message sent with the bot as stored now, by the admin itself: the bot
    works, whatever the telegram service still runs with.
    """
    ctx = request.app.state.ctx
    vault = _vault(request)
    value = await vault.open("telegram.bot")
    if not value:
        raise HTTPException(status_code=404, detail="No Telegram bot is set")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{value['token']}/sendMessage",
                json={"chat_id": value["chat_id"], "text": TELEGRAM_TEST},
            )
        ok = response.status_code == 200
        try:
            detail = "Sent" if ok else str(response.json().get("description") or response.status_code)[:200]
        except ValueError:
            detail = f"Answered {response.status_code}"
    except httpx.HTTPError as e:
        # Its text names the address, and the address the bot's token.
        ok, detail = False, f"Telegram could not be reached ({type(e).__name__})"
    try:
        await store.record_check(vault.writer, "telegram.bot", "admin", ok, detail)
    except Exception:
        pass
    ctx.audit(request, "Telegram bot tried", detail)
    return {"ok": ok, "detail": detail}


@router.post("/anthropic.api/test")
async def test_anthropic(request: Request):
    """
    The Claude API asked, with the key as stored now, for the model the AI agent
    is set to use: a read of the model's entry, which spends no token.
    """
    from src.admin.agent.provider import check_key

    ctx = request.app.state.ctx
    vault = _vault(request)
    value = await vault.open("anthropic.api")
    if not value:
        raise HTTPException(status_code=404, detail="No Claude API key is set")
    ok, detail = await check_key(value["key"], ctx.settings.ai_agent.model)
    try:
        await store.record_check(vault.writer, "anthropic.api", "admin", ok, detail)
    except Exception:
        pass
    return {"ok": ok, "detail": detail}
