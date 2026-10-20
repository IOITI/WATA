"""
The Pine scripts of the Technical Analysis pages, kept as files as backtests are
(the admin's database pool is read-only):

    <root>/scripts/<id>/script.json   the script: name, kind, description, its versions
    <root>/scripts/<id>/v0001.pine    each saved version, never written again
    <root>/workspaces/<key>.json      a chart layout, Vela's own document, kept as it comes
    <root>/trash/<id>-<time>/         a deleted script, until it is removed by hand

The versions are plain .pine files, so that a script can be read, diffed or run by
pinets-cli without WATA. A save names the version it started from: if another
save came first, it is refused rather than lost.

Only native Pine Script goes to PineTS. It runs a source without a //@version
line, or one marked //@PineTS, as JavaScript: such a source is refused here, and
again by the pages and the engine before they run anything.
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")
WORKSPACE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

MAX_SOURCE_BYTES = 256 * 1024
MAX_SCRIPTS = 500
MAX_WORKSPACE_BYTES = 1024 * 1024
MAX_NAME_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 500

INDICATOR = "indicator"
STRATEGY = "strategy"
LIBRARY = "library"
KINDS = (INDICATOR, STRATEGY, LIBRARY)
PINE_VERSIONS = (5, 6)

_VERSION_LINE = re.compile(r"^\s*//\s*@version\s*=\s*(\d+)\s*$", re.MULTILINE)
_PINETS_MARKER = re.compile(r"^\s*//\s*@PineTS\b", re.MULTILINE)
# Matched where a line starts (pattern.match with a position: no "^").
_DECLARATION = re.compile(r"(indicator|strategy|library)\s*\(")
_LIBRARY_TITLE = re.compile(r"""library\s*\(\s*(?:title\s*=\s*)?(?:"([^"\n]*)"|'([^'\n]*)')""")
_IMPORT = re.compile(
    r"^import\s+([A-Za-z0-9_]+)\s*/\s*([A-Za-z0-9_]+)\s*/\s*(\d+)(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?\s*$",
    re.MULTILINE,
)


class ScriptNotFound(Exception):
    """No script, version or library of that name."""


class InvalidScript(Exception):
    """A source, name or layout that cannot be kept."""


class ScriptConflict(Exception):
    """Another save came first, or the name is already taken."""


@dataclass(frozen=True)
class PineFacts:
    """What WATA reads of a source without running it."""

    pine_version: int
    kind: str
    library_name: str | None
    imports: tuple[dict, ...]


def strip_comments(source: str) -> str:
    """The source without its // comments, strings left whole, lines kept where they were."""
    out = []
    quote = None
    index = 0
    while index < len(source):
        char = source[index]
        if quote:
            out.append(char)
            if char == "\\" and index + 1 < len(source):
                out.append(source[index + 1])
                index += 2
                continue
            if char == quote or char == "\n":
                quote = None
        elif char in "\"'":
            quote = char
            out.append(char)
        elif source.startswith("//", index):
            end = source.find("\n", index)
            index = len(source) if end == -1 else end
            continue
        else:
            out.append(char)
        index += 1
    return "".join(out)


def pine_facts(source: str) -> PineFacts:
    """
    The Pine version, kind, library name and imports of ``source``, or InvalidScript
    when it is not native Pine Script of a version PineTS runs.
    """
    if not isinstance(source, str) or not source.strip():
        raise InvalidScript("The script is empty")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise InvalidScript(f"A script is {MAX_SOURCE_BYTES // 1024} KB at most")
    if _PINETS_MARKER.search(source):
        raise InvalidScript("Only Pine Script is accepted: //@PineTS marks PineTS's JavaScript syntax")
    versions = _VERSION_LINE.findall(source)
    if not versions:
        raise InvalidScript("The script must say its Pine version, //@version=5 or //@version=6")
    pine_version = int(versions[0])
    if pine_version not in PINE_VERSIONS:
        raise InvalidScript(f"Pine Script version {pine_version} is not supported: versions 5 and 6 are")
    code = strip_comments(source)
    start = len(code) - len(code.lstrip())
    # A declaration starts a line: Pine's top-level statements begin at column 0.
    start = code.rfind("\n", 0, start) + 1
    declaration = _DECLARATION.match(code, start)
    if not declaration:
        raise InvalidScript("The script must start with indicator(), strategy() or library()")
    kind = declaration.group(1)
    library_name = None
    if kind == LIBRARY:
        # The title may be on the declaration's next line.
        title = _LIBRARY_TITLE.match(code, start)
        library_name = (title.group(1) or title.group(2) or "").strip() if title else ""
        if not library_name:
            raise InvalidScript('A library must be named: library("Name")')
    imports = tuple(
        {"user": user, "name": name, "version": int(version), "alias": alias or name}
        for user, name, version, alias in _IMPORT.findall(code)
    )
    return PineFacts(pine_version=pine_version, kind=kind, library_name=library_name, imports=imports)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _checked_name(name: str) -> str:
    if not isinstance(name, str):
        raise InvalidScript("A script needs a name")
    name = " ".join(name.split())
    if not name:
        raise InvalidScript("A script needs a name")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidScript(f"A name is {MAX_NAME_LENGTH} characters at most")
    return name


