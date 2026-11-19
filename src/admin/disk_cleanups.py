"""
What the Disk page offers to give space back, and doing it (src/admin/routers/disk.py).

- ``server``: watadeploy's cleanup on the host (src/release/host.py ``cleanup``): the older releases' folders and
  images, the images no tag names any more, Docker's build cache no image holds, unused for a day. The admin holds no
  Docker: it writes a request that names nothing but itself (src/admin/release.py ``ask_cleanup``), and watadeploy
  decides what goes, as ``watadeploy prune`` does at each install, and says in its status which releases it would
  remove and why it keeps the others (``Host.releases_said``).
- ``signal_cache``: the Pine runner's answers kept for the runs asked again (the admin's own folder), all of them;
  a run asked again on the same days computes them anew. Not while a backtest or a study runs.
- ``former``: what the former backtester left (its runs, its measures of the turbos), as the Backtests page removes it.

What each would give back is said from the newest sample of the disk and the services' own counts: an estimate,
"up to" for Docker, which says what it gave back once done.
"""

import re

from src.admin.errors import InvalidRequest, NotPossible
from src.disk.kinds import VERSION_FOLDER

# The releases watadeploy's prune keeps, the newest first (src/release/host.py KEEP_RELEASES, held equal by a test):
# not imported, which would bring the deploy's host code into the admin's container.
KEEP_RELEASES = 3

SERVER = "server"
SIGNAL_CACHE = "signal_cache"
FORMER = "former"
ACTIONS = (SERVER, SIGNAL_CACHE, FORMER)

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def releases_to_go(status: dict, kept_bytes: dict[str, int]) -> list[dict]:
    """
    The release folders watadeploy's prune would remove: as its status says (``prunable``, src/release/host.py
    ``releases_said``), or, from a watadeploy that does not say it, as far as the admin can tell: beyond the newest
    ``KEEP_RELEASES`` installed, and none a container runs, is pinned to, or the last apply went from.
    """
    prunable = status.get("prunable")
    if isinstance(prunable, list):
        return [{"version": version, "bytes": kept_bytes.get(version)} for version in prunable
                if isinstance(version, str) and _VERSION.match(version)]
    installed = [version for version in status.get("installed") or [] if isinstance(version, str)
                 and _VERSION.match(version)] or [version for version in kept_bytes if VERSION_FOLDER.match(version)]
    installed = sorted(set(installed), key=_key, reverse=True)
    named = set(installed[:KEEP_RELEASES])
    for entry in status.get("containers") or []:
        if isinstance(entry, dict):
            named.add(entry.get("version"))
            image = entry.get("image") or ""
            named.add(image.rsplit(":", 1)[-1].split("-", 1)[0] if ":" in image else None)
    for step in (status.get("last") or {}).get("steps") or []:
        for image in (step.get("before"), step.get("after")):
            if isinstance(image, str) and ":" in image:
                named.add(image.rsplit(":", 1)[-1].split("-", 1)[0])
    return [{"version": version, "bytes": kept_bytes.get(version)} for version in installed if version not in named]


# Why watadeploy keeps a release (src/release/host.py KEPT_*, not imported for the reason above).
KEPT_WHY = ("newest", "pinned", "created", "rollback")


def releases_kept(status: dict) -> list[dict] | None:
    """The releases watadeploy keeps and why, the newest first, as its status says; None when it does not say."""
    kept = status.get("kept")
    if not isinstance(kept, list):
        return None
    found = []
    for entry in kept:
        if not isinstance(entry, dict) or not isinstance(entry.get("version"), str) \
                or not _VERSION.match(entry["version"]):
            continue
        found.append({"version": entry["version"],
                      "why": [why for why in entry.get("why") or [] if why in KEPT_WHY],
                      "containers": [name for name in entry.get("containers") or [] if isinstance(name, str)]})
    return sorted(found, key=lambda entry: _key(entry["version"]), reverse=True)


class DiskCleanups:
    def __init__(self, *, disk, release=None, backtests=None, studies=None):
        self._disk = disk
        self._release = release
        self._backtests = backtests
        self._studies = studies

    def _busy(self) -> str | None:
        if self._backtests is not None and self._backtests.under_way:
            return "A backtest is under way: its Pine answers are read as it goes"
        if self._studies is not None and self._studies.busy:
            return "A Pine study is under way: its answers are read as it goes"
        return None

    def offers(self) -> list[dict]:
        """Each cleanup the page may ask, what it would give back, and whether it may be asked now."""
        latest = self._disk.latest() or {}
        found = []
        if self._release is not None:
            status = self._release.status()
            docker = latest.get("docker") or {}
            build_cache = (docker.get("reclaimable") or {}).get("build_cache")
            releases = releases_to_go(status, latest.get("releases") or {}) if status else []
            folders = sum(entry["bytes"] or 0 for entry in releases)
            why_not = None
            if not status.get("newest"):
                why_not = "watadeploy has said nothing yet on this server"
            elif self._release.applying(status):
                why_not = "An apply is under way"
            found.append({
                "id": SERVER,
                "bytes": (build_cache or 0) + folders if (build_cache is not None or releases) else None,
                "up_to": True,
                "releases": releases,
                "kept": releases_kept(status) if status else None,
                "build_cache": build_cache,
                "available": why_not is None and self._release.cleanup_waiting(status) is None,
                "why_not": why_not,
                "waiting": self._release.cleanup_waiting(status) if status else None,
                "last": status.get("cleanup"),
            })
        if self._backtests is not None:
            storage = self._backtests.storage()
            cache = storage.get("signal_cache") or {}
            busy = self._busy()
            found.append({"id": SIGNAL_CACHE, "bytes": cache.get("bytes"), "files": cache.get("count"),
                          "limit": cache.get("limit"), "available": busy is None and bool(cache.get("count")),
                          "why_not": busy})
            former = storage.get("former") or {}
            if former.get("bytes"):
                found.append({"id": FORMER, "bytes": former.get("bytes"), "runs": former.get("count"),
                              "available": True, "why_not": None})
        return found

    def run(self, action: str, by: str) -> dict:
        """Do a cleanup, or ask the server for its own; what was done (or asked)."""
        if action not in ACTIONS:
            raise InvalidRequest(f"No cleanup named {action}")
        if action == SERVER:
            if self._release is None:
                raise NotPossible("watadeploy has said nothing yet on this server")
            from src.admin.release import ReleaseRefused

            try:
                return {"action": SERVER, "asked": self._release.ask_cleanup(by)}
            except ReleaseRefused as e:
                raise NotPossible(str(e)) from e
        if self._backtests is None:
            raise NotPossible("Backtests are switched off on this admin (admin.backtests_enabled)")
        if action == SIGNAL_CACHE:
            busy = self._busy()
            if busy:
                raise NotPossible(f"{busy}: empty the cache once it ends")
            return {"action": SIGNAL_CACHE, **self._backtests.clear_signal_cache()}
        before = (self._backtests.storage().get("former") or {}).get("bytes")
        return {"action": FORMER, **self._backtests.delete_former(), "bytes": before}
