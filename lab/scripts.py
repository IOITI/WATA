"""
The Pine scripts the lab runs, in one store of WATA's own kind
(src/admin/ta_scripts.py, under ``var/lib/lab/ta``): the server's, as
``lab/sync.sh`` pulled them, and those written here. A run pins a version
and its sha256 as the admin does, and the lab's admin shows them all on its
Indicators page.

The server's scripts are mirrored as they are, never changed here: a script
written from one of them is a script of its own (``add``). A file that keeps
being edited is saved as new versions of the script of the same name
(``version_of_file``), so that each run names the version it ran.
"""

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path

from lab import paths

def store():
    from src.admin.ta_scripts import ScriptStore

    paths.scripts().mkdir(parents=True, exist_ok=True)
    return ScriptStore(str(paths.scripts()))


def _server_ids() -> set[str]:
    folder = paths.server_ta() / "scripts"
    return {entry.name for entry in folder.iterdir() if entry.is_dir()} if folder.is_dir() else set()


def is_server_script(script_id: str) -> bool:
    return script_id in _server_ids()


def import_server() -> dict:
    """The server's scripts and signal rules into the lab's store: what was new or newer."""
    source = paths.server_ta() / "scripts"
    target = paths.scripts() / "scripts"
    target.mkdir(parents=True, exist_ok=True)
    scripts = versions = 0
    for folder in sorted(source.iterdir()) if source.is_dir() else []:
        if not folder.is_dir() or not (folder / "script.json").is_file():
            continue
        mirrored = target / folder.name
        if not mirrored.is_dir():
            scripts += 1
        mirrored.mkdir(exist_ok=True)
        for version in folder.glob("v*.pine"):
            if not (mirrored / version.name).is_file():
                shutil.copyfile(version, mirrored / version.name)
                versions += 1
        shutil.copyfile(folder / "script.json", mirrored / "script.json")
    rules = paths.server_ta() / "rules.json"
    if rules.is_file():
        shutil.copyfile(rules, paths.scripts() / "rules.json")
    return {"scripts": scripts, "versions": versions, "rules": rules.is_file()}


def listed() -> list[dict]:
    server = _server_ids()
    return [
        {
            "id": meta["id"], "name": meta["name"], "kind": meta["kind"], "version": meta["version"],
            "from": "server" if meta["id"] in server else "lab", "updated_at": meta.get("updated_at"),
        }
        for meta in store().scripts()
    ]


def add(path: Path, name: str | None = None) -> dict:
    """A new script of the lab, from a file."""
    return store().create(name or path.stem, path.read_text(encoding="utf-8"))


def update(script_id: str, path: Path) -> dict:
    """A new version of a script of the lab, from a file (the same source: no new version)."""
    if is_server_script(script_id):
        raise ValueError("A script of the server is not changed here: `lab script add` makes one of the lab's from the file")
    scripts = store()
    return scripts.save(script_id, scripts.script(script_id)["version"], source=path.read_text(encoding="utf-8"))


def version_of_file(path: Path, name: str | None = None) -> dict:
    """The script a file is, at the version its content is: saved as the next version when it changed."""
    name = name or path.stem
    source = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(source.encode()).hexdigest()
    scripts = store()
    for meta in scripts.scripts():
        if meta["name"] != name or is_server_script(meta["id"]):
            continue
        for entry in reversed(meta["versions"]):
            if entry.get("sha256") == digest:
                return {"id": meta["id"], "version": entry["number"], "name": meta["name"], "new": False}
        saved = scripts.save(meta["id"], meta["version"], source=source)
        return {"id": saved["id"], "version": saved["version"], "name": saved["name"], "new": True}
    created = scripts.create(name, source)
    return {"id": created["id"], "version": created["version"], "name": created["name"], "new": True}


def resolve(reference: str) -> tuple[str, int]:
    """A script by id, by ``id@version`` or by name: its id and version (the newest by default)."""
    scripts = store()
    script_id, _, number = reference.partition("@")
    found = next((meta for meta in scripts.scripts() if script_id in (meta["id"], meta["name"])), None)
    if found is None:
        raise ValueError(f"No script {reference!r}: `python -m lab script list`")
    return found["id"], int(number) if number else found["version"]


async def check(script_id: str, version: int, node: str = "node") -> dict:
    """The script run once through the Pine runner on made-up bars: what it raises, or where it fails (src/research/study.py)."""
    from src.research.study import StudyRefused, check as study_check

    try:
        found = await study_check({"script": {"id": script_id, "version": version}}, store(), node, 512, 60)
    except StudyRefused as e:
        raise RuntimeError(str(e)) from None
    except Exception as e:
        error = getattr(e, "error", None)
        if error is None:
            raise
        return {"ok": False, "error": str(e), "where": error}
    alerts = found["alerts_on_made_up_bars"]
    return {
        "ok": True,
        "script": f"{script_id}@{version}",
        "bars": 300,
        "alerts": sum(alerts.values()),
        "alert_titles": sorted(alerts),
        "warnings": found["warnings"],
        "runtime_warnings": found["runtime_warnings"],
        "milliseconds": found["milliseconds"],
    }


def check_now(script_id: str, version: int) -> dict:
    return asyncio.run(check(script_id, version))


def as_text(value) -> str:
    return json.dumps(value, indent=2, default=str)
