"""
Files the operator attaches to a conversation with the AI agent from their
computer: a screenshot, a PDF, a CSV, a Pine script. A file is staged when it is
chosen, an hour at most, until the request that sends it; it is then kept with
its conversation (src/admin/agent/store.py) and sent to the Claude API with each
of its requests, as the transcript's reference to it says (transcript.py).

What a file is comes from its first bytes (an image, a PDF) or from its name
and its text (UTF-8, no NUL byte). A text is cleaned of the shapes of secrets
before it is kept, as a log line is (src/logging_helper/redaction.py); an image
or a PDF cannot be, and goes as it is. Ids are made here and matched against
their pattern before any path is built: a file's own name is only ever said.
"""

import json
import os
import re
import secrets
import threading
import time

from src.admin.errors import InvalidRequest
from src.logging_helper.redaction import MASK, redact

FILE_ID = re.compile(r"^f[0-9a-f]{10}$")
IMAGE, PDF, TEXT = "image", "pdf", "text"
# The most a file may weigh, by kind: an image as the Claude API takes one, a PDF of a few dozen pages, a text of
# about 50,000 tokens.
MAX_BYTES = {IMAGE: 5 * 1024 * 1024, PDF: 5 * 1024 * 1024, TEXT: 200 * 1024}
LARGEST = max(MAX_BYTES.values())
# A file chosen is staged this long, and this many wait at most.
STAGED_SECONDS = 3600
MAX_STAGED = 10
# A text file is known by its name.
TEXT_SUFFIXES = (".csv", ".tsv", ".pine", ".txt", ".json", ".md")
MAX_NAME = 120
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", IMAGE, "image/png"),
    (b"\xff\xd8\xff", IMAGE, "image/jpeg"),
    (b"GIF87a", IMAGE, "image/gif"),
    (b"GIF89a", IMAGE, "image/gif"),
    (b"%PDF-", PDF, "application/pdf"),
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class UnknownFile(Exception):
    """No such file staged: never staged, removed, sent already, or kept past its hour."""


def said_name(name) -> str:
    """A file's name as it is said: its last part, without control characters, cut short."""
    name = _CONTROL.sub("", str(name or "")).replace("\\", "/").rsplit("/", 1)[-1].strip()
    if len(name) > MAX_NAME:
        stem, dot, suffix = name.rpartition(".")
        name = f"{stem[:MAX_NAME - len(suffix) - 2]}….{suffix}" if dot and len(suffix) <= 8 else f"{name[:MAX_NAME - 1]}…"
    return name or "file"


def kind_of(name: str, data: bytes) -> tuple[str, str]:
    """What a file is (an image, a PDF, a text) and its media type, or refused with why."""
    for signature, kind, media_type in _SIGNATURES:
        if data.startswith(signature):
            return kind, media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return IMAGE, "image/webp"
    if name.lower().endswith(TEXT_SUFFIXES):
        return TEXT, "text/plain"
    raise InvalidRequest("Attach an image (PNG, JPEG, GIF, WebP), a PDF, or a text file (CSV, TSV, .pine, .txt, "
                         ".json, .md)")


def checked(name: str, data: bytes) -> dict:
    """A file as it is kept: what it is, its bytes (a text cleaned), how many values looking like secrets were masked."""
    if not data:
        raise InvalidRequest("This file is empty")
    kind, media_type = kind_of(name, data)
    if len(data) > MAX_BYTES[kind]:
        raise InvalidRequest(f"A {'text file' if kind == TEXT else kind} is {MAX_BYTES[kind] // 1024:,} kB at most")
    masked = 0
    if kind == TEXT:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise InvalidRequest("A text file is attached in UTF-8") from None
        if "\x00" in text:
            raise InvalidRequest("This file is not text: it holds NUL bytes")
        cleaned = redact(text)
        masked = cleaned.count(MASK) - text.count(MASK)
        data = cleaned.encode("utf-8")
    return {"kind": kind, "media_type": media_type, "data": data, "masked": masked}


class Uploads:
    """The files chosen and not sent yet, under ``root``: each its bytes and what it is, an hour at most."""

    def __init__(self, root: str, clock=time.time):
        self._root = root
        self._clock = clock
        self._lock = threading.RLock()

    def _paths(self, file_id: str) -> tuple[str, str]:
        if not isinstance(file_id, str) or not FILE_ID.match(file_id):
            raise UnknownFile(f"No file {file_id!r}")
        return os.path.join(self._root, f"{file_id}.bin"), os.path.join(self._root, f"{file_id}.json")

    def stage(self, name: str, data: bytes) -> dict:
        """A file chosen, checked and kept until it is sent: what is said of it (never its bytes)."""
        name = said_name(name)
        kept = checked(name, data)
        with self._lock:
            self.prune()
            if len(self._staged()) >= MAX_STAGED:
                raise InvalidRequest(f"{MAX_STAGED} files wait to be sent at most: send them, or remove some")
            os.makedirs(self._root, exist_ok=True)
            file_id = f"f{secrets.token_hex(5)}"
            content, described = self._paths(file_id)
            with open(content, "wb") as out_file:
                out_file.write(kept["data"])
            said = {"id": file_id, "name": name, "type": kept["kind"], "media_type": kept["media_type"],
                    "size": len(kept["data"]), "masked": kept["masked"], "staged_at": self._clock()}
            with open(described, "w", encoding="utf-8") as out_file:
                json.dump(said, out_file)
        return {key: value for key, value in said.items() if key != "staged_at"}

    def describe(self, file_id: str) -> dict:
        """What is said of a staged file, or UnknownFile once it is sent, removed or past its hour."""
        _, described = self._paths(file_id)
        try:
            with open(described, encoding="utf-8") as in_file:
                said = json.load(in_file)
        except (FileNotFoundError, ValueError):
            raise UnknownFile(f"No file {file_id!r}") from None
        if said.get("staged_at", 0) < self._clock() - STAGED_SECONDS:
            raise UnknownFile(f"No file {file_id!r}")
        return {key: value for key, value in said.items() if key != "staged_at"}

    def take(self, file_id: str) -> tuple[dict, bytes]:
        """A staged file handed over to the conversation it is sent in: no longer staged."""
        with self._lock:
            said = self.describe(file_id)
            content, _ = self._paths(file_id)
            try:
                with open(content, "rb") as in_file:
                    data = in_file.read()
            except FileNotFoundError:
                raise UnknownFile(f"No file {file_id!r}") from None
            self.remove(file_id)
        return said, data

    def remove(self, file_id: str) -> None:
        with self._lock:
            found = False
            for path in self._paths(file_id):
                try:
                    os.remove(path)
                    found = True
                except FileNotFoundError:
                    pass
        if not found:
            raise UnknownFile(f"No file {file_id!r}")

    def _staged(self) -> list[str]:
        try:
            names = os.listdir(self._root)
        except OSError:
            return []
        return sorted(name[:-5] for name in names if name.endswith(".json") and FILE_ID.match(name[:-5]))

    def prune(self) -> int:
        """The files staged more than an hour ago, removed; how many."""
        removed = 0
        with self._lock:
            for file_id in self._staged():
                try:
                    self.describe(file_id)
                except UnknownFile:
                    for path in self._paths(file_id):
                        try:
                            os.remove(path)
                        except FileNotFoundError:
                            pass
                    removed += 1
        return removed
