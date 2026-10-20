"""
The operator's switch of the MCP endpoint, on the System page: paused, every
call is answered 503 and nothing Claude asks is done, without editing the
configuration or restarting the admin. Kept beside the tokens, so that a
pause outlives a restart.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class McpPause:
    def __init__(self, path: str, clock=time.time):
        self.path = path
        self._clock = clock
        self._state = self._read()

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as state_file:
                found = json.load(state_file)
        except FileNotFoundError:
            return {"paused": False, "changed_at": None}
        except (OSError, ValueError) as e:
            # A file that cannot be read keeps the endpoint paused: the safe side.
            logger.error("The MCP endpoint's switch (%s) cannot be read, it is taken as paused: %s", self.path, e)
            return {"paused": True, "changed_at": None}
        paused = found.get("paused") if isinstance(found, dict) else None
        return {"paused": paused if isinstance(paused, bool) else True, "changed_at": (found or {}).get("changed_at")}

    def paused(self) -> bool:
        return self._state["paused"]

    def state(self) -> dict:
        return dict(self._state)

    def set(self, paused: bool) -> dict:
        state = {"paused": bool(paused),
                 "changed_at": datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        partial = f"{self.path}.part"
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file)
        os.replace(partial, self.path)
        self._state = state
        return self.state()
