"""
What the Indicators pages say of a script beside its code, kept in the script's own folder beside its versions, by
this module and never by src/admin/ta_scripts.py: the TA engine loads that one, and a change there would put its
container in the next deploy (as script_origins.py and rule_history.py are kept beside it).

    <root>/scripts/<id>/details.json     among the favourites or not, its family, its picture, its How it works
    <root>/scripts/<id>/how/r0001.md     each version of its How it works, never written again
    <root>/scripts/<id>/picture.png      its picture (PNG, JPEG or WebP), when it has one

A deleted script's folder goes to the trash whole, these with it; a copy of a script takes them along (``carry``).
The operator writes them from the pages, the AI agent a family and a How it works (describe_script, as
agent:<conversation>), and a script taken from LuxAlgo's Library gets LuxAlgo's write-up, family and picture
(``from_luxalgo``). Each version of a How it works is kept, and one is put back by saving it again. Nothing here
trades: none of it is audited, as a script's save is not.
"""

import asyncio
import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

from src.admin.images import JPEG, PNG, WEBP, image_type
from src.admin.ta_scripts import ID_PATTERN, InvalidScript, ScriptConflict, ScriptNotFound

FILE = "details.json"
HOW = "how"
PICTURE = "picture"
MAX_FAMILY = 40
MAX_HOW = 30_000
MAX_PICTURE_BYTES = 2 * 1024 * 1024
# The pictures kept, by the media type their first bytes say, and the file each is kept as.
PICTURE_TYPES = {PNG: "png", JPEG: "jpg", WEBP: "webp"}
# The versions of a How it works its history gives, the newest first.
HISTORY = 30
OPERATOR = "operator"
LUXALGO = "luxalgo"
# LuxAlgo's pictures, on its image host (the admin's policy lets the pages load pictures from it alone, LUXALGO_IMAGES
# of src/admin/app.py), under its Library: previewImage of admin_ui/src/features/ta/luxalgo/luxalgoModel.ts.
LUXALGO_IMAGE_HOST = "luxalgo-production.s3.amazonaws.com"
_LUXALGO_IMAGE_PATH = re.compile(r"^/library/[A-Za-z0-9/_.-]{1,200}\.(?:png|jpe?g|webp|gif)$", re.IGNORECASE)
LIBRARY_PAGE = "https://www.luxalgo.com/library/indicator/{slug}/"
# Said where LuxAlgo's write-up was longer than a How it works may be.
CUT = "\n\n*Cut here: LuxAlgo's page has the rest.*"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _now(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")


def _write_bytes(path: str, data: bytes) -> None:
    partial = f"{path}.part"
    with open(partial, "wb") as out_file:
        out_file.write(data)
    os.replace(partial, path)


def checked_family(family) -> str | None:
    """A family as it is kept: its words, one space apart, MAX_FAMILY characters at most; None for none."""
    label = " ".join(str(family or "").split())
    if _CONTROL.search(label):
        raise InvalidScript("A family is a short name, on one line")
    if len(label) > MAX_FAMILY:
        raise InvalidScript(f"A family is {MAX_FAMILY} characters at most")
    return label or None


def luxalgo_picture(url) -> str | None:
    """LuxAlgo's picture of an indicator when it is one the pages may show: on its image host, under its Library."""
    if not isinstance(url, str) or len(url) > 400:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != LUXALGO_IMAGE_HOST or parsed.query or parsed.fragment:
        return None
    return url if _LUXALGO_IMAGE_PATH.match(parsed.path) else None


class ScriptDetails:
    """``store``: the scripts (src/admin/ta_scripts.py), whose lock every write here takes: no script is deleted
    halfway through one, and none is made again by one."""

    def __init__(self, store, clock=time.time):
        self._store = store
        self._clock = clock

    # ── paths, from checked ids of scripts that are there ──

    def _folder(self, script_id) -> str:
        if not isinstance(script_id, str) or not ID_PATTERN.match(script_id):
            raise ScriptNotFound(f"No script {script_id!r}")
        folder = os.path.join(self._store.root, "scripts", script_id)
        if not os.path.isfile(os.path.join(folder, "script.json")):
            raise ScriptNotFound(f"No script {script_id!r}")
        return folder

    @staticmethod
    def _read(folder: str) -> dict:
        try:
            with open(os.path.join(folder, FILE), encoding="utf-8") as in_file:
                found = json.load(in_file)
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    @staticmethod
    def _write(folder: str, details: dict) -> None:
        _write_bytes(os.path.join(folder, FILE), json.dumps(details, ensure_ascii=False, indent=1).encode("utf-8"))

    def _changed(self, script_id: str, change) -> dict:
        """``change`` made to a script's details under the store's lock, and written: what the pages read of them."""
        with self._store.lock:
            folder = self._folder(script_id)
            details = self._read(folder)
            change(folder, details)
            self._write(folder, details)
        return self.of(script_id)

    # ── reading ──

    def of(self, script_id: str) -> dict:
        """What the pages read of a script beside its code (``how`` without its text, which ``how`` gives)."""
        details = self._read(self._folder(script_id))
        how = details.get("how")
        return {
            "favourite": bool(details.get("favourite")),
            "family": details.get("family"),
            "picture": details.get("picture"),
            "luxalgo_picture": details.get("luxalgo_picture"),
            "how": {key: how.get(key) for key in ("revision", "by", "at", "from", "size")} if how else None,
        }

    def how(self, script_id: str) -> dict | None:
        """Its How it works as it is now (text, revision, who, when, where from); None while it has none."""
        folder = self._folder(script_id)
        how = self._read(folder).get("how")
        if not how or not how.get("size"):
            return None
        return {**how, "text": self._how_text(folder, how["revision"])}

    @staticmethod
    def _how_text(folder: str, revision: int) -> str:
        try:
            with open(os.path.join(folder, HOW, f"r{revision:04d}.md"), encoding="utf-8") as in_file:
                return in_file.read()
        except OSError:
            return ""

    def how_history(self, script_id: str) -> list[dict]:
        """Its How it works as each save left it, the newest first, HISTORY at most."""
        folder = self._folder(script_id)
        versions = self._read(folder).get("how_versions") or []
        return [{**entry, "text": self._how_text(folder, entry["revision"])} for entry in reversed(versions[-HISTORY:])]

    def picture(self, script_id: str) -> tuple[str, str] | None:
        """Where its picture is kept, and its media type; None while it has none."""
        folder = self._folder(script_id)
        picture = self._read(folder).get("picture")
        if not picture:
            return None
        path = os.path.join(folder, picture["file"])
        return (path, picture["type"]) if os.path.isfile(path) else None

    # ── writing ──

    def set_favourites(self, script_ids: list[str], favourite: bool) -> dict:
        """Several scripts among the favourites, or out of them: those that are not there named with why."""
        changed, kept = [], []
        for script_id in dict.fromkeys(script_ids):
            try:
                self._changed(script_id, lambda folder, details: details.update(favourite=bool(favourite)))
            except ScriptNotFound:
                kept.append({"id": script_id, "reason": "No such script: deleted, or never made"})
                continue
            changed.append(script_id)
        return {"changed": changed, "kept": kept}

    def set_family(self, script_id: str, family, by: str) -> dict:
        label = checked_family(family)

        def change(folder: str, details: dict) -> None:
            details["family"] = label
            details["family_set"] = {"by": by, "at": _now(self._clock())}

        return self._changed(script_id, change)

    def save_how(self, script_id: str, text, revision: int, by: str, source: dict | None = None) -> dict:
        """
        A new version of its How it works (Markdown; empty: it has none), from the ``revision`` its writer read (0
        while it had none): refused when another was saved meanwhile, rather than lost.
        """
        text = str(text or "").replace("\r\n", "\n").strip()
        if len(text) > MAX_HOW:
            raise InvalidScript(f"A How it works is {MAX_HOW:,} characters at most")
        saved = {}

        def change(folder: str, details: dict) -> None:
            current = (details.get("how") or {}).get("revision", 0)
            if revision != current:
                raise ScriptConflict(
                    f"Its How it works was saved meanwhile (revision {current}, you started from {revision}): read it again"
                )
            number = current + 1
            os.makedirs(os.path.join(folder, HOW), exist_ok=True)
            _write_bytes(os.path.join(folder, HOW, f"r{number:04d}.md"), text.encode("utf-8"))
            entry = {"revision": number, "by": by, "at": _now(self._clock()), "from": source, "size": len(text)}
            details["how"] = entry
            details["how_versions"] = [*(details.get("how_versions") or []), entry]
            saved.update(entry)

        self._changed(script_id, change)
        return {**saved, "text": text}

    def set_picture(self, script_id: str, data: bytes, by: str) -> dict:
        """Its picture: a PNG, JPEG or WebP image of MAX_PICTURE_BYTES at most, known by its first bytes."""
        media_type = image_type(data)
        if media_type not in PICTURE_TYPES:
            raise InvalidScript("A picture is a PNG, JPEG or WebP image")
        if len(data) > MAX_PICTURE_BYTES:
            raise InvalidScript(f"A picture is {MAX_PICTURE_BYTES // (1024 * 1024)} MB at most")
        name = f"{PICTURE}.{PICTURE_TYPES[media_type]}"

        def change(folder: str, details: dict) -> None:
            _write_bytes(os.path.join(folder, name), data)
            for other in PICTURE_TYPES.values():
                if f"{PICTURE}.{other}" != name:
                    try:
                        os.remove(os.path.join(folder, f"{PICTURE}.{other}"))
                    except FileNotFoundError:
                        pass
            details["picture"] = {"file": name, "type": media_type, "size": len(data), "by": by, "at": _now(self._clock())}

        return self._changed(script_id, change)

    def drop_picture(self, script_id: str) -> dict:
        def change(folder: str, details: dict) -> None:
            picture = details.pop("picture", None)
            if picture:
                try:
                    os.remove(os.path.join(folder, picture["file"]))
                except FileNotFoundError:
                    pass

        return self._changed(script_id, change)

    def set_luxalgo_picture(self, script_id: str, url) -> dict:
        """LuxAlgo's picture of the indicator it was taken from, shown while it has none of its own."""
        kept = luxalgo_picture(url)
        return self._changed(script_id, lambda folder, details: details.update(luxalgo_picture=kept))

    def carry(self, from_id: str, to_id: str, by: str) -> dict:
        """A copy of a script takes its family, its How it works (its first version there), its pictures; not its star."""
        with self._store.lock:
            source = self._folder(from_id)
            found = self._read(source)
            text = self._how_text(source, found["how"]["revision"]) if (found.get("how") or {}).get("size") else ""
            picture = found.get("picture")

            def change(folder: str, details: dict) -> None:
                details["family"] = found.get("family")
                details["luxalgo_picture"] = found.get("luxalgo_picture")
                if picture and os.path.isfile(os.path.join(source, picture["file"])):
                    shutil.copyfile(os.path.join(source, picture["file"]), os.path.join(folder, picture["file"]))
                    details["picture"] = {**picture, "by": by, "at": _now(self._clock())}

            self._changed(to_id, change)
            if text:
                self.save_how(to_id, text, 0, by, {"script": from_id, **(found["how"].get("from") or {})})
        return self.of(to_id)


def picture_url(script_id: str, found: dict) -> str | None:
    """Where the pages load a script's picture: its own (its time in the address, so that a new one is loaded), else
    LuxAlgo's; None while it has neither."""
    picture = found.get("picture")
    if picture:
        return f"/api/ta/scripts/{script_id}/picture?v={quote(str(picture.get('at') or ''))}"
    return found.get("luxalgo_picture")


def with_details(details: ScriptDetails | None, meta: dict) -> dict:
    """A script as the pages read it: with its star, family, picture and How it works (without its text)."""
    if details is None:
        return meta
    try:
        found = details.of(meta["id"])
    except ScriptNotFound:
        return meta
    return {
        **meta, "favourite": found["favourite"], "family": found["family"],
        "picture": picture_url(meta["id"], found), "own_picture": found["picture"] is not None, "how": found["how"],
    }


async def from_luxalgo(details: ScriptDetails, luxalgo, script_id: str, slug: str, by: str, *, replace: bool = False) -> list[str]:
    """
    LuxAlgo's write-up, family and picture of the indicator a script was taken from, kept as the script's: its How
    it works and its family only where it has none, unless ``replace``; LuxAlgo's picture shown while it has none of
    its own. What it filled: how, family, picture.
    """
    found = await luxalgo.read("library_get_indicator", {"slug": slug})
    if not isinstance(found, dict):
        raise InvalidScript(f"LuxAlgo said nothing of {slug}")
    names = {}
    try:
        listed = await luxalgo.read("library_list_families")
        names = {entry.get("key"): entry.get("name") for entry in (listed or {}).get("families") or [] if isinstance(entry, dict)}
    except Exception:
        pass
    key = found.get("family")
    family = names.get(key) or (str(key).replace("-", " ").capitalize() if key else None)
    markdown = str(found.get("body_markdown") or "").strip()
    if len(markdown) > MAX_HOW:
        markdown = markdown[:MAX_HOW - len(CUT)] + CUT
    now = await asyncio.to_thread(details.of, script_id)
    filled = []
    if markdown and (replace or not (now["how"] or {}).get("size")):
        source = {"provider": LUXALGO, "slug": slug, "url": LIBRARY_PAGE.format(slug=slug)}
        revision = (now["how"] or {}).get("revision") or 0
        await asyncio.to_thread(details.save_how, script_id, markdown, revision, by, source)
        filled.append("how")
    if family and (replace or not now["family"]):
        await asyncio.to_thread(details.set_family, script_id, family[:MAX_FAMILY], by)
        filled.append("family")
    if luxalgo_picture(found.get("image_url")):
        await asyncio.to_thread(details.set_luxalgo_picture, script_id, found["image_url"])
        filled.append("picture")
    return filled
