"""
What the AI agent shows on the pages: a chart under its answer (show_chart, src/admin/agent/charts.py), drawn
from figures its tools gave and checked before it is kept, four a request at most; and its verdict on a run, a
signal rule, a trading day or a position (set_verdict, src/admin/agent/verdicts.py), kept only on a thing that
exists, the latest replacing the one before, gone with its conversation, read by the pages through their route.
"""

import asyncio
import json

import pytest

from src.admin.agent import transcript
from src.admin.agent.charts import MAX_CHARTS, ChartRefused, checked_chart
from src.admin.agent.proposals import Proposals
from src.admin.agent.tools import AgentTools
from src.admin.agent.verdicts import VerdictStore
from tests.test_admin_api import SAME_ORIGIN, config_path, make_ctx  # noqa: F401 (fixtures)
from tests.test_admin_backtests import NOW, config, pine, scripts  # noqa: F401 (fixtures)
from tests.test_agent_api import signed_in, with_agent
from tests.test_agent_loop import FakeProvider
from tests.test_agent_tools import CONVERSATION, calls, store  # noqa: F401 (fixtures)
from tests.test_mcp_tools_read import journal, research  # noqa: F401 (fixtures)

LINE = {"title": "The run against random entries", "kind": "line", "unit": "%", "source": "backtest_days of r1",
        "series": [{"key": "run", "label": "The run"}, {"key": "random", "label": "Random entries, median"}],
        "points": [{"t": "2026-10-14", "run": 1.5, "random": 0.2}, {"t": "2026-10-13", "run": -0.5, "random": None}]}
BARS = {"title": "Each day", "kind": "bar", "unit": "%", "source": "backtest_days of r1", "tone": "sign",
        "series": [{"key": "day", "label": "Day"}], "categories": [{"label": "13 Oct", "day": -0.5}, {"label": "14 Oct", "day": 1.5}]}


def test_a_line_is_kept_in_time_order_with_its_gaps_and_bars_by_category():
    line = checked_chart(LINE)
    bars = checked_chart(BARS)

    assert [point["t"] for point in line["points"]] == ["2026-10-13", "2026-10-14"]
    assert line["points"][0] == {"t": "2026-10-13", "run": -0.5, "random": None} and line["zero"] is False
    assert bars["categories"][1] == {"label": "14 Oct", "day": 1.5} and bars["tone"] == "sign"


@pytest.mark.parametrize("change, said", [
    ({"points": [{"t": "2026-10-13", "run": 1, "other": 2}, {"t": "2026-10-14", "run": 2}]}, "keys no series has"),
    ({"points": [{"t": "yesterday", "run": 1}, {"t": "2026-10-14", "run": 2}]}, "not a moment"),
    ({"points": [{"t": "2026-10-13", "run": float("nan")}, {"t": "2026-10-14", "run": 2}]}, "not a number"),
    ({"points": [{"t": "2026-10-13", "run": 1}]}, "two at least"),
    ({"series": [{"key": "run", "label": "a"}, {"key": "run", "label": "b"}]}, "key of its own"),
    ({"points": [{"t": f"2026-10-13T10:{minute % 60:02d}:00", "run": 1.123456789, "random": 2.123456789}
                 for minute in range(2000)]}, "over 64 kB"),
])
def test_a_chart_that_cannot_be_drawn_is_refused_saying_why(change, said):
    with pytest.raises(ChartRefused, match=said):
        checked_chart({**LINE, **change})
    with pytest.raises(ChartRefused, match="one series"):
        checked_chart({**BARS, "series": [{"key": "day", "label": "Day"}, {"key": "b", "label": "B"}]})
    with pytest.raises(ChartRefused, match="across a line chart"):
        checked_chart({**BARS, "references": [{"value": 1}]})


def tools_with_verdicts(research, store, tmp_path):
    tools = AgentTools(research, Proposals(store, research.scripts), verdicts=VerdictStore(str(tmp_path / "agent")),
                       clock=lambda: 1_793_372_400.0)
    asyncio.run(tools.start())
    return tools


