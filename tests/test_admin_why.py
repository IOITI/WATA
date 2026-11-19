"""
Why a run wins or loses, as the admin works it out: its twin without the day's caps and where its result comes from
(BacktestService.without_caps, decomposition), and its trade autopsy kept beside it (src/admin/autopsy.py).
"""

import asyncio
import json
import sys
import types
from datetime import date

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.admin import autopsy as autopsy_glue  # noqa: E402
from src.admin.backtests import CAPS_OFF  # noqa: E402
from src.admin.errors import NotPossible  # noqa: E402
from src.admin.processes import DONE  # noqa: E402
from tests.test_admin_backtests import FakeLauncher, Ticking, config, folder_of, pine, scripts, service  # noqa: E402, F401
from tests.test_admin_forward_checks import DAYS, RANDOM, finished  # noqa: E402


@pytest.fixture
def backtests(tmp_path, scripts, config):  # noqa: F811
    return service(tmp_path, scripts, config, FakeLauncher(ends_with=None), clock=Ticking())


def asked(backtests, body, **research) -> str:
    async def ask():
        return backtests.create(body, **research)

    run_id = asyncio.run(ask())["id"]
    backtests._waiting.clear()
    return run_id


def test_a_twin_is_the_run_again_with_every_cap_of_the_day_off(tmp_path, backtests, scripts):
    run_id = asked(backtests, pine(scripts), made_by="claude", experiment="aaaaaa")
    finished(tmp_path, run_id, DAYS, baseline=RANDOM)

    twin = backtests.without_caps(run_id, made_by="claude", made_via="agent:abc", experiment="aaaaaa")

    request = backtests.request_of(twin["id"])
    settings = json.loads((folder_of(tmp_path, twin["id"]) / "settings.json").read_text())
    assert request["decomposes"] == run_id and request["name"].startswith("Without the day's caps: ")
    assert request["experiment"] == "aaaaaa" and request.get("baseline") == backtests.request_of(run_id).get("baseline")
    assert all(settings["settings"][name] == value for name, value in CAPS_OFF.items())
    assert all(settings["changed"][name] == value for name, value in CAPS_OFF.items())
    # Its settings differ: a configuration of its own, not its run's.
    assert backtests.configuration_of(twin["id"]) != backtests.configuration_of(run_id)
    assert backtests.twin_under_way(run_id) == twin["id"] and backtests.twin_of(run_id) is None
    backtests._waiting.clear()
    with pytest.raises(NotPossible, match="not a twin itself"):
        finished(tmp_path, twin["id"], DAYS)
        backtests.without_caps(twin["id"])


def test_where_a_result_comes_from_is_worked_out_once_its_twin_is_done(tmp_path, backtests, scripts):
    run_id = asked(backtests, pine(scripts))
    finished(tmp_path, run_id, DAYS, baseline=RANDOM)
    assert backtests.decomposition(run_id) is None
    twin = backtests.without_caps(run_id)["id"]
    backtests._waiting.clear()
    finished(tmp_path, twin, DAYS, [3.0, -1.0, -2.0], baseline=RANDOM)

    found = backtests.decomposition(run_id)

    assert found["twin"] == twin and found["with_caps"]["trades"] == 20 and found["without_caps"]["trades"] == 30
    assert found["tail"]["days_drawn_from"] == 10 and set(found["tail"]["bounds"]) == {"best_first", "results"}
    # Kept beside the run, read again as it was.
    kept = json.loads((folder_of(tmp_path, run_id) / "decomposition.json").read_text())
    assert kept == found and backtests.decomposition(run_id) == found


def test_a_sweep_or_a_run_not_finished_has_no_twin(tmp_path, backtests, scripts):
    run_id = asked(backtests, pine(scripts))

    with pytest.raises(NotPossible, match="Only a finished run"):
        backtests.without_caps(run_id)


def test_an_autopsy_is_kept_beside_its_run_and_refused_on_the_lock_box(tmp_path, backtests, scripts, monkeypatch):
    run_id = asked(backtests, pine(scripts, **{"from": "2026-10-05", "to": "2026-10-16"}))
    finished(tmp_path, run_id, DAYS, baseline=RANDOM)
    read = []

    async def bars(queries, history, zone, provider, key, first, last, interval, today, limit=0):
        read.append((provider, key, first.isoformat(), interval))
        return {"bars": []}

    monkeypatch.setattr(autopsy_glue.asset_bars, "bars", bars)

    found = asyncio.run(autopsy_glue.run_autopsy(backtests, None, None, backtests.journal, run_id, "Europe/Paris", date(2026, 11, 19)))
    again = asyncio.run(autopsy_glue.run_autopsy(backtests, None, None, backtests.journal, run_id, "Europe/Paris", date(2026, 11, 19)))

    assert found["trades"] == 20 and found["too_few"] is not None and again == found
    # Each traded day's minute bars of the chart's asset, read once.
    assert len(read) == 10 and read[0] == ("dukascopy", "us100", "2026-10-05", 60)
    assert found["asset"] == {"provider": "dukascopy", "key": "us100"}
    other = asked(backtests, pine(scripts, **{"from": "2026-10-05", "to": "2026-10-16"}))
    finished(tmp_path, other, DAYS, baseline=RANDOM)
    backtests.journal.set_lockbox(date(2026, 10, 14))
    with pytest.raises(NotPossible, match="lock box"):
        asyncio.run(autopsy_glue.run_autopsy(backtests, None, None, backtests.journal, other, "Europe/Paris", date(2026, 11, 19)))
    assert backtests.state(other)["state"] == DONE
