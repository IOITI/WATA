"""
Where Saxo sends the browser back after a login (the server's redirect,
``SAXO_REDIRECT_URL``, registered in the app): ``/api/saxo/callback``.

No session is asked: the browser arrives from Saxo's site, which the session
cookie (SameSite strict) is never sent from. The state is what admits it: made
by a signed-in operator's "Authorise", kept hashed for ten minutes, used once
(src/admin/saxo.py). The code is then sealed for the services that log in to
Saxo and the browser sent back to the System page, which says what came of it.
"""

import logging
import urllib.parse

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from src.admin.auth import client_ip
from src.admin.errors import InvalidRequest, NotPossible

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/saxo")


@router.get("/callback", include_in_schema=False)
async def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    ctx = request.app.state.ctx
    if error or not code or not state:
        said = error or "no code"
        logger.warning("Saxo sent the browser back without a code (%s)", said[:100])
        return RedirectResponse(f"/system?saxo=refused&why={urllib.parse.quote(said[:100])}", status_code=303)
    try:
        environment = await ctx.saxo.hand_over(code, state=state, by=client_ip(request, ctx.trusted_proxies))
    except (InvalidRequest, NotPossible) as e:
        return RedirectResponse(f"/system?saxo=refused&why={urllib.parse.quote(str(e)[:200])}", status_code=303)
    ctx.audit(request, "Saxo authorization code received", f"{environment} account, through the admin's callback")
    return RedirectResponse("/system?saxo=handed-over", status_code=303)
