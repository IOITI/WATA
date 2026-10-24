"""
The admin's way to the control service (src/service_control/), which holds
Docker's socket so that the admin never does: what each of WATA's containers
is doing, and a restart of one. Reached on control-net with the token both are
given as a compose secret; without it (the admin started alone, the lab, the
fake admin), the control service is said to be unavailable.
"""

import os
import time

import httpx

from src.admin.errors import ServiceUnavailable

URL = "http://service_control1:8090"
TOKEN_FILE = "/run/secrets/service_control_token"
TIMEOUT_SECONDS = 15
# What the pages ask on every load (the top bar's "Restart needed") is answered from the last answer this young.
RECENT_SECONDS = 10.0


class RestartRefused(Exception):
    """The control service did not take the restart: its status (404, 409, 429...) and why."""

    def __init__(self, status: int, detail: str, retry_after: int | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.retry_after = retry_after


class ServiceControl:
    def __init__(self, url: str = URL, token_file: str = TOKEN_FILE, transport: httpx.AsyncBaseTransport | None = None,
                 clock=time.monotonic):
        self._url = url
        self._token_file = token_file
        self._client = httpx.AsyncClient(base_url=url, timeout=TIMEOUT_SECONDS, transport=transport)
        self._clock = clock
        self._last: tuple[float, list[dict]] | None = None

    @property
    def available(self) -> bool:
        """Whether this admin was given the token: started with the control service, by the compose file."""
        return os.path.exists(self._token_file)

    def _headers(self) -> dict:
        try:
            with open(self._token_file, encoding="utf-8") as token_file:
                return {"Authorization": f"Bearer {token_file.read().strip()}"}
        except OSError as e:
            raise ServiceUnavailable(f"control service: no token ({type(e).__name__})") from None

    async def services(self, max_age: float = 0) -> list[dict]:
        """
        Each of WATA's containers: whether it runs, since when, how often it
        restarted, the last restart asked. The last answer when it is not older
        than ``max_age`` seconds.
        """
        if max_age and self._last is not None and self._clock() - self._last[0] <= max_age:
            return self._last[1]
        try:
            response = await self._client.get("/services", headers=self._headers())
        except httpx.HTTPError as e:
            raise ServiceUnavailable(f"control service: {type(e).__name__}") from None
        if response.status_code != 200:
            raise ServiceUnavailable(f"control service answered {response.status_code}")
        found = response.json()["services"]
        self._last = (self._clock(), found)
        return found

    async def restart(self, service: str) -> dict:
        """Ask for a restart of ``service``; it runs in the control service, this returns once it is taken."""
        self._last = None
        try:
            response = await self._client.post(f"/services/{service}/restart", headers=self._headers())
        except httpx.HTTPError as e:
            raise ServiceUnavailable(f"control service: {type(e).__name__}") from None
        if response.status_code == 202:
            return response.json()
        try:
            detail = response.json().get("detail") or response.text
        except ValueError:
            detail = response.text
        retry_after = response.headers.get("retry-after")
        raise RestartRefused(response.status_code, str(detail), int(retry_after) if retry_after and retry_after.isdigit() else None)

    async def close(self) -> None:
        await self._client.aclose()
