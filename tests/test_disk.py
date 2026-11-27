"""
The disk's samples (src/disk/): what the hourly collector measures and keeps, the kinds read from a sample,
the alarm levels, and the projection under retentions. The collector's commands (Docker, psql) are replaced;
its folders are real ones under tmp_path.
"""

import json
import os
import stat
from datetime import date, datetime, timedelta, timezone

import pytest

from src.disk import collect, kinds, levels, projection
from src.disk.levels import Thresholds

AT = datetime(2026, 11, 17, 7, 7, tzinfo=timezone.utc)
GB = 1_000_000_000
MB = 1_000_000

DOCKER_DF = "\n".join(json.dumps(row) for row in (
    {"Type": "Images", "TotalCount": "10", "Active": "8", "Size": "4.035GB", "Reclaimable": "4.857kB (0%)"},
    {"Type": "Containers", "TotalCount": "16", "Active": "15", "Size": "30.77MB", "Reclaimable": "3.277MB (10%)"},
    {"Type": "Local Volumes", "TotalCount": "12", "Active": "1", "Size": "0B", "Reclaimable": "0B"},
    {"Type": "Build Cache", "TotalCount": "78", "Active": "0", "Size": "4.396GB", "Reclaimable": "902.6MB"},
))
TABLES = "\n".join("\t".join(row) for row in (
    ("price_bar_10s", "price_bar_10s_y2026m10", "462000000"),
    ("price_bar_10s", "price_bar_10s_y2026m11", "424000000"),
    ("price_recorder_coverage", "price_recorder_coverage", "19000000"),
    ("capital_bar_10s", "capital_bar_10s_y2026m11", "17000000"),
    ("ta_rule_run", "ta_rule_run", "23000000"),
    ("turbo_data_position", "turbo_data_position", "288000"),
))


def _write(path, size: int) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as file:
        file.write(os.urandom(size))


def _runner(answers: dict[str, str]):
    """A stand-in for Docker: each command answered by its first matching word, the others refused."""
    asked = []

    def run(arguments, timeout=collect.COMMAND_SECONDS):
        asked.append(arguments)
        text = " ".join(arguments)
        for word, answer in answers.items():
            if word in text:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise RuntimeError(f"not answered: {text}")

    run.asked = asked
    return run


def _base(tmp_path):
    base = tmp_path / "wata"
    _write(base / "var/lib/backtests/runs/20261117-070000-abcdef/summary.json", 300_000)
    _write(base / "var/lib/backtests/_signal_cache/answer.json", 200_000)
    _write(base / "var/lib/backtests/paper/index.json", 10_000)
    _write(base / "var/log/wata-trader-async/wata-trader-async.log", 100_000)
    _write(base / "var/log/wata-admin/audit.jsonl", 5_000)
    _write(base / "var/lib/backups/wata-2026-11-15-full.dump", 60_000)
    _write(base / "var/lib/backups/wata-2026-11-16.dump", 20_000)
    _write(base / "var/lib/backups/last.json", 100)
    _write(base / "var/lib/price_history/dukascopy/bars/us100/2026-11-03.csv.gz", 10_000)
    _write(base / "var/lib/price_history/dukascopy/bars/us100/2026-11-05.csv.gz", 10_000)
    _write(base / "var/lib/price_history/dukascopy/bars/de40/2026-10-30.csv.gz", 10_000)
    _write(base / "var/lib/price_history/capital/bars/us100/2026-11-14.csv.gz", 4_000)
    _write(base / "0.88.0/deploy/docker-compose.yml", 1_100_000)
    _write(base / "release/history.jsonl", 2_000)
    os.symlink(base / "var/lib/backtests", base / "var/lib/linked")
    return base


def _collector(base, answers=None, clock=lambda: AT):
    return collect.Collector(str(base), docker="docker", runner=_runner(answers if answers is not None else {
        "info": "/var/lib/docker\n", "system df": DOCKER_DF, "pg_total_relation_size": TABLES,
        "pg_database_size": "990000000\n",
    }), clock=clock)


# ── the collector ──


