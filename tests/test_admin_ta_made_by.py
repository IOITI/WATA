"""Scripts and rules made by an agent say so; a source not saved yet finds its libraries."""

from src.admin.ta_scripts import pine_facts
from tests.test_admin_ta_rules import (  # noqa: F401 (fixtures)
    STRATEGY,
    cross,
    fields,
    rules,
    scripts,
)


def test_a_script_made_by_an_agent_says_so_and_the_operators_do_not(scripts, cross):
    made = scripts.create("Claude EMA", STRATEGY.replace("Cross", "EMA"), made_by="claude")

    assert made["made_by"] == "claude"
    assert made["versions"][0]["made_by"] == "claude"
    assert cross["made_by"] is None
    assert "made_by" not in cross["versions"][0]
    assert scripts.count_made_by("claude") == 1


def test_each_version_says_who_saved_it(scripts, cross):
    saved = scripts.save(cross["id"], cross["version"], source=STRATEGY + "\n// tuned", made_by="claude")

    assert [entry.get("made_by") for entry in saved["versions"]] == [None, "claude"]
    # The script is still the operator's: an agent saving a version of it does not make it its own.
    assert saved["made_by"] is None


def test_a_rule_made_by_an_agent_says_so_and_keeps_it_when_changed(rules, cross):
    made = rules.create(fields(cross), rules.load()["revision"], made_by="claude")["rule"]
    updated = rules.update(made["id"], fields(cross, name="Renamed"), rules.load()["revision"])["rule"]

    assert made["made_by"] == "claude"
    assert updated["made_by"] == "claude"
    assert "made_by" not in rules.create(fields(cross, name="Mine"), rules.load()["revision"])["rule"]


def test_a_source_not_saved_yet_finds_the_libraries_it_imports(rules):
    libraries = rules.libraries_of(pine_facts(STRATEGY), "Draft")

    assert [library["name"] for library in libraries] == ["Maths", "Bands"]
    assert all(len(library["sha256"]) == 64 for library in libraries)
