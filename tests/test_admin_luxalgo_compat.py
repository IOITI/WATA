"""Whether LuxAlgo's indicators run on WATA (src/admin/luxalgo_compat.py), on the LuxAlgo of tests/test_admin_luxalgo.py."""

import asyncio
import hashlib

import pytest

from src.admin import luxalgo_compat
from src.admin.errors import NotPossible
from src.admin.luxalgo import LuxAlgo
from src.admin.luxalgo_compat import CUT, FAILS, NO_ALERTS, RUNS, CompatChecker, CompatStore, declared, entry_of
from tests.test_admin_luxalgo import WHOLE, FakeLuxAlgo


class FakeStudies:
    """The Pine studies, as the checks use them: one at a time, each check answered as told."""

    def __init__(self, answer=None):
        self.asked = []
        self.busy = False
        self.answer = answer or {"ok": True, "plots": ["Fast", "Slow"], "inputs": [{"id": "in_0"}],
                                 "alerts_on_made_up_bars": {"Long": 2}, "tradingview_rules": []}

    async def study(self, mode, request):
        self.asked.append((mode, request))
        return self.answer


def test_what_a_source_declares_is_read_without_its_comments_and_strings():
    source = """//@version=6
// alertcondition(fake, "Commented")
indicator("x", overlay = true)
s = "alertcondition(no, 'Inside a string')"
alertcondition(ta.crossover(a, b), "Long", "Fast crossed above")
alertcondition(ta.crossunder(a, b), title = "Short", message = 'down')
alertcondition(cond, title="Close")
if barstate.isconfirmed
    alert("Bull " + str.tostring(close), alert.freq_once_per_bar_close)
strategy.entry("L", strategy.long)
myalert(1)
"""
    assert declared(source) == {"alertconditions": ["Long", "Short", "Close"], "alert_calls": 1, "strategy": False}
    assert declared('//@version=6\nstrategy("S", overlay=true)\nplot(close)')["strategy"] is True
    assert declared("") == {"alertconditions": [], "alert_calls": 0, "strategy": False}


@pytest.mark.parametrize("checked,source,state", [
    ({"ok": True, "tradingview_rules": []}, '//@version=6\nindicator("x")\nalertcondition(c, "Up")', RUNS),
    ({"ok": True, "tradingview_rules": []}, '//@version=6\nindicator("x")\nplot(close)', NO_ALERTS),
    ({"ok": False, "error": {"message": "Unknown function"}}, '//@version=6\nindicator("x")', FAILS),
])
def test_a_check_says_whether_it_runs_and_raises_alerts(checked, source, state):
    entry = entry_of({"slug": "made-up", "name": "Made up", "source": source}, checked, 1_800_000_000)
    assert entry["state"] == state and entry["sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert entry["checked_at"] == "2027-01-15T08:00:00Z"
    if state == FAILS:
        assert entry["error"] == "Unknown function" and entry["compiles"] is False


def test_the_store_keeps_one_entry_by_indicator(tmp_path):
    store = CompatStore(str(tmp_path))
    store.record(entry_of({"slug": "made-up"}, None, 1_800_000_000, cut="sign in"))
    store.record(entry_of({"slug": "made-up", "name": "Made up", "source": WHOLE}, {"ok": True}, 1_800_000_100))
    assert list(store.all()) == ["made-up"] and store.get("made-up")["state"] == NO_ALERTS
    with pytest.raises(ValueError):
        store.record({"slug": "../account"})


def checker(tmp_path, fake, studies=None, now=None):
    clock = (lambda: now[0]) if now is not None else (lambda: 1_800_000_000.0)
    return CompatChecker(CompatStore(str(tmp_path)), LuxAlgo(server=fake.server), studies, clock=clock)


def test_a_whole_source_is_checked_once_and_a_cut_one_noted(tmp_path):
    studies = FakeStudies()
    checks = checker(tmp_path, FakeLuxAlgo(), studies)

    first = asyncio.run(checks.check("made-up"))
    again = asyncio.run(checks.check("made-up"))

    assert first["state"] == NO_ALERTS and first["plots"] == 2 and again is None
    assert [mode for mode, _ in studies.asked] == ["check"]
    assert asyncio.run(checks.check("unknown")) is None

    cut = checker(tmp_path / "cut", FakeLuxAlgo(whole=False), FakeStudies())
    entry = asyncio.run(cut.check("made-up"))
    assert entry["state"] == CUT and "sign in to LuxAlgo" in entry["why"]


def test_checks_are_asked_once_each_and_a_cut_one_again_after_a_sign_in(tmp_path):
    checks = checker(tmp_path, FakeLuxAlgo(whole=False), FakeStudies())
    said = checks.ask(["made-up", "made-up", "other-one", "../account"])
    assert said["queued"] == ["made-up", "other-one"] and said["added"] == 2
    asyncio.run(checks.check("made-up"))
    checks._queue.clear()
    checks.signed_in()
    assert checks.state()["queued"] == ["made-up"]
    # A source already as it was kept is not asked again.
    checks._queue.clear()
    checks.store.record(entry_of({"slug": "kept", "name": "Kept", "source": WHOLE}, {"ok": True}, 1_800_000_000))
    checks.noticed("kept", WHOLE)
    checks.noticed("changed", WHOLE)
    assert checks.state()["queued"] == ["changed"]


def test_without_the_studies_nothing_is_checked(tmp_path):
    checks = checker(tmp_path, FakeLuxAlgo(), None)
    assert not checks.available
    with pytest.raises(NotPossible, match="Pine studies"):
        checks.ask(["made-up"])
    checks.noticed("made-up", WHOLE)
    assert checks.state()["queued"] == []


def test_the_queue_is_checked_one_at_a_time_within_the_hours_pace(tmp_path, monkeypatch):
    monkeypatch.setattr(luxalgo_compat, "CHECKS_AN_HOUR", 1)
    monkeypatch.setattr(luxalgo_compat, "RETRY_SECONDS", 0.01)
    monkeypatch.setattr(luxalgo_compat, "IDLE_SECONDS", 0.01)
    studies = FakeStudies()
    fake = FakeLuxAlgo()
    now = [1_800_000_000.0]
    checks = checker(tmp_path, fake, studies, now)

    async def run():
        task = asyncio.create_task(checks.run())
        checks.ask(["made-up"])
        for _ in range(100):
            if checks.store.get("made-up"):
                break
            await asyncio.sleep(0.01)
        # The hour's one check is spent: the next waits, and so does any while a study runs.
        checks.store.record({"slug": "made-up", "sha256": "older"})
        checks.ask(["made-up"])
        await asyncio.sleep(0.1)
        waited = checks.state()["queued"]
        studies.busy, now[0] = True, now[0] + 3601
        await asyncio.sleep(0.1)
        busy = checks.state()["queued"]
        studies.busy = False
        for _ in range(100):
            if not checks.state()["queued"] and checks.state()["checking"] is None:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        return waited, busy

    waited, busy = asyncio.run(run())
    assert waited == ["made-up"] and busy == ["made-up"]
    assert len(studies.asked) == 2