def test_a_sample_measures_the_disk_the_folders_docker_and_the_database(tmp_path):
    base = _base(tmp_path)
    sample = _collector(base).sample()

    assert sample["v"] == collect.VERSION and sample["at"] == AT.isoformat() and sample["errors"] == {}
    (system,) = [system for system in sample["filesystems"] if system["wata"]]
    assert system["total"] > 0 and 0 < system["available"] <= system["free"] <= system["total"]
    folders = sample["folders"]
    # Every named folder, 0 when absent; a link is not followed.
    assert folders["var/lib/postgresql"] == 0 and "var/lib/linked" not in folders
    assert folders["var/lib/backtests/runs"] >= 300_000 and folders["var/lib/backtests/_signal_cache"] >= 200_000
    assert folders["var/lib/backtests"] >= folders["var/lib/backtests/runs"] + folders["var/lib/backtests/_signal_cache"]
    # Each service's log folder, whatever its size.
    assert {"var/log/wata-trader-async", "var/log/wata-admin"} <= set(folders)
    assert folders[""] >= folders["var/lib/backtests"]
    assert set(sample["releases"]) == {"0.88.0"} and not any(path.startswith("0.88.0") for path in folders)
    assert [(dump["file"], dump["full"], dump["day"]) for dump in sample["dumps"]] == [
        ("wata-2026-11-15-full.dump", True, "2026-11-15"), ("wata-2026-11-16.dump", False, "2026-11-16")]
    assert sample["day_files"]["dukascopy"] == {
        "days": 3, "first": "2026-10-30", "last": "2026-11-05",
        "labels": {"de40": {"days": 1, "first": "2026-10-30", "last": "2026-10-30"},
                   "us100": {"days": 2, "first": "2026-11-03", "last": "2026-11-05"}}}
    assert sample["day_files"]["capital_day_files"]["days"] == 1
    assert sample["docker"] == {"images": 4_035_000_000, "containers": 30_770_000, "volumes": 0,
                                "build_cache": 4_396_000_000,
                                "reclaimable": {"images": 4_857, "containers": 3_277_000, "volumes": 0,
                                                "build_cache": 902_600_000}}
    assert sample["tables"] == {"price_bar_10s": 886_000_000, "price_recorder_coverage": 19_000_000,
                                "capital_bar_10s": 17_000_000, "ta_rule_run": 23_000_000,
                                "turbo_data_position": 288_000}
    assert sample["partitions"] == {
        "price_bar_10s": {"price_bar_10s_y2026m10": 462_000_000, "price_bar_10s_y2026m11": 424_000_000},
        "capital_bar_10s": {"capital_bar_10s_y2026m11": 17_000_000}}
    assert sample["database_bytes"] == 990_000_000


def test_the_build_cache_is_what_no_image_holds_the_layers_it_shares_being_counted_with_the_images(tmp_path):
    # docker buildx du on the server, 2026-11-19 (containerd's image store): 4.0 of its 5.5 GB were the images' own.
    buildx = ("ID\t\t\t\t\tRECLAIMABLE\tSIZE\t\tLAST ACCESSED\n"
              "oppbzsba4754y9p10okj7x0og*\ttrue\t\t933.2MB\t\t23 hours ago\n"
              "Shared:\t\t4.017GB\nPrivate:\t1.527GB\nReclaimable:\t5.544GB\nTotal:\t\t5.544GB\n")
    df = DOCKER_DF.replace("4.396GB", "5.544GB")
    sample = _collector(_base(tmp_path), {"info": "/var/lib/docker\n", "system df": df, "buildx du": buildx,
                                          "pg_total_relation_size": TABLES, "pg_database_size": "990000000\n"}).sample()

    docker = sample["docker"]
    assert (docker["build_cache"], docker["build_cache_shared"], docker["build_cache_total"]) == (
        1_527_000_000, 4_017_000_000, 5_544_000_000)
    assert docker["reclaimable"]["build_cache"] == 902_600_000
    assert collect.build_cache_shared("Reclaimable:\t12MB\nTotal:\t12MB\n") == (0, 12_000_000)
    assert collect.build_cache_shared("buildx: unknown command") == (None, None)


def test_a_source_that_fails_is_said_and_the_rest_measured(tmp_path):
    base = _base(tmp_path)
    sample = _collector(base, {"info": "/var/lib/docker\n", "system df": RuntimeError("docker system df failed: x"),
                               "psql": RuntimeError("docker exec postgres failed: No such container")}).sample()

    assert set(sample["errors"]) == {"docker", "database"}
    assert "No such container" in sample["errors"]["database"]
    assert sample["docker"] is None and "tables" not in sample
    assert sample["folders"]["var/lib/backtests/runs"] >= 300_000


