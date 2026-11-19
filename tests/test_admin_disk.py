"""
The server's disk in the admin (src/admin/disk.py, src/admin/routers/disk.py): the Disk page's figures and its
projection read from watadisk's samples, the health entry, and Telegram once when the disk turns low or is fine
again. The samples are written as the collector writes them (src/disk/collect.py keep).
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.admin.disk import SAID_FILE, DiskWatch
from src.admin.health import disk_entry
from src.disk import collect, levels
from tests.test_admin_api import (  # noqa: F401 (fixtures)
    PASSWORD,
    SAME_ORIGIN,
    config_path,
    make_client,
    make_ctx,
)

GB = 1_000_000_000
MB = 1_000_000
AT = datetime(2026, 11, 17, 7, 7, tzinfo=timezone.utc)


def _sample(at: datetime, available: int, bars: int) -> dict:
    return {
        "v": 1, "at": at.isoformat(), "took_seconds": 4.2, "errors": {},
        "filesystems": [{"mount": "/", "wata": True, "total": 96 * GB, "used": 96 * GB - available,
                         "free": available, "available": available}],
        "folders": {"": 3 * GB, "var/lib/postgresql": bars + 100 * MB, "var/log": 400 * MB,
                    "var/log/wata-trader": 64 * MB, "var/log/wata-admin": 5 * MB,
                    "var/lib/backtests": 1_100 * MB, "var/lib/backtests/_signal_cache": 1_000 * MB,
                    "var/lib/backtests/runs": 50 * MB, "var/lib/backups": 200 * MB},
        "releases": {"0.87.0": 16 * MB, "0.88.0": 16 * MB},
        "host_folders": {"/var/log": 140 * MB, "/var/lib/docker/containers": 9 * MB},
        "tables": {"price_bar_10s": bars, "turbo_data_position": MB},
        "partitions": {"price_bar_10s": {"price_bar_10s_y2026m11": bars}},
        "docker": {"images": 4 * GB, "build_cache": 4 * GB, "containers": 30 * MB, "volumes": 0,
                   "reclaimable": {"build_cache": 900 * MB}},
        "dumps": [{"file": "wata-2026-11-15-full.dump", "day": "2026-11-15", "full": True, "bytes": 150 * MB}],
        "day_files": {},
    }


def _keep(folder, days: int = 10, available: int = 80 * GB, lost_per_day: int = 35 * MB) -> dict:
    latest = None
    for day in range(days):
        at = AT - timedelta(days=days - 1 - day)
        grown = lost_per_day * day
        latest = collect.keep(str(folder), _sample(at, available - grown, 700 * MB + grown))
    return latest


def _config(tmp_path, reporting=None, **trade) -> str:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"reporting": reporting or {}, "trade": {"config": trade},
                                "admin": {"backtests_signal_cache_mb": 2048}}))
    return str(path)


@pytest.fixture
def watch(tmp_path):
    told, published = [], []

    async def notify(message):
        told.append(message)

    clock = {"now": AT.timestamp() + 600}
    disk = DiskWatch(config_path=_config(tmp_path, price_recorder={"retention_months": 3}),
                     prices_root=str(tmp_path / "prices"), state_folder=str(tmp_path / "admin" / "disk"),
                     root=str(tmp_path / "disk"), notify=notify,
                     publish=lambda event, **data: published.append((event, data)), clock=lambda: clock["now"])
    disk.told, disk.published, disk.clock = told, published, clock
    return disk


def test_the_page_says_each_kind_its_pace_and_the_fourteen_days(watch, tmp_path):
    _keep(tmp_path / "disk")
    (tmp_path / "prices" / "dukascopy" / "_fetch").mkdir(parents=True)
    (tmp_path / "prices" / "dukascopy" / "_fetch" / "keep.json").write_text(json.dumps({"retain_days": 400}))

    found = watch.overview()

    assert found["judged"]["level"] == levels.OK
    assert found["sample"]["samples"] == 10 and found["sample"]["took_seconds"] == 4.2
    saxo = next(kind for kind in found["kinds"] if kind["id"] == "saxo_bars")
    assert saxo["bytes"] == 700 * MB + 9 * 35 * MB and saxo["family"] == "Prices"
    assert saxo["per_day"] == pytest.approx(35 * MB, rel=1e-3) and saxo["pace_from"] == "samples"
    # Seven days of 35 MB.
    assert saxo["week"] == 7 * 35 * MB
    assert saxo["retention"] == 3 and saxo["knob"] == "saxo_retention_months"
    assert found["retentions"]["values"] == {
        "saxo_retention_months": 3, "capital_retention_months": 0, "capital_day_files_days": 0,
        "dukascopy_retain_days": 400, "signal_cache_mb": 2048, "research_keep_days": 30, "ta_monitoring_days": 7}
    assert len(found["series"]) == 10 and found["series"][-1]["available"] == 80 * GB - 9 * 35 * MB
    assert sum(family["bytes"] for family in found["families"]) == 96 * GB - found["series"][-1]["available"]
    details = found["details"]
    assert details["tables"][0] == {"name": "price_bar_10s", "bytes": 700 * MB + 9 * 35 * MB}
    assert [log["service"] for log in details["logs"]] == ["wata-trader", "wata-admin"]
    assert [release["version"] for release in details["releases"]] == ["0.87.0", "0.88.0"]
    assert details["docker"]["reclaimable"] == {"build_cache": 900 * MB}


def test_without_a_sample_the_page_says_so(watch):
    found = watch.overview()

    assert found["judged"]["level"] == levels.NONE and found["sample"] is None and found["kinds"] == []
    with pytest.raises(ValueError, match="No sample"):
        watch.projection({})


def test_a_projection_uses_the_retentions_asked_over_those_set(watch, tmp_path):
    _keep(tmp_path / "disk")

    as_set = watch.projection({}, [365])
    shorter = watch.projection({"saxo_retention_months": 1}, [365])

    assert as_set["retentions"]["saxo_retention_months"] == 3 and as_set["asked"] == {}
    assert shorter["asked"] == {"saxo_retention_months": 1} and shorter["current"]["saxo_retention_months"] == 3
    assert shorter["horizons"][0]["available"] > as_set["horizons"][0]["available"]
    for asked, horizons in (({"saxo_retention_months": 500}, None), ({}, [0]), ({}, [731]), ({"no": 1}, None)):
        with pytest.raises(ValueError):
            watch.projection(asked, horizons)


def test_a_low_disk_is_said_once_when_it_comes_and_once_when_it_goes(watch, tmp_path):
    folder = tmp_path / "disk"
    collect.keep(str(folder), _sample(AT, 40 * GB, 700 * MB))
    asyncio.run(watch.tick())
    # Fine from the start: nothing to say, the pages told.
    assert watch.told == [] and watch.published[-1][1]["level"] == levels.OK

    collect.keep(str(folder), _sample(AT + timedelta(minutes=1), 12 * GB, 700 * MB))
    asyncio.run(watch.tick())
    asyncio.run(watch.tick())
    assert len(watch.told) == 1 and watch.told[0].startswith("⚠️ WATA: Disk: 12.0 GB free of 96.0 GB")

    collect.keep(str(folder), _sample(AT + timedelta(minutes=2), 3 * GB, 700 * MB))
    asyncio.run(watch.tick())
    assert watch.told[-1].startswith("🛑 WATA: Disk: 3.0 GB free") and "critical" in watch.told[-1]

    # A restart of the admin says nothing again.
    assert json.loads((tmp_path / "admin" / "disk" / SAID_FILE).read_text())["level"] == levels.CRITICAL
    collect.keep(str(folder), _sample(AT + timedelta(minutes=3), 40 * GB, 700 * MB))
    asyncio.run(watch.tick())
    assert watch.told[-1].startswith("✅ WATA: Disk: 40.0 GB free") and watch.told[-1].endswith("(fine again)")
    assert len(watch.told) == 3


def test_a_timer_that_stopped_is_said(watch, tmp_path):
    collect.keep(str(tmp_path / "disk"), _sample(AT, 40 * GB, 700 * MB))
    watch.clock["now"] = (AT + timedelta(hours=5)).timestamp()

    judged = asyncio.run(watch.tick())

    assert judged["level"] == levels.STALE and "No sample of the disk for 5 h" in watch.told[0]
    assert disk_entry(judged)[0]["status"] == "warning"


def test_the_health_entry_follows_the_level():
    for level, status in ((levels.OK, "ok"), (levels.WARNING, "warning"), (levels.CRITICAL, "down"),
                          (levels.NONE, "unknown")):
        (entry,) = disk_entry({"level": level, "reason": "why"})
        assert (entry["id"], entry["name"], entry["status"], entry["detail"]) == ("disk", "Disk space", status, "why")


def test_the_routes_read_the_samples_and_refuse_what_cannot_be_projected(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path)
    ctx.disk = DiskWatch(config_path=config_path, prices_root=str(tmp_path / "prices"),
                         state_folder=str(tmp_path / "admin" / "disk"), root=str(tmp_path / "disk"))
    with make_client(ctx, tmp_path / "no-interface") as client:
        assert client.get("/api/system/disk").status_code == 401
        assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
        assert client.get("/api/system/disk").json()["judged"]["level"] == levels.NONE

        _keep(tmp_path / "disk")
        overview = client.get("/api/system/disk").json()
        assert overview["sample"]["samples"] == 10
        projected = client.post("/api/system/disk/projection", headers=SAME_ORIGIN,
                                json={"retentions": {"saxo_retention_months": 2}, "horizons": [30, 90]})
        assert projected.status_code == 200
        assert [horizon["days"] for horizon in projected.json()["horizons"]] == [30, 90]
        refused = client.post("/api/system/disk/projection", headers=SAME_ORIGIN,
                              json={"retentions": {"saxo_retention_months": 999}})
        assert refused.status_code == 422 and "between 0 and 120" in refused.json()["detail"]
        health = client.get("/api/system/health").json()
        assert next(entry for entry in health["services"] if entry["id"] == "disk")["status"] in ("ok", "warning")


def test_the_morning_line_is_said_only_while_the_disk_is_not_fine():
    fine = _sample(AT, 40 * GB, 700 * MB)
    low = _sample(AT, 10 * GB, 700 * MB)

    assert levels.morning_line(fine, {}, AT) is None
    assert levels.morning_line(None, {}, AT) is None
    assert levels.morning_line(low, {}, AT).startswith("⚠️ WATA: Disk: 10.0 GB free of 96.0 GB")
    assert levels.morning_line(low, {"reporting": {"disk_warning_free_percent": 10}}, AT) is None
    assert levels.morning_line(fine, {}, AT + timedelta(hours=6)).startswith("⚠️ WATA: Disk: No sample")


# ── the cleanups ──


from src.admin.disk_cleanups import DiskCleanups, releases_kept, releases_to_go  # noqa: E402
from src.admin.errors import NotPossible  # noqa: E402
from src.admin.release import Release, ReleaseRefused, cleanup_said  # noqa: E402

RELEASE_STATUS = {
    "newest": "0.89.0",
    "installed": ["0.89.0", "0.88.0", "0.87.0", "0.85.0", "0.84.0", "0.61.1"],
    "containers": [{"container": "admin1", "version": "0.88.0", "image": "wata-base:0.88.0"},
                   {"container": "price_recorder1", "version": "0.84.0", "image": "wata-base:0.84.0"}],
    "requests": [],
}


class FakeBacktests:
    def __init__(self):
        self.running = 0
        self.cleared = 0

    @property
    def under_way(self):
        return self.running

    def storage(self):
        return {"signal_cache": {"count": 120, "bytes": 1_100 * MB, "limit": 1024 * 1024 * 1024},
                "former": {"count": 3, "bytes": 2 * MB}}

    def clear_signal_cache(self):
        self.cleared += 1
        return {"removed": 120, "bytes": 1_100 * MB}

    def delete_former(self):
        return {"removed": 3}


class FakeStudies:
    """StudyService's ``busy``: a property, as there."""

    running = False

    @property
    def busy(self):
        return self.running


