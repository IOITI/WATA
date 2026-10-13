"""
What a backtest was made with, in a few characters: the code that replayed it
and the prices it replayed.

Two runs of the same request can differ for a reason no page shows: the
strategy was changed in between, or a day of prices was completed. A run keeps
both fingerprints; whoever reads it later compares them with today's.
"""

import hashlib
import os
from typing import Callable

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# What decides the result of a backtest: the strategies and what replays them, and the bars they are built on.
_CODE = (
    os.path.join("src", "strategy"),
    os.path.join("src", "price_recorder", "bars.py"),
    os.path.join("src", "price_recorder", "range_bars.py"),
)
_LENGTH = 16


def file_digest(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as in_file:
        for block in iter(lambda: in_file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _version(root: str) -> str:
    try:
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as version_file:
            return version_file.read().strip()
    except OSError:
        return "unknown"


def code_fingerprint(root: str = _ROOT) -> dict:
    """The version of WATA, and a digest of the files a backtest's result depends on."""
    paths = []
    for entry in _CODE:
        if entry.endswith(".py"):
            paths.append(os.path.join(root, entry))
        for folder, _, names in os.walk(os.path.join(root, entry)):
            paths += [os.path.join(folder, name) for name in names if name.endswith(".py")]
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(os.path.relpath(path, root).encode())
        digest.update(file_digest(path).encode())
    return {"version": _version(root), "digest": digest.hexdigest()[:_LENGTH]}


def prices_fingerprint(paths: list[str], digest_of: Callable[[str], str] = file_digest) -> dict:
    """
    A digest of the files of prices a backtest read, by name and content.
    ``digest_of`` lets whoever asks often keep the digests it already has.
    """
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(os.path.basename(path).encode())
        digest.update(digest_of(path).encode())
    return {"files": len(paths), "digest": digest.hexdigest()[:_LENGTH]}
