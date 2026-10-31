"""
What the AI agent reads of the admin itself (src/admin/agent_ops.py, src/admin/agent/tools.py OpsReads): trading
now as the dashboard reads it, what changed (config.json's saves, the operator's actions, the last deploy), the TA
engine's last minutes and what waits to be deployed. Nothing that must not reach Anthropic is in them: an operator's
address, a credential's last characters, a sign-in; each read is offered only when the admin has what it reads, and
one that fails is said to the agent as a refusal.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.admin.agent.proposals import Proposals
from src.admin.agent.tools import OPS_TOOLS, AgentTools, OpsReads
from src.admin.agent_ops import CHANGES_MAX_LINES, changes_between, ops_reads, release_now, trading_now
from tests.test_admin_backtests import NOW, config, pine, scripts  # noqa: F401 (fixtures)
from tests.test_agent_tools import calls, store  # noqa: F401 (fixtures)
from tests.test_mcp_tools_read import journal, research  # noqa: F401 (fixtures)

UTC = timezone.utc
AT = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)


def overview():
    return {
        "date": "2026-10-30", "timezone": "Europe/Paris", "trading_mode": "day_trading",
        "day": {"trade_count": 3, "closed_count": 3, "performance_percent": -1.2, "profit_loss": -4.1,
                "best_percent": 0.8, "worst_percent": -2.0, "by_action": [{"action": "long"}]},
        "limits": {"profit_target_percent": 2.0, "max_trades_per_day": 6, "cooldown_after_loss_minutes": 15,
                   "trading_start_hour": 9, "trading_end_hour": 16, "risky_trading_start_hour": 15,
                   "risky_trading_start_minute": 45, "close_position_time": "15:50", "market_timezone": "America/New_York"},
        "market_closed_today": False, "market_day": None,
        "cooldown": {"last_loss_at": AT - timedelta(minutes=5), "ends_at": AT + timedelta(minutes=10), "active": True},
        "news": {"window": None, "known_until": "2026-12-31", "readable": True},
        "entries_paused_since": None, "entries_paused_by_account_guard": None, "breakers": None,
        "saxo_login": {"renewal_failing_since": None, "error": None, "lapses_at": None},
        "open_positions": [{"position_id": "5001", "action": "long", "instrument_name": "Turbo Long",
                            "instrument_uic": 123, "order_id": "777", "live_performance_percent": 0.4,
                            "sell_refusals": None}],
        "recent_days": [{"day": "2026-10-29", "performance_percent": 1.0}] * 30,
    }


def test_trading_now_is_the_overview_without_its_month_nor_the_orders_numbers():
    said = trading_now(overview(), AT)

    assert said["market"]["hours"]["market_timezone"] == "America/New_York"
    assert said["limits"] == {"profit_target_percent": 2.0, "max_trades_per_day": 6, "cooldown_after_loss_minutes": 15}
    assert said["today"]["trade_count"] == 3 and "by_action" not in said["today"]
    assert said["open_positions"] == [{"position_id": "5001", "action": "long", "instrument_name": "Turbo Long",
                                       "live_performance_percent": 0.4, "sell_refusals": None}]
    assert "recent_days" not in said and said["now"] == "2026-10-30T15:00:00+00:00"
    assert said["market"]["weekday"] == "Friday"
    json.dumps(said, default=str)


def test_trading_now_names_the_market_s_weekday_which_closed_today_does_not_count():
    # Saturday 31 Oct 2026, 03:00 UTC: still Friday in New York; in Paris, the trading timezone, already Saturday.
    early = datetime(2026, 10, 31, 3, 0, tzinfo=UTC)
    assert trading_now(overview(), early)["market"]["weekday"] == "Friday"
    in_paris = overview()
    in_paris["limits"]["market_timezone"] = None
    said = trading_now(in_paris, early)
    assert said["market"]["weekday"] == "Saturday" and said["market"]["closed_today"] is False


def history():
    return [
        {"at": "2026-10-30T14:30:00.123456+00:00", "action": "saved from an AI agent's proposal", "by": "10.0.0.7",
         "revision_before": "a", "revision_after": "b",
         "changes": [{"path": "trade.rules[risk_management].rule_config.max_trades_per_day", "before": 8, "after": 6,
                      "added": False, "removed": False},
                     {"path": "telegram.bot_token", "before": "***", "after": "***", "added": False, "removed": False}]},
        {"at": "2026-10-28T09:00:00+00:00", "action": "saved", "by": "10.0.0.7", "changes": []},
    ]


def audit():
    return [
        {"at": "2026-10-30T14:50:00+00:00", "ip": "10.0.0.7", "action": "new entries paused", "detail": ""},
        {"at": "2026-10-30T14:40:00+00:00", "ip": "10.0.0.7", "action": "credential set",
         "detail": "Telegram bot, ending …x9Zk; read by telegram"},
        {"at": "2026-10-30T14:30:00+00:00", "ip": "10.0.0.7", "action": "configuration saved", "detail": "1 key"},
        {"at": "2026-10-30T14:20:00+00:00", "ip": "10.0.0.7", "action": "login", "detail": ""},
    ]


def deploy():
    return {"id": "d1", "kind": "apply", "by": "10.0.0.7", "force": False, "version": "0.64.0",
            "started_at": "2026-10-30T14:00:00+00:00", "finished_at": "2026-10-30T14:05:00+00:00", "done": True,
            "steps": [{"container": "admin1", "state": "ready", "detail": None, "started_at": "x"}]}


def test_what_changed_is_said_newest_first_without_an_address_nor_a_credential_s_ending():
    said = changes_between(history(), audit(), deploy(), AT - timedelta(hours=2), AT)

    assert [(line["what"], line.get("action") or line.get("how")) for line in said["changes"]] == [
        ("operator action", "new entries paused"),
        ("operator action", "credential set"),
        ("config.json", "saved from an AI agent's proposal"),
        ("deploy", None),
    ]
    text = json.dumps(said)
    assert "10.0.0.7" not in text and "x9Zk" not in text and "ending" not in text
    assert said["changes"][1]["detail"] == "Telegram bot; read by telegram"
    assert said["changes"][2]["changes"][0] == {"path": "trade.rules[risk_management].rule_config.max_trades_per_day",
                                                "before": 8, "after": 6, "added": False, "removed": False}
    assert said["changes"][3]["steps"] == [{"container": "admin1", "state": "ready", "detail": None}]
    # Outside the window: the save of the 28th; a sign-in and the audit's own line of a save are not changes.
    assert said["lines"] == 4 and "cut" not in said


def test_a_long_window_keeps_its_newest_lines_and_says_so():
    many = [{"at": (AT - timedelta(minutes=minute)).isoformat(), "action": "new entries resumed"} for minute in range(300)]

    said = changes_between([], many, None, AT - timedelta(hours=6), AT)

    assert (said["lines"], len(said["changes"])) == (300, CHANGES_MAX_LINES) and "fewer hours" in said["cut"]
    assert said["changes"][0]["at"] == AT.isoformat()


def test_what_waits_to_be_deployed_names_what_holds_each_container_and_not_who_asked():
    status = {"newest": "0.65.0", "at": "2026-10-30T15:00:00+00:00", "last": deploy(), "requests": [{"by": "10.0.0.7"}],
              "containers": []}
    waiting = {"trader1": {"container": "trader1", "version": "0.64.0", "kind": "change", "when": "now",
                           "said": "its code changes"}}

    said = release_now(status, {"trader1": "positions are open"}, waiting, None)

    assert said["waiting"] == [{"container": "trader1", "version": "0.64.0", "kind": "change", "when": "now",
                                "said": "its code changes", "held": "positions are open"}]
    assert said["last"]["version"] == "0.64.0" and "10.0.0.7" not in json.dumps(said)
    assert release_now({}, {}, {}, None) == {"available": False, "said": "watadeploy has said nothing on this server yet"}


def test_the_reads_offered_follow_what_the_admin_has(tmp_path):
    settings = SimpleNamespace(log_path=str(tmp_path), ai_agent=SimpleNamespace(read_logs=False))
    bare = ops_reads(SimpleNamespace(settings=settings, ta_scripts=None, release=None))
    full = ops_reads(SimpleNamespace(settings=SimpleNamespace(log_path=str(tmp_path),
                                                              ai_agent=SimpleNamespace(read_logs=True)),
                                     ta_scripts=object(), release=object()))

    assert bare.offered() == ["ops_health", "ops_trading_now", "ops_changes"]
    assert full.offered() == list(OPS_TOOLS)


def test_what_changed_is_read_from_the_journal_the_audit_and_watadeploy_s_status(tmp_path):
    ctx = SimpleNamespace(
        settings=SimpleNamespace(log_path=str(tmp_path), ai_agent=SimpleNamespace(read_logs=True)), ta_scripts=None,
        config_editor=SimpleNamespace(history=lambda limit: history()), audit_log=SimpleNamespace(read=lambda limit: audit()),
        release=SimpleNamespace(status=lambda: {"last": deploy()}),
    )

    said = asyncio.run(ops_reads(ctx).changes(AT - timedelta(hours=2), AT))

    assert said["lines"] == 4


# ── as the agent calls them ──


def reads(asked: list, fail: str | None = None) -> OpsReads:
    async def health():
        return {"status": "ok", "services": []}

    async def read(name, *arguments):
        asked.append((name, *arguments))
        if name == fail:
            raise ConnectionError("The database is unavailable")
        return {"read": name}

    return OpsReads(health=health, trading=lambda: read("trading"), engine=lambda: read("engine"),
                    release=lambda: read("release"), changes=lambda since, until: read("changes", since, until))


def test_the_agent_reads_trading_now_the_engine_and_what_waits_and_a_failure_is_a_refusal(research, store):
    asked = []
    tools = AgentTools(research, Proposals(store, research.scripts), ops=reads(asked, fail="engine"),
                       clock=lambda: AT.timestamp())
    asyncio.run(tools.start())

    names = [definition["name"] for definition in tools.definitions()]
    assert names[-5:] == ["ops_health", "ops_trading_now", "ops_changes", "ops_engine", "ops_release"]
    assert all(tools.is_read(name) for name in names[-5:])
    trading, engine, release = calls(tools, ("ops_trading_now", {}), ("ops_engine", {}), ("ops_release", {}))

    assert json.loads(trading.text) == {"read": "trading"} and json.loads(release.text) == {"read": "release"}
    assert engine.is_error and "could not be read now" in engine.text


def test_what_changed_is_looked_at_over_the_hours_before_its_end(research, store):
    asked = []
    tools = AgentTools(research, Proposals(store, research.scripts), ops=reads(asked), clock=lambda: AT.timestamp())
    asyncio.run(tools.start())

    _, _, too_long = calls(tools, ("ops_changes", {}), ("ops_changes", {"until": "2026-10-29 10:30", "hours": 2}),
                           ("ops_changes", {"hours": 500}))

    assert asked == [("changes", AT - timedelta(hours=24), AT),
                     ("changes", datetime(2026, 10, 29, 8, 30, tzinfo=UTC), datetime(2026, 10, 29, 10, 30, tzinfo=UTC))]
    assert too_long.is_error