def test_the_database_is_asked_through_docker_as_wata(tmp_path):
    runner = _runner({"info": "/var/lib/docker\n", "system df": DOCKER_DF, "pg_total_relation_size": TABLES,
                      "pg_database_size": "1\n"})
    collect.Collector(str(_base(tmp_path)), docker="docker", runner=runner, clock=lambda: AT).sample()

    psql = [arguments for arguments in runner.asked if "psql" in arguments]
    assert psql and all(arguments[:8] == ["docker", "exec", "postgres", "psql", "-X", "-q", "-U", "wata"]
                        for arguments in psql)
    # Reads alone.
    assert all(arguments[-1].lstrip().upper().startswith("SELECT") for arguments in psql)


@pytest.mark.parametrize("text, expected", [
    ("4.035GB", 4_035_000_000), ("4.857kB (0%)", 4_857), ("0B", 0), ("902.6MB", 902_600_000),
    ("1.5GiB", int(1.5 * 2**30)), ("12B", 12), ("nothing", None),
])
def test_docker_sizes_are_read_as_docker_writes_them(text, expected):
    assert collect.docker_bytes(text) == expected


def test_fourteen_days_are_kept_and_the_newest_says_the_pace_of_the_free_space(tmp_path):
    folder = tmp_path / "disk"
    available = 80 * GB
    for hours in range(0, 16 * 24, 6):
        at = AT + timedelta(hours=hours)
        latest = collect.keep(str(folder), {"at": at.isoformat(), "filesystems": [
            {"wata": True, "total": 96 * GB, "available": available - hours * 50 * MB, "used": 0}]})

    kept = collect.read_samples(str(folder / collect.SAMPLES))
    assert collect.moment(kept[0]["at"]) >= collect.moment(latest["at"]) - timedelta(days=collect.KEEP_DAYS)
    assert len(kept) == 14 * 4 + 1
    # 50 MB an hour.
    assert latest["trend"]["available_per_day"] == pytest.approx(-1_200 * MB, rel=1e-6)
    assert latest["trend"]["over_days"] == 14.0 and latest["trend"]["samples"] == len(kept)
    assert json.loads((folder / collect.LATEST).read_text()) == latest
    assert not list(folder.glob("*.part"))


def test_under_a_day_of_samples_no_pace_is_said(tmp_path):
    for hours in range(3):
        latest = collect.keep(str(tmp_path), {"at": (AT + timedelta(hours=hours)).isoformat(), "filesystems": [
            {"wata": True, "total": GB, "available": GB - hours * MB}]})

    assert latest["trend"]["available_per_day"] is None


def test_a_line_cut_short_is_skipped(tmp_path):
    (tmp_path / collect.SAMPLES).write_text(json.dumps({"at": AT.isoformat()}) + "\n{\"at\": \"2026-11\n")

    assert len(collect.read_samples(str(tmp_path / collect.SAMPLES))) == 1


def _hourly(hours: int = 0) -> dict:
    return {"at": (AT + timedelta(hours=hours)).isoformat(), "filesystems": [
        {"wata": True, "total": GB, "available": GB - hours * MB}]}


def test_a_link_left_where_the_readers_read_is_replaced_and_never_written_through(tmp_path):
    # Five containers write in var/lib as root; this runs as root on the host.
    own, readers = tmp_path / "var/disk", tmp_path / "var/lib/disk"
    readers.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("a file of the host")
    names = (collect.SAMPLES, collect.LATEST, f"{collect.SAMPLES}.part", f"{collect.LATEST}.part", collect.LOCK)
    for name in names:
        os.symlink(victim, readers / name)

    latest = collect.keep(str(own), _hourly(), imported_from=str(readers))
    collect.publish(str(own), str(readers))

    assert victim.read_text() == "a file of the host"
    for name in (collect.SAMPLES, collect.LATEST):
        assert not (readers / name).is_symlink()
        assert (readers / name).read_text() == (own / name).read_text()
    assert json.loads((readers / collect.LATEST).read_text()) == latest
    assert not [path for path in readers.iterdir() if path.name.endswith(".part") and not path.is_symlink()]