@pytest.fixture
def cleanups(tmp_path, watch):
    status_path = tmp_path / "release" / "status.json"
    status_path.parent.mkdir()
    status_path.write_text(json.dumps(RELEASE_STATUS))
    release = Release(status_path=str(status_path), requests_path=str(tmp_path / "requests"),
                      announced_path=str(tmp_path / "announced.json"), clock=lambda: AT.timestamp())
    collect.keep(str(tmp_path / "disk"), {**_sample(AT, 40 * GB, 700 * MB), "releases": {
        "0.89.0": 16 * MB, "0.88.0": 16 * MB, "0.87.0": 16 * MB, "0.85.0": 15 * MB, "0.84.0": 15 * MB, "0.61.1": 14 * MB}})
    backtests, studies = FakeBacktests(), FakeStudies()
    found = DiskCleanups(disk=watch, release=release, backtests=backtests, studies=studies)
    found.release, found.backtests, found.studies, found.status_path = release, backtests, studies, status_path
    return found


def test_the_releases_to_go_are_beyond_the_newest_three_and_none_in_use():
    found = releases_to_go(RELEASE_STATUS, {"0.85.0": 15 * MB, "0.61.1": 14 * MB})

    # 0.84.0 is what the recorder runs.
    assert found == [{"version": "0.85.0", "bytes": 15 * MB}, {"version": "0.61.1", "bytes": 14 * MB}]


