"""What WATA knows of LuxAlgo's indicators (src/admin/luxalgo_knowledge.py), and the routes that serve it and the checks."""

from types import SimpleNamespace

import pytest

from src.admin import luxalgo_compat
from src.admin.agent.verdicts import VerdictStore
from src.admin.luxalgo import LuxAlgo
from src.admin.luxalgo_compat import CompatChecker, CompatStore, entry_of
from src.admin.luxalgo_knowledge import known
from src.admin.script_origins import ScriptOrigins, luxalgo_origin
from src.admin.ta_rules import RuleStore
from src.admin.ta_scripts import ScriptStore
from tests.test_admin_api import PASSWORD, SAME_ORIGIN, config_path, make_client, make_ctx  # noqa: F401 (fixtures)
from tests.test_admin_luxalgo import WHOLE, FakeLuxAlgo
from tests.test_admin_luxalgo_compat import FakeStudies
from tests.test_admin_ta_rules import fields

FOUND = {"slug": "made-up", "name": "Made up", "chart_url": None, "licence": "CC BY-NC-SA 4.0", "owner": "LuxAlgo"}
SOURCE = WHOLE.replace("\r\n", "\n").replace('plot(close)', 'plot(close)\nalertcondition(close > open, "Up")')


class FakeRuns:
    """The backtests, as what WATA knows reads them: the list of runs and the rules' days on paper."""

    def __init__(self, runs, paper=None):
        self.runs = runs
        self.paper_rules = paper or {}

    def list_runs(self):
        return self.runs

    def paper(self, *, with_reports=True):
        return {"rules": self.paper_rules, "live": None}


@pytest.fixture
def world(tmp_path):
    scripts = ScriptStore(str(tmp_path / "ta"))
    origins = ScriptOrigins(str(tmp_path / "ta"))
    taken = scripts.create("Made up", SOURCE)
    mine = scripts.create("Made up, Claude's", SOURCE, made_by="claude")
    gone = scripts.create("Made up, gone", SOURCE)
    for script in (taken, mine, gone):
        origins.record(script["id"], luxalgo_origin(FOUND, "operator", 1_800_000_000))
    scripts.delete(gone["id"])
    rules = RuleStore(scripts)
    rule = rules.create(fields(taken, name="Made up on US100"), rules.load()["revision"])["rule"]
    run = {"id": "20261030-101010-abcdef", "name": "Made up, a week", "source": {"script": {"id": taken["id"]}},
           "status": {"state": "done"}, "headline": {"total_percent": -1.5}, "made_by": None}
    other = {"id": "20261030-111111-abcdef", "name": "Someone else's", "source": {"script": {"id": "0123456789ab"}},
             "status": {"state": "done"}, "headline": None, "made_by": None}
    verdicts = VerdictStore(str(tmp_path / "agent"))
    verdicts.save("luxalgo:made-up", "negative", "No edge on 14 days.", conversation="c", request=0, at="2026-10-31T09:00:00Z")
    verdicts.save(f"run:{run['id']}", "negative", "Loses after costs.", conversation="c", request=0, at="2026-10-31T09:00:00Z")
    compat = CompatStore(str(tmp_path / "ta"))
    compat.record(entry_of({"slug": "made-up", "name": "Made up", "source": SOURCE}, {"ok": True}, 1_800_000_000))
    compat.record(entry_of({"slug": "never-taken"}, None, 1_800_000_000, cut="sign in"))
    paper = {rule["id"]: {"days": [{"day": "2026-10-30", "day_percent": -0.4, "trades": 3, "outside": True},
                                   {"day": "2026-10-29", "day_percent": 0.2, "trades": 2, "outside": False}]}}
    ctx = SimpleNamespace(script_origins=origins, ta_scripts=scripts, luxalgo_compat=compat,
                          settings=SimpleNamespace(agent_path=str(tmp_path / "agent"), backtests_enabled=True),
                          backtests=FakeRuns([run, other], paper))
    return ctx, taken, mine, rule, run