def test_the_samples_gone_on_from_are_the_collector_s_own_never_the_readers(tmp_path):
    own, readers = tmp_path / "var/disk", tmp_path / "var/lib/disk"
    readers.mkdir(parents=True)
    # Kept where the readers read until the collector kept its own: taken once.
    (readers / collect.SAMPLES).write_text(json.dumps(_hourly(0)) + "\n")
    collect.keep(str(own), _hourly(1), imported_from=str(readers))
    assert len(collect.read_samples(str(own / collect.SAMPLES))) == 2
    collect.publish(str(own), str(readers))

    # Anything written there since is never read back.
    forged = {**_hourly(2), "forged": True}
    (readers / collect.SAMPLES).write_text(json.dumps(forged) + "\n")
    collect.keep(str(own), _hourly(3), imported_from=str(readers))
    assert [sample.get("forged") for sample in collect.read_samples(str(own / collect.SAMPLES))] == [None] * 3
    assert stat.S_IMODE(own.stat().st_mode) == 0o700


def test_what_is_not_a_plain_file_is_never_read_and_a_link_for_the_folder_never_entered(tmp_path):
    import threading

    own, readers = tmp_path / "var/disk", tmp_path / "var/lib/disk"
    readers.mkdir(parents=True)
    # A pipe would hold a reader for good.
    os.mkfifo(readers / collect.SAMPLES)
    done = threading.Thread(target=collect.keep, args=(str(own), _hourly()), kwargs={"imported_from": str(readers)},
                            daemon=True)
    done.start()
    done.join(5)
    assert not done.is_alive()
    assert len(collect.read_samples(str(own / collect.SAMPLES))) == 1

    # The readers' folder itself made a link to another folder of the host: nothing is written there.
    elsewhere = tmp_path / "etc"
    elsewhere.mkdir()
    os.remove(readers / collect.SAMPLES)
    readers.rmdir()
    os.symlink(elsewhere, readers)
    with pytest.raises(OSError):
        collect.publish(str(own), str(readers))
    assert list(elsewhere.iterdir()) == []


def test_now_says_what_it_kept_when_the_readers_folder_is_not_a_folder(tmp_path, monkeypatch, capsys):
    base = _base(tmp_path)
    monkeypatch.setattr(collect, "run", _runner({"info": "/var/lib/docker\n", "system df": DOCKER_DF,
                                                 "pg_total_relation_size": TABLES, "pg_database_size": "1\n"}))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (base / "var/lib").mkdir(parents=True, exist_ok=True)
    os.symlink(elsewhere, base / "var/lib/disk")

    assert collect.main(["now", "--base", str(base)]) == 1
    assert "not copied to" in capsys.readouterr().err
    assert (base / "var/disk" / collect.SAMPLES).exists() and list(elsewhere.iterdir()) == []


def test_now_measures_keeps_and_says_it_in_short(tmp_path, monkeypatch, capsys):
    base = _base(tmp_path)
    monkeypatch.setattr(collect, "run", _runner({"info": "/var/lib/docker\n", "system df": DOCKER_DF,
                                                 "pg_total_relation_size": TABLES, "pg_database_size": "1\n"}))

    assert collect.main(["now", "--base", str(base)]) == 0
    said = capsys.readouterr().out
    assert "free of" in said and "Backtest runs" in said and "Not measured" not in said
    assert (base / "var/lib/disk/samples.jsonl").exists()
    assert collect.main(["show", "--base", str(base)]) == 0
    assert "Backtest runs" in capsys.readouterr().out


# ── kinds ──


