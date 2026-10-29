"""
What a release changes, container by container, and what may go now
(src/release/plan.py, pure).
"""

import pytest

from src.release.guards import Situation
from src.release.plan import (
    ABSENT,
    CHANGE,
    INFRA,
    INFRASTRUCTURE,
    NOW,
    OPTIONAL,
    PENDING,
    SKIP,
    UNCHANGED,
    BACKUP,
    Container,
    make_plan,
    said,
    summary,
)


def fingerprint(files: dict, libraries: dict | None = None, platform: str = "p1") -> dict:
    libraries = libraries or {"fastapi": "1.0"}
    return {"code": str(sorted(files.items())), "files": files, "libraries": str(sorted(libraries.items())),
            "distributions": libraries, "platform": platform}


def container(name, role, before, after, **others) -> Container:
    return Container(name=name, role=role, current=before, target=after, version="0.59.1", **others)


SHARED = {"src/trade/rules.py": "a", "src/configuration/__init__.py": "c"}
ADMIN = {"src/admin/app.py": "x", "src/admin/ta_rules.py": "r", **SHARED}
ENGINE = {"src/ta_engine/engine.py": "e", "src/admin/ta_rules.py": "r", **SHARED}
TRADER = {"src/trader/__init__.py": "t", **SHARED}


def steps_by_name(steps):
    return {step.container: step for step in steps}


def test_an_admin_only_release_moves_the_admin_alone_market_open_or_not():
    containers = [
        container("admin1", "admin", fingerprint(ADMIN), fingerprint({**ADMIN, "src/admin/app.py": "y"})),
        container("trader1", "trader", fingerprint(TRADER), fingerprint(TRADER)),
        container("ta_engine1", "ta_engine", fingerprint(ENGINE), fingerprint(ENGINE)),
    ]
    steps = steps_by_name(make_plan(containers, Situation(market_open=True, positions_open=1), target="0.61.0"))
    assert steps["admin1"].kind == CHANGE and steps["admin1"].when == NOW
    assert steps["admin1"].reasons == {"code": ["src/admin/app.py"]}
    assert steps["trader1"].kind == UNCHANGED and steps["trader1"].when == SKIP
    assert steps["ta_engine1"].kind == UNCHANGED


def test_a_shared_file_makes_one_group_held_while_one_member_is():
    after_admin = fingerprint({**ADMIN, "src/admin/ta_rules.py": "r2"})
    after_engine = fingerprint({**ENGINE, "src/admin/ta_rules.py": "r2"})
    containers = [
        container("admin1", "admin", fingerprint(ADMIN), after_admin),
        container("ta_engine1", "ta_engine", fingerprint(ENGINE), after_engine),
    ]
    situation = Situation(market_open=True, live_rules=1)
    steps = steps_by_name(make_plan(containers, situation, target="0.61.0"))
    assert steps["admin1"].group == steps["ta_engine1"].group
    assert steps["ta_engine1"].when == PENDING and "live rule" in steps["ta_engine1"].held
    assert steps["admin1"].when == PENDING and steps["admin1"].held is None
    assert steps["admin1"].held_with == ["ta_engine1"]
    # The market closed: both go.
    steps = steps_by_name(make_plan(containers, Situation(market_open=False, live_rules=1), target="0.61.0"))
    assert steps["admin1"].when == steps["ta_engine1"].when == NOW


def test_a_group_split_on_purpose_is_said():
    after_admin = fingerprint({**ADMIN, "src/admin/ta_rules.py": "r2"})
    after_engine = fingerprint({**ENGINE, "src/admin/ta_rules.py": "r2"})
    containers = [
        container("admin1", "admin", fingerprint(ADMIN), after_admin),
        container("ta_engine1", "ta_engine", fingerprint(ENGINE), after_engine),
    ]
    steps = steps_by_name(make_plan(containers, Situation(market_open=True, live_rules=1), target="0.61.0",
                                    wanted=["admin1"]))
    assert steps["admin1"].when == NOW and steps["admin1"].ahead_of == ["ta_engine1"]
    assert steps["ta_engine1"].when == SKIP


def test_libraries_are_compared_by_container():
    containers = [
        container("admin1", "admin", fingerprint(ADMIN, {"anthropic": "2.3.0"}),
                  fingerprint(ADMIN, {"anthropic": "2.4.0"})),
        container("trader1", "trader", fingerprint(TRADER), fingerprint(TRADER)),
    ]
    steps = steps_by_name(make_plan(containers, Situation(), target="0.61.0"))
    assert steps["admin1"].reasons == {"libraries": [{"name": "anthropic", "from": "2.3.0", "to": "2.4.0"}]}
    assert steps["trader1"].kind == UNCHANGED


