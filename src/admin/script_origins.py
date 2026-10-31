"""
Where a Pine script came from, kept beside the scripts (src/admin/ta_scripts.py)
and never in them: the TA engine loads ta_scripts.py, and a change there would
put its container in the next deploy, as rule_history.py is kept beside
ta_rules.py.

One provider today, LuxAlgo's Library (src/admin/luxalgo.py). An indicator taken
from it keeps its slug, its name, its Library page, its chart on Vela, its
licence and owner, when it was taken and by whom (the operator, or
agent:<conversation>). A script with no record whose header says © LuxAlgo is
said to be derived from LuxAlgo all the same: a copy saved under another name.
"""

import json
import os
import re
import threading
import time
from datetime import datetime, timezone

from src.admin.luxalgo import licence_of
from src.admin.ta_scripts import ID_PATTERN

FILE = "origins.json"
LUXALGO = "luxalgo"
LIBRARY_PAGE = "https://www.luxalgo.com/library/indicator/{slug}/"
_LUXALGO_MARK = re.compile(r"^\s*//.*©\s*LuxAlgo", re.IGNORECASE | re.MULTILINE)
# What is read of a script's header to tell it is LuxAlgo's.
HEADER_LINES = 20


def stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def luxalgo_origin(found: dict, by: str | None, at: float) -> dict:
    """The origin of an indicator taken from LuxAlgo's Library, from its source as LuxAlgo.source gives it."""
    return {
        "provider": LUXALGO, "slug": found["slug"], "name": found["name"],
        "url": LIBRARY_PAGE.format(slug=found["slug"]), "chart_url": found.get("chart_url"),
        "licence": found["licence"], "owner": found.get("owner"), "taken_at": stamp(at), "by": by,
    }


# A licence that forbids commercial use (Creative Commons' NC), as LuxAlgo's open indicators mostly are.
_NON_COMMERCIAL = re.compile(r"\bNC\b|NonCommercial|Non-Commercial", re.IGNORECASE)
UNREAD_LICENCE = "see LuxAlgo's NOTICE"


def needs_licence_check(origin: dict | None) -> bool:
    """
    Whether taking live a rule whose script came from LuxAlgo asks the operator
    to say they checked its licence: a non-commercial one, or one its header
    did not let WATA read.
    """
    if not origin or origin.get("provider") != LUXALGO:
        return False
    licence = str(origin.get("licence") or UNREAD_LICENCE)
    return licence == UNREAD_LICENCE or bool(_NON_COMMERCIAL.search(licence))


def derived_from_header(source: str | None) -> dict | None:
    """A script whose header says © LuxAlgo: derived from LuxAlgo, with the licence its header says."""
    head = "\n".join((source or "").splitlines()[:HEADER_LINES])
    if not _LUXALGO_MARK.search(head):
        return None
    return {"provider": LUXALGO, "derived": True, **licence_of(head)}


class ScriptOrigins:
    """``root``: the Technical Analysis folder (admin.persistant.ta_path), the admin's own."""

    def __init__(self, root: str, clock=time.time):
        self._path = os.path.join(root, FILE)
        self._clock = clock
        self._lock = threading.Lock()
        # A script's version -> what its header says, read once.
        self._headers: dict[tuple[str, int], dict | None] = {}

    def _read(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as in_file:
                value = json.load(in_file)
        except (FileNotFoundError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, origins: dict) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        partial = f"{self._path}.part"
        with open(partial, "w", encoding="utf-8") as out_file:
            json.dump(origins, out_file, ensure_ascii=False, indent=1)
        os.replace(partial, self._path)

    def all(self) -> dict[str, dict]:
        with self._lock:
            return self._read()

    def get(self, script_id: str) -> dict | None:
        return self.all().get(script_id)

    def record(self, script_id: str, origin: dict) -> dict:
        if not isinstance(script_id, str) or not ID_PATTERN.match(script_id):
            raise ValueError(f"No script {script_id!r}")
        with self._lock:
            origins = self._read()
            origins[script_id] = origin
            self._write(origins)
        return origin

    def carry(self, from_id: str, to_id: str) -> dict | None:
        """A script made from another (a proposal applied) keeps the origin of the one it came from."""
        origin = self.get(from_id)
        return self.record(to_id, {**origin, "from_script": from_id}) if origin else None

    def of(self, script_id: str, version: int | None = None, source=None) -> dict | None:
        """
        A script's origin: the one recorded, else what its header says
        (``source``: () -> its source, read once per version).
        """
        recorded = self.get(script_id)
        if recorded is not None or source is None:
            return recorded
        key = (script_id, version or 0)
        if key not in self._headers:
            try:
                self._headers[key] = derived_from_header(source())
            except Exception:
                self._headers[key] = None
        return self._headers[key]


def with_origin(origins, scripts, meta: dict) -> dict:
    """A script as the pages read it, with where it came from when that is known."""
    if origins is None:
        return meta
    version = meta.get("version")

    def source() -> str:
        return meta["source"] if "source" in meta else scripts.source(meta["id"], version)

    origin = origins.of(meta["id"], version, source)
    return {**meta, "origin": origin} if origin else meta