def _sample(**changes) -> dict:
    sample = {
        "at": AT.isoformat(),
        "filesystems": [{"wata": True, "total": 96 * GB, "used": 13 * GB, "free": 83 * GB, "available": 79 * GB}],
        "folders": {
            "": 3_300 * MB, "var/lib/postgresql": 921 * MB, "var/log": 474 * MB, "var/log/wata-trader": 64 * MB,
            "var/log/wata-admin": 6 * MB, "var/lib/backups": 197 * MB, "var/lib/admin": 9 * MB,
            "var/lib/backtests": 1_140 * MB, "var/lib/backtests/runs": 39 * MB,
            "var/lib/backtests/_signal_cache": 1_100 * MB, "var/lib/price_history/dukascopy": 424 * MB,
            "var/lib/price_history/capital": 4 * MB, "var/lib/price_history/research": 0,
            "var/lib/price_export": 0, "release": 2 * MB,
        },
        "releases": {"0.87.0": 16 * MB, "0.88.0": 16 * MB},
        "host_folders": {"/var/log": 144 * MB, "/var/lib/docker/containers": 9 * MB},
        "tables": {"price_bar_10s": 730 * MB, "price_recorder_coverage": 19 * MB, "capital_bar_10s": 27 * MB,
                   "capital_coverage": 18 * MB, "ta_engine_tick": 14 * MB, "ta_rule_run": 23 * MB,
                   "turbo_data_position": 1 * MB},
        "partitions": {"price_bar_10s": {"price_bar_10s_y2026m10": 462 * MB, "price_bar_10s_y2026m11": 268 * MB}},
        "docker": {"images": 4_035 * MB, "build_cache": 4_396 * MB, "containers": 31 * MB, "volumes": 0},
        "dumps": [{"file": "wata-2026-11-15-full.dump", "full": True, "bytes": 150 * MB}],
        "day_files": {"dukascopy": {"days": 90, "first": "2026-07-01", "last": "2026-11-14"}},
    }
    return {**sample, **changes}


def test_each_kind_is_read_from_what_was_measured():
    found = kinds.sizes(_sample())

    assert found["saxo_bars"] == 749 * MB and found["capital_bars"] == 45 * MB and found["ta_monitoring"] == 37 * MB
    assert found["trading_records"] == 1 * MB
    # What the tables do not take of PostgreSQL's folder: the WAL, the catalog.
    assert found["database_other"] == (921 - 749 - 45 - 37 - 1) * MB
    assert found["backtests_other"] == 1 * MB and found["releases"] == 34 * MB
    assert found["docker_build_cache"] == 4_396 * MB and found["container_logs"] == 9 * MB
    in_wata = sum(found[name] for name in (
        "capital_day_files", "dukascopy", "price_export", "backtest_runs", "signal_cache", "research_export",
        "backtests_other", "backups", "wata_logs", "admin_files", "releases"))
    assert found["wata_other"] == 3_300 * MB - 921 * MB - in_wata
    # The disk's used space less everything measured.
    assert found["system"] == 13 * GB - sum(value for name, value in found.items() if name != "system")
    assert set(found) == {kind.id for kind in kinds.KINDS}
    assert sum(kinds.families(found).values()) == 13 * GB


def test_what_was_not_measured_is_none_and_never_negative():
    found = kinds.sizes(_sample(tables=None, docker=None))

    assert found["saxo_bars"] is None and found["database_other"] is None and found["docker_images"] is None
    # PostgreSQL's folder is still counted as the database's, not as other files.
    assert found["wata_other"] >= 0 and found["system"] >= 0


def test_the_logs_may_take_each_service_s_rotation():
    assert kinds.log_cap(_sample()) == 2 * kinds.LOG_CAP_BYTES


# ── levels ──


def _latest(available_percent: float, per_day=None, at=AT) -> dict:
    total = 100 * GB
    return {"at": at.isoformat(), "filesystems": [{"wata": True, "total": total,
                                                   "available": int(total * available_percent / 100)}],
            "trend": {"available_per_day": per_day}}


@pytest.mark.parametrize("percent, per_day, level", [
    (40, None, levels.OK),
    (40, -0.1 * GB, levels.OK),
    # 35 GB to lose at 2.5 GB a day: 14 days.
    (40, -2.5 * GB, levels.WARNING),
    (14, None, levels.WARNING),
    (4, None, levels.CRITICAL),
])
def test_the_disk_is_judged_against_the_levels_and_the_pace(percent, per_day, level):
    judged = levels.judge(_latest(percent, per_day), Thresholds(), AT + timedelta(minutes=30))

    assert judged["level"] == level
    assert judged["free_percent"] == pytest.approx(percent)


def test_days_to_the_critical_level_are_said_at_the_pace_of_the_last_days():
    judged = levels.judge(_latest(25, -2 * GB), Thresholds(), AT)

    assert judged["days_to_critical"] == 10.0
    assert "10 days to the critical level" in judged["reason"]
    assert levels.said(judged).startswith("Disk: 25.0 GB free of 100.0 GB (25.0 %)")