def test_python_or_the_system_alone_is_optional_and_applied_only_when_asked():
    containers = [container("trader1", "trader", fingerprint(TRADER, platform="p1"), fingerprint(TRADER, platform="p2"))]
    steps = steps_by_name(make_plan(containers, Situation(), target="0.61.0"))
    assert steps["trader1"].kind == OPTIONAL and steps["trader1"].when == SKIP
    steps = steps_by_name(make_plan(containers, Situation(), target="0.61.0", platform=True))
    assert steps["trader1"].when == NOW
    steps = steps_by_name(make_plan(containers, Situation(market_open=True), target="0.61.0", wanted=["trader1"]))
    assert steps["trader1"].when == PENDING


def test_what_a_container_runs_unknown_is_a_change_not_left_behind_unseen():
    steps = steps_by_name(make_plan([container("trader1", "trader", None, fingerprint(TRADER))], Situation()))
    assert steps["trader1"].kind == CHANGE and "unknown" in steps["trader1"].reasons


def test_compose_and_the_images_settings_are_changes_and_the_files_gone_too():
    same = fingerprint(TRADER)
    containers = [
        container("trader1", "trader", same, same, compose=["environment"]),
        container("scheduler1", "scheduler", same, same, image_settings=True),
        container("telegram1", "telegram", same, same, compose=None),
    ]
    steps = steps_by_name(make_plan(containers, Situation(), target="0.61.0"))
    assert steps["trader1"].reasons == {"compose": ["environment"]}
    assert "image" in steps["scheduler1"].reasons
    assert steps["telegram1"].kind == CHANGE


def test_the_infrastructure_is_never_applied_and_a_missing_container_is_said():
    containers = [
        Container(name="postgres1", role="postgres1", kind=INFRA, compose=["command"]),
        Container(name="traefik", role="traefik", kind=INFRA, compose=[]),
        Container(name="ta_engine1", role="ta_engine", running=False),
    ]
    steps = steps_by_name(make_plan(containers, Situation(), target="0.61.0", force=True))
    assert steps["postgres1"].kind == INFRASTRUCTURE and steps["postgres1"].when == SKIP
    assert steps["traefik"].kind == UNCHANGED
    assert steps["ta_engine1"].kind == ABSENT
    assert "full restart" in said(steps["postgres1"])


def test_the_order_is_the_restarts_with_the_backups_first_and_the_control_service_before_the_admin():
    names = ["admin1", "trader1", "service_control1", "pg_backup1", "web_server1", "position_monitor1", "telegram1"]
    roles = {"admin1": "admin", "trader1": "trader", "service_control1": "service_control", "pg_backup1": "pg_backup",
             "web_server1": "web_server", "position_monitor1": "position_monitor", "telegram1": "telegram"}
    containers = [container(name, roles[name], fingerprint({name: "1"}), fingerprint({name: "2"}),
                            **({"kind": BACKUP} if name == "pg_backup1" else {})) for name in names]
    steps = make_plan(containers, Situation(), target="0.61.0")
    assert [step.container for step in steps] == [
        "pg_backup1", "web_server1", "telegram1", "position_monitor1", "trader1", "service_control1", "admin1"]


def test_force_goes_whatever_holds_and_a_host_hold_counts():
    containers = [
        container("trader1", "trader", fingerprint(TRADER), fingerprint({**TRADER, "src/trader/__init__.py": "u"})),
        container("pg_backup1", "pg_backup", fingerprint({"deploy/pg_backup.sh": "1"}),
                  fingerprint({"deploy/pg_backup.sh": "2"}), kind=BACKUP),
    ]
    situation = Situation(positions_open=2, market_open=True)
    holds = {"pg_backup1": "a dump is being written"}
    steps = steps_by_name(make_plan(containers, situation, holds=holds, target="0.61.0"))
    assert steps["trader1"].when == PENDING and steps["trader1"].held == "2 positions open; the market is open by WATA's hours"
    assert steps["pg_backup1"].when == PENDING and steps["pg_backup1"].held == "a dump is being written"
    steps = steps_by_name(make_plan(containers, situation, holds=holds, target="0.61.0", force=True))
    assert steps["trader1"].when == steps["pg_backup1"].when == NOW


def test_an_unknown_container_asked_for_is_refused():
    with pytest.raises(ValueError):
        make_plan([], Situation(), wanted=["nobody1"])


def test_the_summary_and_the_lines_say_it_in_short():
    containers = [
        container("admin1", "admin", fingerprint(ADMIN), fingerprint({**ADMIN, "src/admin/app.py": "y"})),
        container("trader1", "trader", fingerprint(TRADER), fingerprint({**TRADER, "src/trader/__init__.py": "u"})),
        container("scheduler1", "scheduler", fingerprint(TRADER), fingerprint(TRADER)),
    ]
    steps = make_plan(containers, Situation(market_open=True), target="0.61.0")
    assert summary(steps) == {"now": ["admin1"], "pending": ["trader1"], "optional": [], "infrastructure": [],
                              "unchanged": ["scheduler1"]}
    lines = {step.container: said(step) for step in steps}
    assert lines["admin1"] == "admin1: code (1 file: src/admin/app.py) → now"
    assert lines["trader1"].endswith("→ pending (the market is open by WATA's hours)")
    assert lines["scheduler1"] == "scheduler1: unchanged"