def test_charts_are_drawn_four_a_request_at_most(research, store, tmp_path):
    tools = tools_with_verdicts(research, store, tmp_path)

    answers = calls(tools, *([("show_chart", LINE)] * (MAX_CHARTS + 1)))

    assert all(answer.chart is not None for answer in answers[:MAX_CHARTS])
    assert answers[-1].is_error and "charts already" in answers[-1].text


def test_a_verdict_is_kept_only_on_a_thing_that_exists(research, store, tmp_path, scripts):
    tools = tools_with_verdicts(research, store, tmp_path)

    on_position, on_day, no_position, no_run, bad_day = calls(
        tools,
        ("set_verdict", {"about": "position:5001", "tone": "negative", "line": "Sold on a quote older than its purchase."}),
        ("set_verdict", {"about": "day:2026-10-28", "tone": "neutral", "line": "A quiet day."}),
        ("set_verdict", {"about": "position:9", "tone": "positive", "line": "Fine."}),
        ("set_verdict", {"about": "run:20261001-101010-abcdef", "tone": "positive", "line": "Holds."}),
        ("set_verdict", {"about": "day:2026-13-45", "tone": "neutral", "line": "No."}),
    )

    assert on_position.verdict == {"about": "position:5001", "tone": "negative",
                                   "line": "Sold on a quote older than its purchase."}
    assert not on_day.is_error
    assert no_position.is_error and "no position 9" in no_position.text
    assert no_run.is_error and bad_day.is_error
    kept = VerdictStore(str(tmp_path / "agent")).of(["position:5001", "day:2026-10-28", "position:9"])
    assert set(kept) == {"position:5001", "day:2026-10-28"}
    assert kept["position:5001"]["conversation"] == CONVERSATION and kept["position:5001"]["at"] == "2026-10-30T15:00:00Z"


def test_the_latest_verdict_replaces_the_one_before_and_goes_with_its_conversation(tmp_path):
    verdicts = VerdictStore(str(tmp_path))
    verdicts.save("run:r1", "mixed", "In part.", conversation="c1", request=0, at="2026-10-30T10:00:00Z")
    verdicts.save("run:r1", "positive", "It   holds\non 21 days.", conversation="c2", request=1, at="2026-10-30T11:00:00Z")
    verdicts.save("rule:a1b2c3d4e5f6", "mixed", "Not yet.", conversation="c1", request=0, at="2026-10-30T10:00:00Z")

    assert verdicts.of(["run:r1"])["run:r1"]["line"] == "It holds on 21 days."
    assert verdicts.forget({"c1"}) == 1
    assert set(verdicts.of(["run:r1", "rule:a1b2c3d4e5f6"])) == {"run:r1"}


def test_a_request_s_charts_and_verdicts_are_projected_for_its_page():
    activity = [
        {"at": "2026-10-30T10:00:00Z", "event": "request", "request": 0, "prompt": "Is it believable?"},
        {"at": "2026-10-30T10:00:05Z", "event": "chart", "request": 0, "id": "t1", "chart": {"kind": "bar", "title": "x"}},
        {"at": "2026-10-30T10:00:06Z", "event": "verdict", "request": 0, "about": "run:r1", "tone": "positive",
         "line": "It holds."},
    ]

    request = transcript.project({"id": "c", "status": "idle"}, activity, {})["requests"][0]

    assert request["charts"] == [{"kind": "bar", "title": "x", "id": "t1"}]
    assert request["verdicts"] == [{"about": "run:r1", "tone": "positive", "line": "It holds."}]


def test_the_pages_read_the_verdicts_of_what_they_show(config_path, tmp_path):
    ctx = with_agent(make_ctx(config_path, tmp_path), tmp_path, FakeProvider())
    ctx.agent.verdicts = VerdictStore(str(tmp_path / "agent"))
    ctx.agent.verdicts.save("run:r1", "positive", "It holds.", conversation="c1", request=0, at="2026-10-30T10:00:00Z")
    client = signed_in(ctx, tmp_path)
    try:
        found = client.get("/api/agent/verdicts", params=[("about", "run:r1"), ("about", "rule:x")]).json()
    finally:
        client.__exit__(None, None, None)

    assert found == {"items": {"run:r1": {"about": "run:r1", "tone": "positive", "line": "It holds.",
                                          "at": "2026-10-30T10:00:00Z", "conversation": "c1", "request": 0}}}
    json.dumps(found)