def test_the_releases_to_go_are_watadeploy_s_own_list_once_it_says_it():
    # watadeploy keeps more than the admin can tell (the last five applies, the infrastructure's compose files):
    # the page offered 0.59.1 to 0.90.0 on 2026-11-19 and the cleanup removed 0.82.0 alone.
    said = {**RELEASE_STATUS, "prunable": ["0.84.0"], "kept": [
        {"version": "0.61.1", "why": ["created", "unknown"], "containers": ["traefik", "postgres"]},
        {"version": "0.89.0", "why": ["newest", "pinned"], "containers": ["admin1"]},
        {"version": "../etc", "why": ["newest"], "containers": []}]}

    assert releases_to_go(said, {"0.84.0": 15 * MB}) == [{"version": "0.84.0", "bytes": 15 * MB}]
    assert releases_to_go({**said, "prunable": []}, {}) == []
    assert releases_kept(said) == [
        {"version": "0.89.0", "why": ["newest", "pinned"], "containers": ["admin1"]},
        {"version": "0.61.1", "why": ["created"], "containers": ["traefik", "postgres"]}]
    # From a watadeploy that does not say it: worked out as before, and nothing said of what is kept.
    assert releases_to_go({**said, "prunable": None}, {}) == releases_to_go(RELEASE_STATUS, {})
    assert releases_kept(RELEASE_STATUS) is None


