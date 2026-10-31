"""Where a Pine script came from (src/admin/script_origins.py): kept beside the scripts, or read from its header."""

import json

import pytest

from src.admin.script_origins import FILE, ScriptOrigins, derived_from_header, luxalgo_origin

FOUND = {"slug": "tri-star", "name": "Tri-star", "source": "…", "chart_url": "https://vela.luxalgo.com/chart?x=1",
         "licence": "CC BY-NC-SA 4.0", "owner": "LuxAlgo"}
HEADER = ("// This work is licensed under a Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0) "
          "https://creativecommons.org/licenses/by-nc-sa/4.0/\n// © LuxAlgo\n\n//@version=6\nindicator('x')\n")


def test_an_indicator_taken_from_luxalgo_keeps_its_page_its_licence_and_who_took_it():
    origin = luxalgo_origin(FOUND, "agent:0123456789ab", 1_800_000_000)
    assert origin == {
        "provider": "luxalgo", "slug": "tri-star", "name": "Tri-star",
        "url": "https://www.luxalgo.com/library/indicator/tri-star/", "chart_url": "https://vela.luxalgo.com/chart?x=1",
        "licence": "CC BY-NC-SA 4.0", "owner": "LuxAlgo", "taken_at": "2027-01-15T08:00:00Z",
        "by": "agent:0123456789ab",
    }


def test_origins_are_kept_beside_the_scripts_and_carried_to_a_script_made_from_one(tmp_path):
    origins = ScriptOrigins(str(tmp_path))
    origin = luxalgo_origin(FOUND, "operator", 1_800_000_000)
    origins.record("0123456789ab", origin)
    assert origins.get("0123456789ab") == origin
    assert json.loads((tmp_path / FILE).read_text()) == {"0123456789ab": origin}
    assert origins.carry("0123456789ab", "ba9876543210") == {**origin, "from_script": "0123456789ab"}
    assert origins.carry("000000000000", "ba9876543210") is None
    assert set(ScriptOrigins(str(tmp_path)).all()) == {"0123456789ab", "ba9876543210"}


@pytest.mark.parametrize("script_id", ["../origins", "0123456789AB", "", None, "0123456789abc"])
def test_an_origin_is_kept_only_for_a_scripts_id(tmp_path, script_id):
    with pytest.raises(ValueError):
        ScriptOrigins(str(tmp_path)).record(script_id, {"provider": "luxalgo"})


def test_a_script_with_no_record_is_said_derived_from_luxalgo_by_its_header(tmp_path):
    origins = ScriptOrigins(str(tmp_path))
    reads = []

    def source():
        reads.append(1)
        return HEADER

    assert origins.of("0123456789ab", 3, source) == {"provider": "luxalgo", "derived": True,
                                                     "licence": "CC BY-NC-SA 4.0", "owner": "LuxAlgo"}
    assert origins.of("0123456789ab", 3, source) is not None and len(reads) == 1
    assert origins.of("0123456789ab", 4, lambda: "//@version=6\nindicator('mine')\n") is None
    assert origins.of("0123456789ab") is None

    def unreadable():
        raise FileNotFoundError("gone")

    assert origins.of("ba9876543210", 1, unreadable) is None
    origins.record("0123456789ab", {"provider": "luxalgo", "slug": "tri-star"})
    assert origins.of("0123456789ab", 4, source)["slug"] == "tri-star"


def test_only_a_header_says_luxalgo():
    assert derived_from_header(HEADER)["provider"] == "luxalgo"
    assert derived_from_header("//@version=6\n// © Someone else\nindicator('x')") is None
    assert derived_from_header("//@version=6\n" + "x = 1\n" * 25 + "// © LuxAlgo\n") is None
    assert derived_from_header('//@version=6\nindicator("© LuxAlgo look-alike")') is None
    assert derived_from_header(None) is None


def test_a_file_that_cannot_be_read_holds_no_origin(tmp_path):
    (tmp_path / FILE).write_text("[not, an, object")
    assert ScriptOrigins(str(tmp_path)).all() == {}
    (tmp_path / FILE).write_text("[1, 2]")
    assert ScriptOrigins(str(tmp_path)).all() == {}


def test_a_licence_is_checked_before_going_live_when_it_forbids_commerce_or_cannot_be_read():
    from src.admin.script_origins import needs_licence_check

    assert needs_licence_check({"provider": "luxalgo", "licence": "CC BY-NC-SA 4.0"})
    assert needs_licence_check({"provider": "luxalgo", "licence": "Attribution-NonCommercial 4.0"})
    assert needs_licence_check({"provider": "luxalgo", "licence": "see LuxAlgo's NOTICE"})
    assert needs_licence_check({"provider": "luxalgo"})
    assert not needs_licence_check({"provider": "luxalgo", "licence": "Mozilla Public License 2.0"})
    assert not needs_licence_check(None)