def test_an_old_sample_is_stale_and_no_sample_is_none():
    assert levels.judge(_latest(40), Thresholds(), AT + timedelta(hours=4))["level"] == levels.STALE
    assert levels.judge(None, Thresholds(), AT)["level"] == levels.NONE


def test_the_levels_are_read_from_reporting_and_checked():
    config = {"reporting": {"disk_warning_free_percent": 20, "disk_critical_free_percent": 8,
                            "disk_warning_days_left": 0}}
    assert Thresholds.from_config(config) == Thresholds(20, 8, 0)
    assert Thresholds.from_config({}) == Thresholds(15, 5, 14)
    # Off: the pace alone does not warn.
    assert levels.judge(_latest(40, -10 * GB), Thresholds(20, 8, 0), AT)["level"] == levels.OK
    assert levels.thresholds_refusal(config["reporting"]) is None
    assert levels.thresholds_refusal(None) is None
    for wrong in ({"disk_warning_free_percent": 0}, {"disk_critical_free_percent": 51},
                  {"disk_warning_days_left": 366}, {"disk_warning_days_left": True}, {"disk_warning_free_percent": "15"}):
        assert "reporting.disk_" in levels.thresholds_refusal(wrong)


# ── projection ──


def _daily(days: int, grow: dict[str, int], **changes) -> list[dict]:
    """A sample a day, each table or folder of ``grow`` growing by its bytes a day, the disk losing as much."""
    found = []
    for day in range(days):
        sample = _sample(**changes)
        sample = json.loads(json.dumps(sample))
        sample["at"] = (AT - timedelta(days=days - 1 - day)).isoformat()
        lost = 0
        for name, per_day in grow.items():
            back = per_day * (days - 1 - day)
            if name in sample["tables"]:
                sample["tables"][name] -= back
                # The table's files are in PostgreSQL's folder.
                sample["folders"]["var/lib/postgresql"] -= back
            else:
                sample["folders"][name] -= back
                for parent in [path for path in sample["folders"] if path and name.startswith(path + "/")]:
                    sample["folders"][parent] -= back
            sample["folders"][""] -= back
            lost += back
        sample["filesystems"][0]["available"] += lost
        sample["filesystems"][0]["used"] -= lost
        found.append(sample)
    return found


def test_without_retention_the_bars_grow_and_the_disk_fills_at_their_pace():
    samples = _daily(7, {"price_bar_10s": 35 * MB})
    found = projection.project(samples, projection.RETENTIONS, Thresholds(), horizons=(30, 365))

    saxo = next(kind for kind in found["kinds"] if kind["id"] == "saxo_bars")
    assert saxo["per_day"] == pytest.approx(35 * MB, rel=1e-3) and saxo["pace_from"] == projection.FROM_SAMPLES
    assert saxo["steady"] is None
    in_a_year = found["horizons"][1]
    assert in_a_year["kinds"]["saxo_bars"] == pytest.approx(749 * MB + 365 * 35 * MB, rel=1e-3)
    # The bars, and the Sunday dump that holds them (150 MB for 794 MB of bars).
    a_day = 35 * MB * (1 + 150 / 794)
    assert found["available"] - in_a_year["available"] == pytest.approx(365 * a_day, rel=1e-2)
    assert found["series"][0]["available"] == found["available"] and len(found["series"]) == 366
    # 79 GB free; the critical level is 5 % of 96 GB.
    assert found["days_to_critical"] == pytest.approx((79 * GB - 0.05 * 96 * GB) / a_day, abs=1)


def test_a_retention_of_months_keeps_the_months_before_and_the_current_one():
    samples = _daily(7, {"price_bar_10s": 35 * MB})
    retentions = {**projection.RETENTIONS, "saxo_retention_months": 2}
    found = projection.project(samples, retentions, Thresholds(), horizons=(365,))

    saxo = next(kind for kind in found["kinds"] if kind["id"] == "saxo_bars")
    # 2 months, then 3 just before the oldest goes.
    assert saxo["steady"] == [pytest.approx(35 * MB * 2 * 30.44, rel=1e-3), pytest.approx(35 * MB * 3 * 30.44, rel=1e-3)]
    # 17 November 2027: from 1 September 2027.
    kept = (date(2027, 11, 17) - date(2027, 9, 1)).days + 1
    assert found["horizons"][0]["kinds"]["saxo_bars"] == pytest.approx(35 * MB * kept, rel=1e-3)