def test_each_cleanup_says_what_it_would_give_back(cleanups):
    offers = {offer["id"]: offer for offer in cleanups.offers()}

    server = offers["server"]
    assert [entry["version"] for entry in server["releases"]] == ["0.85.0", "0.61.1"]
    assert server["build_cache"] == 900 * MB and server["bytes"] == 900 * MB + 29 * MB and server["up_to"] is True
    assert server["available"] is True and server["waiting"] is None
    assert offers["signal_cache"]["bytes"] == 1_100 * MB and offers["signal_cache"]["available"] is True
    assert offers["former"]["runs"] == 3

    cleanups.backtests.running = 1
    offers = {offer["id"]: offer for offer in cleanups.offers()}
    assert offers["signal_cache"]["available"] is False and "backtest" in offers["signal_cache"]["why_not"]
    with pytest.raises(NotPossible, match="backtest is under way"):
        cleanups.run("signal_cache", "operator")
    cleanups.backtests.running, cleanups.studies.running = 0, True
    with pytest.raises(NotPossible, match="study"):
        cleanups.run("signal_cache", "operator")
    cleanups.studies.running = False
    assert cleanups.run("signal_cache", "operator") == {"action": "signal_cache", "removed": 120, "bytes": 1_100 * MB}


def test_the_server_s_cleanup_is_a_request_naming_nothing_but_itself_and_one_at_a_time(cleanups, tmp_path):
    done = cleanups.run("server", "the Disk page, 10.0.0.1")
    (name,) = (tmp_path / "requests").iterdir()
    request = json.loads(name.read_text())
    assert set(request) == {"id", "action", "asked_by", "asked_at"} and request["action"] == "cleanup"
    assert done["asked"] == request
    offer = next(offer for offer in cleanups.offers() if offer["id"] == "server")
    assert offer["available"] is False and offer["waiting"]["state"] == "asked"
    with pytest.raises(NotPossible, match="asked already"):
        cleanups.run("server", "operator")

    # Taken and done by watadeploy.
    name.unlink()
    status = {**RELEASE_STATUS, "requests": [{"id": request["id"], "action": "cleanup", "state": "done"}],
              "cleanup": {"id": "c1", "by": "admin (the Disk page)", "removed": ["0.85.0"], "build_cache_bytes": 902_600_000,
                          "images_bytes": 4_857, "finished_at": AT.isoformat()}}
    cleanups.status_path.write_text(json.dumps(status))
    offer = next(offer for offer in cleanups.offers() if offer["id"] == "server")
    assert offer["available"] is True and offer["last"]["id"] == "c1"
    with pytest.raises(Exception):
        cleanups.run("everything", "operator")


