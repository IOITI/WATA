"""
What the agent concluded on a backtest run, a signal rule, a trading day, a
position or an indicator of LuxAlgo's Library, in one line (set_verdict): the
admin's pages show it on that thing,
beside a link to the conversation it came from. The latest replaces the one
before; each goes with its conversation (``verdicts.json`` beside the
conversations, written whole).
"""

import json
import os
import re
import threading

FILE = "verdicts.json"
KINDS = ("run", "rule", "day", "position", "luxalgo")
TONES = ("positive", "mixed", "negative", "neutral")
MAX_LINE = 200
# What a page asks for at once.
MAX_ASKED = 200
# A LuxAlgo indicator is named by its slug, up to 121 characters (src/admin/luxalgo.py SLUG).
ABOUT = re.compile(r"^(run|rule|day|position|luxalgo):([A-Za-z0-9_.-]{1,121})$")


class VerdictStore:
    def __init__(self, root: str):
        self._path = os.path.join(root, FILE)
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as kept:
                found = json.load(kept)
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def _write(self, verdicts: dict) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        partial = f"{self._path}.part"
        with open(partial, "w", encoding="utf-8") as out_file:
            json.dump(verdicts, out_file, ensure_ascii=False, indent=1)
        os.replace(partial, self._path)

    def save(self, about: str, tone: str, line: str, *, conversation: str, request: int, at: str) -> dict:
        """The verdict on ``about`` (checked by the caller to exist), replacing the one before."""
        if not ABOUT.match(about) or tone not in TONES:
            raise ValueError(f"No verdict on {about!r} with the tone {tone!r}")
        verdict = {"about": about, "tone": tone, "line": " ".join(line.split())[:MAX_LINE], "at": at,
                   "conversation": conversation, "request": request}
        with self._lock:
            verdicts = self._read()
            verdicts[about] = verdict
            self._write(verdicts)
        return verdict

    def all(self) -> dict[str, dict]:
        """Every verdict kept, by what it is on."""
        return self._read()

    def of(self, asked: list[str]) -> dict[str, dict]:
        """The verdicts on these things that have one."""
        verdicts = self._read()
        return {about: verdicts[about] for about in asked[:MAX_ASKED] if about in verdicts}

    def forget(self, conversations: set[str]) -> int:
        """The verdicts of conversations removed: they go with them."""
        if not conversations:
            return 0
        with self._lock:
            verdicts = self._read()
            kept = {about: verdict for about, verdict in verdicts.items()
                    if verdict.get("conversation") not in conversations}
            if len(kept) != len(verdicts):
                self._write(kept)
        return len(verdicts) - len(kept)