def _checked_description(description: str) -> str:
    description = (description or "").strip()
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise InvalidScript(f"A description is {MAX_DESCRIPTION_LENGTH} characters at most")
    return description


def _write_text(path: str, content: str) -> None:
    partial = f"{path}.part"
    with open(partial, "w", encoding="utf-8") as out_file:
        out_file.write(content)
    os.replace(partial, path)


def _write_json(path: str, content: dict) -> None:
    _write_text(path, json.dumps(content, indent=2))


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as in_file:
            return json.load(in_file)
    except (OSError, ValueError):
        return None


class ScriptStore:
    """The scripts and chart layouts under ``root``. Safe to share between requests."""

    def __init__(self, root: str):
        self._root = root
        # Shared with the signal rules (src/admin/ta_rules.py): a script is not
        # deleted while a rule is being saved on it.
        self._lock = threading.RLock()

    @property
    def root(self) -> str:
        return self._root

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    # ── paths, from checked ids only ──

    def _scripts_dir(self) -> str:
        return os.path.join(self._root, "scripts")

    def _script_dir(self, script_id: str) -> str:
        if not isinstance(script_id, str) or not ID_PATTERN.match(script_id):
            raise ScriptNotFound(f"No script {script_id!r}")
        return os.path.join(self._scripts_dir(), script_id)

    def _version_path(self, script_id: str, number: int) -> str:
        return os.path.join(self._script_dir(script_id), f"v{number:04d}.pine")

    def _workspace_path(self, key: str) -> str:
        if not isinstance(key, str) or not WORKSPACE_KEY_PATTERN.match(key):
            raise InvalidScript("A layout key is up to 64 lower-case letters, digits, '-' or '_'")
        return os.path.join(self._root, "workspaces", f"{key}.json")

    # ── reading ──

    def _meta(self, script_id: str) -> dict:
        meta = _read_json(os.path.join(self._script_dir(script_id), "script.json"))
        if meta is None:
            raise ScriptNotFound(f"No script {script_id!r}")
        return meta

    def _ids(self) -> list[str]:
        try:
            names = os.listdir(self._scripts_dir())
        except OSError:
            return []
        return sorted(name for name in names if ID_PATTERN.match(name))

    def scripts(self) -> list[dict]:
        """Every script, without its source, most recently saved first."""
        metas = []
        for script_id in self._ids():
            meta = _read_json(os.path.join(self._script_dir(script_id), "script.json"))
            if meta is not None:
                metas.append(self._described(meta))
        metas.sort(key=lambda meta: meta["updated_at"], reverse=True)
        return metas

    def script(self, script_id: str) -> dict:
        """A script with the source of its current version."""
        meta = self._meta(script_id)
        return {**self._described(meta), "source": self.source(script_id, meta["version"])}

    def source(self, script_id: str, number: int | None = None) -> str:
        """The source of version ``number``, the current one when None."""
        if number is None:
            number = self._meta(script_id)["version"]
        if not isinstance(number, int) or number < 1:
            raise ScriptNotFound(f"No version {number!r}")
        try:
            with open(self._version_path(script_id, number), encoding="utf-8") as in_file:
                return in_file.read()
        except FileNotFoundError:
            raise ScriptNotFound(f"No version {number} of script {script_id!r}") from None

    def version(self, script_id: str, number: int) -> dict:
        """One version: its source and when it was saved."""
        meta = self._meta(script_id)
        entry = next((entry for entry in meta["versions"] if entry["number"] == number), None)
        if entry is None:
            raise ScriptNotFound(f"No version {number} of script {script_id!r}")
        return {**entry, "source": self.source(script_id, number)}

    def library(self, name: str) -> dict:
        """The library declared as library("<name>"), with its current source."""
        for meta in self.scripts():
            if meta["kind"] == LIBRARY and meta["library_name"] == name:
                return {**meta, "source": self.source(meta["id"], meta["version"])}
        raise ScriptNotFound(f"No library named {name!r}")

    def _described(self, meta: dict) -> dict:
        return {key: meta[key] for key in (
            "id", "name", "kind", "description", "pine_version", "library_name", "imports",
            "version", "versions", "created_at", "updated_at",
        )}

    # ── writing ──

    def _check_unique(self, name: str, library_name: str | None, except_id: str | None = None) -> None:
        for meta in self.scripts():
            if meta["id"] == except_id:
                continue
            if meta["name"].casefold() == name.casefold():
                raise ScriptConflict(f"A script named {meta['name']!r} already exists")
            if library_name and meta["kind"] == LIBRARY and meta["library_name"] == library_name:
                raise ScriptConflict(f"A library named {library_name!r} already exists: {meta['name']!r}")

    def _version_entry(self, number: int, source: str) -> dict:
        data = source.encode("utf-8")
        return {
            "number": number,
            "saved_at": _now(),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def create(self, name: str, source: str, description: str = "") -> dict:
        name = _checked_name(name)
        description = _checked_description(description)
        facts = pine_facts(source)
        with self._lock:
            if len(self._ids()) >= MAX_SCRIPTS:
                raise InvalidScript(f"WATA keeps {MAX_SCRIPTS} scripts at most: delete one first")
            self._check_unique(name, facts.library_name)
            script_id = secrets.token_hex(6)
            directory = self._script_dir(script_id)
            os.makedirs(directory)
            now = _now()
            entry = self._version_entry(1, source)
            meta = {
                "id": script_id,
                "name": name,
                "description": description,
                **self._facts_fields(facts),
                "version": 1,
                "versions": [entry],
                "created_at": now,
                "updated_at": now,
            }
            _write_text(self._version_path(script_id, 1), source)
            _write_json(os.path.join(directory, "script.json"), meta)
        return self.script(script_id)

    @staticmethod
    def _facts_fields(facts: PineFacts) -> dict:
        return {
            "kind": facts.kind,
            "pine_version": facts.pine_version,
            "library_name": facts.library_name,
            "imports": list(facts.imports),
        }

    def save(
        self,
        script_id: str,
        base_version: int,
        source: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> dict:
        """
        A new version when the source changed; the name and description change in
        place. ``base_version`` is the version the change was made on.
        """
        with self._lock:
            meta = self._meta(script_id)
            if base_version != meta["version"]:
                raise ScriptConflict(
                    f"The script was saved meanwhile (version {meta['version']}, not {base_version}): "
                    "reload it, or save yours as a copy"
                )
            updated = dict(meta)
            if name is not None:
                updated["name"] = _checked_name(name)
            if description is not None:
                updated["description"] = _checked_description(description)
            library_name = meta["library_name"]
            new_source = source is not None and source != self.source(script_id, meta["version"])
            if new_source:
                facts = pine_facts(source)
                updated.update(self._facts_fields(facts))
                library_name = facts.library_name
            self._check_unique(updated["name"], library_name, except_id=script_id)
            if new_source:
                number = meta["version"] + 1
                _write_text(self._version_path(script_id, number), source)
                updated["version"] = number
                updated["versions"] = [*meta["versions"], self._version_entry(number, source)]
            if updated != meta:
                updated["updated_at"] = _now()
                _write_json(os.path.join(self._script_dir(script_id), "script.json"), updated)
        return self.script(script_id)

    def duplicate(self, script_id: str, name: str) -> dict:
        source = self.source(script_id)
        meta = self._meta(script_id)
        if meta["kind"] == LIBRARY:
            raise InvalidScript("A library is known by the name it declares: a copy would share it")
        return self.create(name, source, meta["description"])

    def delete(self, script_id: str, users: Callable[[dict], list[str]] | None = None) -> None:
        """
        Move the script to the trash, unless something still relies on it: a script
        importing the library, or what ``users`` names (the signal rules running
        it), asked under the lock.
        """
        with self._lock:
            meta = self._meta(script_id)
            in_use = []
            if meta["kind"] == LIBRARY:
                in_use += [f"the script {name!r}" for name in self.importers(meta["library_name"])]
            if users is not None:
                in_use += users(meta)
            if in_use:
                raise ScriptConflict(f"{meta['name']!r} is used by {', '.join(in_use)}")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            trash = os.path.join(self._root, "trash")
            os.makedirs(trash, exist_ok=True)
            shutil.move(self._script_dir(script_id), os.path.join(trash, f"{script_id}-{stamp}"))

    def importers(self, library_name: str) -> list[str]:
        """The scripts whose current version imports the library ``library_name``."""
        return [
            meta["name"] for meta in self.scripts()
            if any(entry["name"] == library_name for entry in meta["imports"])
        ]

    # ── chart layouts ──

    def workspace(self, key: str) -> str | None:
        stored = _read_json(self._workspace_path(key))
        value = stored.get("value") if stored else None
        return value if isinstance(value, str) else None

    def save_workspace(self, key: str, value: str) -> None:
        if not isinstance(value, str):
            raise InvalidScript("A layout is a string")
        if len(value.encode("utf-8")) > MAX_WORKSPACE_BYTES:
            raise InvalidScript(f"A layout is {MAX_WORKSPACE_BYTES // 1024} KB at most")
        path = self._workspace_path(key)
        with self._lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _write_json(path, {"value": value, "saved_at": _now()})