def test_a_retention_shorter_than_what_is_held_frees_it_the_next_day():
    held = {**_sample()["tables"], "price_bar_10s": 5_000 * MB}
    samples = _daily(7, {"price_bar_10s": 35 * MB}, tables=held)
    found = projection.project(samples, {**projection.RETENTIONS, "saxo_retention_months": 0}, Thresholds(),
                               horizons=(1,))
    kept_all = found["horizons"][0]["available"]
    shorter = projection.project(samples, {**projection.RETENTIONS, "saxo_retention_months": 1}, Thresholds(),
                                 horizons=(1,))

    # On 18 November, from 1 October: 49 days of 35 MB instead of 5 GB and one more day.
    assert shorter["horizons"][0]["kinds"]["saxo_bars"] == pytest.approx(49 * 35 * MB, rel=1e-3)
    assert shorter["horizons"][0]["available"] > kept_all


def test_days_kept_as_files_settle_at_their_size_per_day_held():
    # 424 MB over the 137 days from 1 July to 14 November.
    found = projection.project([_sample()], {**projection.RETENTIONS, "dukascopy_retain_days": 30},
                               Thresholds(), horizons=(30,))

    dukascopy = next(kind for kind in found["kinds"] if kind["id"] == "dukascopy")
    assert dukascopy["pace_from"] == projection.FROM_DAYS_HELD
    assert dukascopy["steady"][0] == pytest.approx(424 * MB / 137 * 30, rel=1e-3)
    assert found["horizons"][0]["kinds"]["dukascopy"] == pytest.approx(424 * MB / 137 * 30, rel=1e-3)


def test_a_cache_stays_under_its_size():
    samples = _daily(7, {"var/lib/backtests/_signal_cache": 20 * MB})
    found = projection.project(samples, {**projection.RETENTIONS, "signal_cache_mb": 2048}, Thresholds(),
                               horizons=(30, 365))

    assert found["horizons"][0]["kinds"]["signal_cache"] == pytest.approx(1_100 * MB + 30 * 20 * MB, rel=1e-3)
    assert found["horizons"][1]["kinds"]["signal_cache"] == 2048 * 1024 * 1024


def test_the_sunday_dumps_grow_with_the_bars():
    samples = _daily(7, {"price_bar_10s": 35 * MB})
    found = projection.project(samples, projection.RETENTIONS, Thresholds(), horizons=(100,))

    bars_now = 749 * MB + 45 * MB
    grown = 100 * 35 * MB
    # One Sunday dump of 150 MB for 794 MB of bars.
    assert found["horizons"][0]["kinds"]["backups"] == pytest.approx(197 * MB + 150 * MB / bars_now * grown, rel=1e-2)


def test_a_kind_whose_pace_is_not_known_yet_stays_as_it_is():
    found = projection.project([_sample()], projection.RETENTIONS, Thresholds(), horizons=(90,))

    runs = next(kind for kind in found["kinds"] if kind["id"] == "backtest_runs")
    assert runs["per_day"] is None and found["horizons"][0]["kinds"]["backtest_runs"] == 39 * MB
    # The bars' pace from their newest month: November's 268 MB over 17 days, October's 462 MB over 31.
    saxo = next(kind for kind in found["kinds"] if kind["id"] == "saxo_bars")
    assert saxo["pace_from"] == projection.FROM_PARTITIONS
    assert saxo["per_day"] == pytest.approx(268 * MB / 17 * 749 / 730, rel=1e-3)


def test_the_retentions_asked_are_checked():
    current = {"saxo_retention_months": 3}
    assert projection.checked_retentions({"dukascopy_retain_days": 60}, current)["saxo_retention_months"] == 3
    for asked in ({"nothing": 1}, {"signal_cache_mb": 10}, {"saxo_retention_months": 1.5},
                  {"saxo_retention_months": True}):
        with pytest.raises(ValueError):
            projection.checked_retentions(asked, current)


def test_kept_days_follow_the_recorders_rule():
    # drop_month_partitions: the months that began before month_start(today, -months) go.
    assert projection.kept_days(kinds.MONTHS, 1, date(2026, 11, 17)) == 48
    assert projection.kept_days(kinds.MONTHS, 0, date(2026, 11, 17)) is None
    assert projection.kept_days(kinds.DAYS, 30, date(2026, 11, 17)) == 30
