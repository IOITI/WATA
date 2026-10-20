"""
The lab's admin: WATA's own admin application run on this machine, over the
lab's database and folders (lab/config.py), and the client the lab's commands
reach it with.

It is the one that runs the backtests: the lab's commands ask it for them
(the same request as the Backtests form) and read what they found from its
folders, so that a run started here or from its pages is the same run, taken
in turn by the same service.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from lab import config, db, paths

PID_FILE = "admin.pid"
# The Pine runner's confinement needs it (admin_ui/.nvmrc).
LEAST_NODE = 24


def _node_major(node: str) -> int:
    try:
        answer = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    found = re.match(r"v(\d+)\.", answer.strip())
    return int(found.group(1)) if found else 0


def prefer_node(home: Path | None = None) -> str | None:
    """
    The Node the Pine runner gets: the one on the PATH when it is recent
    enough, else nvm's newest that is, put first on the PATH of this process
    and of what it starts. None when there is none.
    """
    found = shutil.which("node")
    if found and _node_major(found) >= LEAST_NODE:
        return found
    candidates = sorted(
        (home or Path.home()).glob(".nvm/versions/node/v*/bin/node"),
        key=lambda path: [int(part) for part in re.findall(r"\d+", path.parent.parent.name)],
    )
    for candidate in reversed(candidates):
        if _node_major(str(candidate)) >= LEAST_NODE:
            os.environ["PATH"] = f"{candidate.parent}{os.pathsep}{os.environ.get('PATH', '')}"
            return str(candidate)
    return None


def _pid_path():
    return paths.lab_root() / PID_FILE


def running_pid() -> int | None:
    try:
        pid = int(_pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def url(port: int = config.DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def serve(port: int = config.DEFAULT_PORT, workers: int = config.WORKERS) -> None:
    """Run the lab's admin until stopped (Ctrl-C, or ``python -m lab stop``)."""
    from src.admin.app import SHUTDOWN_GRACE_SECONDS, STATIC_DIR, create_app
    from src.admin.auth import CredentialStore
    from src.admin.context import build_context
    from src.configuration import ConfigurationManager

    if not os.path.isfile(os.path.join(STATIC_DIR, "index.html")):
        sys.exit("The admin's pages are not built: run `npm run build` in admin_ui/ first.")
    if prefer_node() is None:
        sys.exit(f"The Pine runner needs Node {LEAST_NODE} or later (admin_ui/.nvmrc): nvm install {LEAST_NODE}.")
    config_path = config.write(db.dsn(), port, workers)
    # The backtests' command finds the lab's database through it.
    os.environ["WATA_CONFIG_PATH"] = config_path
    for folder in (paths.logs(), paths.backtests(), paths.scripts()):
        folder.mkdir(parents=True, exist_ok=True)
    credentials = CredentialStore(str(paths.credentials()))
    password = config.password()
    if not credentials.is_configured() or not credentials.verify(password):
        credentials.set_password(password)
    ctx = build_context(ConfigurationManager(config_path))
    _pid_path().write_text(str(os.getpid()), encoding="utf-8")
    print(f"The lab's admin: {url(port)} (password in {paths.password_file()})", flush=True)
    import uvicorn

    try:
        uvicorn.run(create_app(ctx), host="127.0.0.1", port=port, timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS, log_level="warning")
    finally:
        try:
            _pid_path().unlink()
        except OSError:
            pass


def stop() -> bool:
    """Stop the lab's admin; False when it was not running."""
    pid = running_pid()
    if pid is None:
        return False
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        if running_pid() is None:
            return True
        time.sleep(0.1)
    return running_pid() is None


class Client:
    """The lab admin's API, signed in with the lab's password."""

    def __init__(self, port: int = config.DEFAULT_PORT):
        import httpx

        self.base = url(port)
        self._http = httpx.Client(base_url=self.base, headers={"Origin": self.base}, timeout=60)
        answer = self._http.post("/api/session", json={"password": config.password()})
        if not answer.is_success:
            raise RuntimeError(f"The lab's admin refused to sign in ({answer.status_code}): is it running? python -m lab serve")

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _call(self, method: str, path: str, **options):
        answer = self._http.request(method, path, **options)
        if answer.status_code >= 400:
            try:
                detail = answer.json().get("detail")
            except ValueError:
                detail = answer.text
            raise RuntimeError(f"{method} {path}: {answer.status_code} {detail}")
        return answer.json() if answer.content else None

    def get(self, path: str, **params):
        return self._call("GET", path, params={key: value for key, value in params.items() if value is not None})

    def post(self, path: str, body=None):
        return self._call("POST", path, json=body)

    def create(self, body: dict) -> dict:
        return self.post("/api/backtests", body)

    def run(self, run_id: str) -> dict:
        return self.get(f"/api/backtests/{run_id}")

    def wait(self, run_ids: list[str], every: float = 2.0, report=None) -> list[dict]:
        """Until every run named has finished; what each became."""
        pending = list(run_ids)
        finished: dict[str, dict] = {}
        while pending:
            for run_id in list(pending):
                found = self.run(run_id)
                if found["status"]["state"] not in ("queued", "running"):
                    finished[run_id] = found
                    pending.remove(run_id)
                    if report:
                        report(found)
            if pending:
                time.sleep(every)
        return [finished[run_id] for run_id in run_ids]