def test_a_cleanup_is_said_once_on_telegram(cleanups):
    record = {"id": "c1", "by": "admin (the Disk page)", "removed": ["0.85.0", "0.61.1"], "build_cache_bytes": 902_600_000,
              "images_bytes": 4_857, "finished_at": AT.isoformat()}
    status = {**RELEASE_STATUS, "cleanup": record}

    first = cleanups.release.news(status, {}, AT.timestamp() + 60)
    assert first == [cleanup_said(record)]
    assert "Releases removed: 0.85.0, 0.61.1" in first[0] and "902.6 MB given back" in first[0]
    assert cleanups.release.news(status, {}, AT.timestamp() + 120) == []
    failed = cleanup_said({"by": "watadeploy", "error": "Another apply is under way: one at a time"})
    assert failed.startswith("⚠️ WATA: the cleanup asked by watadeploy was not made")


def test_no_cleanup_is_asked_during_an_apply(cleanups):
    cleanups.status_path.write_text(json.dumps({**RELEASE_STATUS, "applying": {"id": "a1", "done": False}}))
    with pytest.raises(ReleaseRefused, match="apply is under way"):
        cleanups.release.ask_cleanup("operator")
    assert next(offer for offer in cleanups.offers() if offer["id"] == "server")["why_not"] == "An apply is under way"


def test_the_cleanup_routes_are_operator_actions(config_path, tmp_path, cleanups):
    ctx = make_ctx(config_path, tmp_path)
    ctx.disk_cleanups = cleanups
    audited = []
    ctx.audit = lambda request, action, detail="": audited.append(action)
    with make_client(ctx, tmp_path / "no-interface") as client:
        assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
        listed = client.get("/api/system/disk/cleanups").json()
        assert [item["id"] for item in listed["items"]] == ["server", "signal_cache", "former"]
        asked = client.post("/api/system/disk/cleanups/server", headers=SAME_ORIGIN)
        assert asked.status_code == 200 and asked.json()["asked"]["action"] == "cleanup"
        assert audited[-1] == "disk cleanup on the server"
        assert client.post("/api/system/disk/cleanups/server", headers=SAME_ORIGIN).status_code == 409
        emptied = client.post("/api/system/disk/cleanups/signal_cache", headers=SAME_ORIGIN)
        # The admin's own cache: not an action on the server, as the Backtests page's removals are not.
        assert emptied.json()["bytes"] == 1_100 * MB and audited.count("disk cleanup on the server") == 1
        assert client.post("/api/system/disk/cleanups/nothing", headers=SAME_ORIGIN).status_code == 422
        ctx.settings.actions_enabled = False
        assert client.post("/api/system/disk/cleanups/former", headers=SAME_ORIGIN).status_code == 403


def test_the_fakes_are_shaped_as_the_services_they_stand_for():
    import inspect

    from src.admin.backtests import BacktestService
    from src.admin.studies import StudyService

    # Both are read as properties by the cleanups: a fake with a method in their place would hide a call of one.
    assert isinstance(inspect.getattr_static(BacktestService, "under_way"), property)
    assert isinstance(inspect.getattr_static(StudyService, "busy"), property)
    assert isinstance(inspect.getattr_static(FakeBacktests, "under_way"), property)
    assert isinstance(inspect.getattr_static(FakeStudies, "busy"), property)


def test_the_releases_the_page_says_go_are_counted_as_watadeploy_counts_them():
    from src.admin import disk_cleanups
    from src.release import host

    assert disk_cleanups.KEEP_RELEASES == host.KEEP_RELEASES
    assert set(disk_cleanups.KEPT_WHY) == {host.KEPT_NEWEST, host.KEPT_PINNED, host.KEPT_CREATED, host.KEPT_ROLLBACK}