def test_what_wata_knows_of_an_indicator_is_read_from_its_own_files(world):
    ctx, taken, mine, rule, run = world
    entry = known(ctx, ["made-up"])["made-up"]
    assert {script["id"] for script in entry["scripts"]} == {taken["id"], mine["id"]}
    assert entry["verdict"]["tone"] == "negative" and entry["compat"]["state"] == "runs"
    assert [found["id"] for found in entry["runs"]] == [run["id"]]
    assert entry["runs"][0]["verdict"]["line"] == "Loses after costs." and entry["runs"][0]["headline"]["total_percent"] == -1.5
    [running] = entry["rules"]
    assert (running["id"], running["mode"]) == (rule["id"], "shadow")
    assert running["paper"]["days"] == 2 and running["paper"]["outside"] == 1


def test_the_cards_read_every_indicator_touched_in_short(world):
    ctx, taken, mine, rule, _ = world
    every = known(ctx, None, whole=False)
    assert set(every) == {"made-up", "never-taken"}
    assert every["made-up"]["runs"] == 1 and every["made-up"]["rules"] == [{"id": rule["id"], "mode": "shadow"}]
    assert every["never-taken"]["compat"]["state"] == "cut" and every["never-taken"]["scripts"] == []
    nothing = known(ctx, ["unheard-of"])["unheard-of"]
    assert nothing["scripts"] == [] and nothing["runs"] == [] and nothing["verdict"] is None


@pytest.fixture
def client(config_path, tmp_path, monkeypatch):
    # The admin's lifespan runs the checker: held still here, the queue stays as asked.
    monkeypatch.setattr(luxalgo_compat, "CHECKS_AN_HOUR", 0)
    ctx = make_ctx(config_path, tmp_path)
    ctx.ta_scripts = ScriptStore(str(tmp_path / "ta"))
    ctx.script_origins = ScriptOrigins(str(tmp_path / "ta"))
    ctx.settings.agent_path = str(tmp_path / "agent")
    ctx.luxalgo = LuxAlgo(server=FakeLuxAlgo().server)
    ctx.luxalgo_compat = CompatStore(str(tmp_path / "ta"))
    studies = FakeStudies()
    ctx.luxalgo_checks = CompatChecker(ctx.luxalgo_compat, ctx.luxalgo, studies)
    with make_client(ctx, tmp_path / "no-interface") as client:
        assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
        yield client, ctx, studies


def test_an_indicator_says_what_wata_knows_and_the_library_every_one_touched(client):
    client, ctx, _ = client
    made = client.post("/api/luxalgo/indicators/made-up/import", json={}, headers=SAME_ORIGIN).json()
    assert client.get("/api/luxalgo/indicators/made-up").json()["known"]["scripts"][0]["id"] == made["id"]
    assert [entry["slug"] for entry in client.get("/api/luxalgo/known").json()["items"]] == ["made-up"]


def test_a_source_read_whole_is_queued_for_its_check_and_checks_are_asked(client):
    client, ctx, _ = client
    client.get("/api/luxalgo/indicators/made-up/source")
    assert ctx.luxalgo_checks.state()["queued"] == ["made-up"]
    asked = client.post("/api/luxalgo/compat", json={"slugs": ["other-one", "made-up"]}, headers=SAME_ORIGIN)
    assert asked.status_code == 200 and asked.json()["queued"] == ["made-up", "other-one"]
    assert client.post("/api/luxalgo/compat", json={"slugs": ["../x"]}, headers=SAME_ORIGIN).status_code == 422
    listed = client.get("/api/luxalgo/compat").json()
    assert listed["items"] == [] and listed["checks"]["queued"] == ["made-up", "other-one"]


def test_without_the_studies_checks_are_refused_in_words(client):
    client, ctx, _ = client
    ctx.luxalgo_checks = CompatChecker(ctx.luxalgo_compat, ctx.luxalgo, None)
    refused = client.post("/api/luxalgo/compat", json={"slugs": ["made-up"]}, headers=SAME_ORIGIN)
    assert refused.status_code == 409 and "Pine studies" in refused.json()["detail"]
    assert client.get("/api/luxalgo/compat").json()["checks"]["available"] is False
