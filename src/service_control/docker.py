"""
The control service's only way to Docker: two calls of the Engine's API,
through its socket, on the containers of WATA's own list (containers.py), and
the ping that says which version of the API to ask them at.

Mounting the socket read-only restricts nothing: whoever reaches it can ask
Docker anything, a container that mounts the host included. So the calls are
fixed here, and a container is checked by its compose labels and its image
before it is restarted. Nothing else of Docker (exec, create, remove, images,
volumes, networks) can be asked through this.
"""

import re

import httpx

SOCKET = "/var/run/docker.sock"
# The ping says the version of the API the daemon serves, which every call is then asked at, as
# Docker's own client does: a version written here would one day be refused as too old (Docker 29
# refuses those below 1.44). What is read of a container has kept its shape for years.
PING = ("GET", "/_ping")
ALLOWED_CALLS = (
    PING,
    ("GET", "/containers/{name}/json"),
    ("POST", "/containers/{name}/restart"),
)
_API_VERSION = re.compile(r"^1\.\d{1,3}$")
# A restart waits for the container's own stop_grace_period (60 s for the trader), then for its start.
RESTART_TIMEOUT_SECONDS = 150
INSPECT_TIMEOUT_SECONDS = 10
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
IMAGE_PREFIX = "wata-base:"


class DockerError(Exception):
    """Docker did not do what was asked: its status and what it said."""


class Docker:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, socket_path: str = SOCKET):
        self._client = httpx.AsyncClient(
            transport=transport or httpx.AsyncHTTPTransport(uds=socket_path),
            # The host is never used: the socket is.
            base_url="http://docker",
        )
        self._api_version: str | None = None

    async def api_version(self) -> str:
        """The version of the Engine's API the daemon serves, asked once."""
        if self._api_version is None:
            response = await self._client.request(*PING, timeout=INSPECT_TIMEOUT_SECONDS)
            version = response.headers.get("api-version", "")
            if response.status_code != 200 or not _API_VERSION.match(version):
                raise DockerError(f"Docker's ping answered {response.status_code} without the version of its API")
            self._api_version = version
        return self._api_version

    async def _call(self, method: str, template: str, name: str, timeout: float) -> httpx.Response:
        if (method, template) not in ALLOWED_CALLS or (method, template) == PING:
            raise ValueError(f"Not a call the control service makes: {method} {template}")
        if not _NAME.match(name):
            raise ValueError(f"Not a container name of WATA's: {name!r}")
        version = await self.api_version()
        return await self._client.request(method, f"/v{version}{template.format(name=name)}", timeout=timeout)

    async def inspect(self, name: str) -> dict | None:
        """What Docker knows of the container ``name``; None when there is none (its profile is off)."""
        response = await self._call("GET", "/containers/{name}/json", name, INSPECT_TIMEOUT_SECONDS)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise DockerError(f"Docker answered {response.status_code} for {name}: {response.text[:200]}")
        return response.json()

    async def restart(self, name: str) -> None:
        """
        Stop then start ``name``. No ``t`` is given: Docker waits the
        container's own stop_grace_period, so that the trader finishes the
        signal in hand and the monitor the sale under way.
        """
        response = await self._call("POST", "/containers/{name}/restart", name, RESTART_TIMEOUT_SECONDS)
        if response.status_code != 204:
            raise DockerError(f"Docker answered {response.status_code} for {name}: {response.text[:200]}")

    async def close(self) -> None:
        await self._client.aclose()


def is_watas(name: str, inspected: dict) -> bool:
    """Whether the container is the one of WATA's compose file it is named for, on WATA's image."""
    config = inspected.get("Config") or {}
    labels = config.get("Labels") or {}
    return labels.get(COMPOSE_SERVICE_LABEL) == name and str(config.get("Image", "")).startswith(IMAGE_PREFIX)


def state_of(service: str, container: str, inspected: dict | None) -> dict:
    """A container as the admin shows it: whether it runs, since when, how often it restarted, why it stopped."""
    if inspected is None:
        return {"service": service, "container": container, "state": "absent"}
    state = inspected.get("State") or {}
    return {
        "service": service,
        "container": container,
        "state": state.get("Status", "unknown"),
        "running": bool(state.get("Running")),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "exit_code": state.get("ExitCode"),
        "oom_killed": bool(state.get("OOMKilled")),
        "restart_count": inspected.get("RestartCount", 0),
        "image": (inspected.get("Config") or {}).get("Image"),
    }
