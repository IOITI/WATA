"""
What the Indicators pages say of a script beside its code (src/admin/script_details.py and its routes in
src/admin/routers/ta.py): its star, its family, its How it works and its picture, kept in the script's own folder;
LuxAlgo's write-up, family and picture for a script taken from its Library.
"""

import asyncio
import json
import os

import pytest

from src.admin.script_details import (
    CUT, HISTORY, MAX_FAMILY, MAX_HOW, MAX_PICTURE_BYTES, ScriptDetails, from_luxalgo, luxalgo_picture, with_details,
)
from src.admin.ta_scripts import InvalidScript, ScriptConflict, ScriptNotFound
from tests.test_admin_api import SAME_ORIGIN, config_path  # noqa: F401 (fixtures)
from tests.test_admin_ta_rules import STRATEGY, api, cross, feed_config_path, scripts  # noqa: F401 (fixtures)

PNG = b"\x89PNG\r\n\x1a\n" + bytes(64)
JPEG = b"\xff\xd8\xff\xe0" + bytes(64)
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + bytes(64)
GIF = b"GIF89a" + bytes(64)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
LUXALGO_IMAGE = "https://luxalgo-production.s3.amazonaws.com/library/indicators/vwap/cover.png"
NOW = 1_793_700_000.0


@pytest.fixture
def details(scripts):
    return ScriptDetails(scripts, clock=lambda: NOW)


class LuxAlgo:
    """LuxAlgo's client as from_luxalgo uses it: one indicator, its families, and what was asked."""

    def __init__(self, indicator: dict, families: dict | None = None):
        self.indicator = indicator
        self.families = families if families is not None else {"families": [{"key": "volume-orderflow", "name": "Volume & Order Flow"}]}
        self.asked = []

    async def read(self, tool: str, arguments: dict | None = None):
        self.asked.append(tool)
        if tool == "library_list_families":
            if isinstance(self.families, Exception):
                raise self.families
            return self.families
        return self.indicator


def test_a_script_has_nothing_beside_its_code_until_it_is_given_it(details, cross):
    assert details.of(cross["id"]) == {"favourite": False, "family": None, "picture": None, "luxalgo_picture": None, "how": None}
    assert details.how(cross["id"]) is None and details.how_history(cross["id"]) == [] and details.picture(cross["id"]) is None


def test_several_scripts_are_put_among_the_favourites_and_one_not_there_is_named(details, cross):
    answer = details.set_favourites([cross["id"], cross["id"], "ffffffffffff", "../../etc"], True)

    assert answer["changed"] == [cross["id"]]
    assert [entry["id"] for entry in answer["kept"]] == ["ffffffffffff", "../../etc"]
    assert details.of(cross["id"])["favourite"] is True
    details.set_favourites([cross["id"]], False)
    assert details.of(cross["id"])["favourite"] is False


def test_a_family_is_a_short_name_on_one_line_and_none_clears_it(details, cross):
    assert details.set_family(cross["id"], "  Trend \n  following ", "operator")["family"] == "Trend following"
    with pytest.raises(InvalidScript, match="40 characters"):
        details.set_family(cross["id"], "x" * (MAX_FAMILY + 1), "operator")
    with pytest.raises(InvalidScript, match="one line"):
        details.set_family(cross["id"], "Trend\x07", "operator")
    assert details.set_family(cross["id"], "", "operator")["family"] is None


def test_each_version_of_a_how_it_works_is_kept_and_one_written_from_an_older_is_refused(details, cross):
    first = details.save_how(cross["id"], "  It crosses two averages.\r\n", 0, "operator")
    with pytest.raises(ScriptConflict, match="saved meanwhile"):
        details.save_how(cross["id"], "Mine, written from nothing", 0, "agent:c0ffee")
    second = details.save_how(cross["id"], "It crosses a fast and a slow average.", first["revision"], "agent:c0ffee")
    emptied = details.save_how(cross["id"], "", second["revision"], "operator")

    assert (first["revision"], first["text"], second["revision"], emptied["revision"]) == (1, "It crosses two averages.", 2, 3)
    # Emptied, it has none, while its revision is what the next one starts from.
    assert details.how(cross["id"]) is None and details.of(cross["id"])["how"]["revision"] == 3
    history = details.how_history(cross["id"])
    assert [(entry["revision"], entry["by"], entry["text"]) for entry in history] == [
        (3, "operator", ""), (2, "agent:c0ffee", "It crosses a fast and a slow average."), (1, "operator", "It crosses two averages."),
    ]
    # Put back: the second saved again, a new version.
    back = details.save_how(cross["id"], history[1]["text"], 3, "operator")
    assert (back["revision"], details.how(cross["id"])["text"]) == (4, "It crosses a fast and a slow average.")
    with pytest.raises(InvalidScript, match="30,000"):
        details.save_how(cross["id"], "x" * (MAX_HOW + 1), 4, "operator")


def test_a_history_gives_its_newest_versions(details, cross):
    for revision in range(HISTORY + 3):
        details.save_how(cross["id"], f"Version {revision + 1}", revision, "operator")

    history = details.how_history(cross["id"])
    assert len(history) == HISTORY and history[0]["text"] == f"Version {HISTORY + 3}"


@pytest.mark.parametrize("data,media_type,name", [(PNG, "image/png", "picture.png"), (JPEG, "image/jpeg", "picture.jpg"),
                                                  (WEBP, "image/webp", "picture.webp")])
def test_a_picture_is_a_png_a_jpeg_or_a_webp_known_by_its_first_bytes(details, cross, data, media_type, name):
    kept = details.set_picture(cross["id"], data, "operator")

    path, found_type = details.picture(cross["id"])
    assert (os.path.basename(path), found_type, kept["picture"]["size"]) == (name, media_type, len(data))
    with open(path, "rb") as in_file:
        assert in_file.read() == data


def test_anything_else_is_refused_as_a_picture_and_a_new_one_replaces_the_old(details, cross):
    for data in (SVG, GIF, b"not an image"):
        with pytest.raises(InvalidScript, match="PNG, JPEG or WebP"):
            details.set_picture(cross["id"], data, "operator")
    with pytest.raises(InvalidScript, match="2 MB"):
        details.set_picture(cross["id"], PNG + bytes(MAX_PICTURE_BYTES), "operator")

    details.set_picture(cross["id"], PNG, "operator")
    details.set_picture(cross["id"], JPEG, "operator")
    folder = os.path.dirname(details.picture(cross["id"])[0])
    assert sorted(name for name in os.listdir(folder) if name.startswith("picture")) == ["picture.jpg"]
    details.drop_picture(cross["id"])
    assert details.picture(cross["id"]) is None and not any(name.startswith("picture") for name in os.listdir(folder))


def test_nothing_is_written_for_a_script_that_is_not_there(details, scripts):
    for script_id in ("ffffffffffff", "../../etc", "", None):
        with pytest.raises(ScriptNotFound):
            details.set_family(script_id, "Trend", "operator")
        with pytest.raises(ScriptNotFound):
            details.save_how(script_id, "Text", 0, "operator")
    # Never a script's folder made again by a write.
    assert not os.path.exists(os.path.join(scripts.root, "scripts", "ffffffffffff"))


def test_a_deleted_script_takes_them_along_and_a_copy_takes_them_but_its_star(details, scripts, cross):
    details.set_favourites([cross["id"]], True)
    details.set_family(cross["id"], "Trend", "operator")
    details.save_how(cross["id"], "It crosses two averages.", 0, "operator")
    details.set_picture(cross["id"], PNG, "operator")
    copy = scripts.duplicate(cross["id"], "Cross copy")

    details.carry(cross["id"], copy["id"], "operator")
    scripts.delete(cross["id"])

    found = details.of(copy["id"])
    assert (found["favourite"], found["family"], found["picture"]["type"]) == (False, "Trend", "image/png")
    assert details.how(copy["id"])["text"] == "It crosses two averages." and details.how(copy["id"])["from"] == {"script": cross["id"]}
    [trashed] = os.listdir(os.path.join(scripts.root, "trash"))
    assert {"details.json", "picture.png", "how"} <= set(os.listdir(os.path.join(scripts.root, "trash", trashed)))


def test_luxalgos_pictures_are_kept_only_from_its_image_host_under_its_library():
    assert luxalgo_picture(LUXALGO_IMAGE) == LUXALGO_IMAGE
    for url in ("http://luxalgo-production.s3.amazonaws.com/library/a.png", "https://evil.example/library/a.png",
                "https://luxalgo-production.s3.amazonaws.com/private/a.png", f"{LUXALGO_IMAGE}?x=1",
                "https://luxalgo-production.s3.amazonaws.com/library/a.svg", None, 3):
        assert luxalgo_picture(url) is None


def test_luxalgo_gives_a_script_taken_from_it_its_write_up_family_and_picture(details, cross):
    luxalgo = LuxAlgo({"slug": "vwap-made-up", "family": "volume-orderflow", "body_markdown": "## VWAP\nThe volume-weighted price.",
                       "image_url": LUXALGO_IMAGE})

    filled = asyncio.run(from_luxalgo(details, luxalgo, cross["id"], "vwap-made-up", "operator"))

    assert filled == ["how", "family", "picture"]
    how = details.how(cross["id"])
    assert how["text"] == "## VWAP\nThe volume-weighted price." and how["from"] == {
        "provider": "luxalgo", "slug": "vwap-made-up", "url": "https://www.luxalgo.com/library/indicator/vwap-made-up/"}
    assert (details.of(cross["id"])["family"], details.of(cross["id"])["luxalgo_picture"]) == ("Volume & Order Flow", LUXALGO_IMAGE)
    # Asked again: only what it has none of, unless replaced.
    details.set_family(cross["id"], "Mine", "operator")
    assert asyncio.run(from_luxalgo(details, luxalgo, cross["id"], "vwap-made-up", "operator")) == ["picture"]
    assert asyncio.run(from_luxalgo(details, luxalgo, cross["id"], "vwap-made-up", "operator", replace=True)) == ["how", "family", "picture"]
    assert (details.how(cross["id"])["revision"], details.of(cross["id"])["family"]) == (2, "Volume & Order Flow")


def test_a_write_up_too_long_is_cut_and_a_family_unnamed_is_its_key(details, cross):
    luxalgo = LuxAlgo({"family": "smc-ict", "body_markdown": "x" * (MAX_HOW + 500)}, families=RuntimeError("down"))

    asyncio.run(from_luxalgo(details, luxalgo, cross["id"], "long-made-up", "agent:c0ffee"))

    text = details.how(cross["id"])["text"]
    assert len(text) == MAX_HOW and text.endswith(CUT) and details.how(cross["id"])["by"] == "agent:c0ffee"
    assert details.of(cross["id"])["family"] == "Smc ict"


def test_the_pages_read_a_script_with_its_star_family_picture_and_how(details, cross):
    details.set_picture(cross["id"], PNG, "operator")
    shown = with_details(details, {"id": cross["id"], "name": "Cross"})
    assert shown["picture"] == f"/api/ta/scripts/{cross['id']}/picture?v=2026-11-03T10%3A00%3A00%2B00%3A00"
    assert shown["own_picture"] is True and shown["how"] is None
    details.drop_picture(cross["id"])
    details.set_luxalgo_picture(cross["id"], LUXALGO_IMAGE)
    assert with_details(details, {"id": cross["id"]})["picture"] == LUXALGO_IMAGE
    assert with_details(None, {"id": cross["id"]}) == {"id": cross["id"]}


# ── the routes ──


@pytest.fixture
def pages(api):
    client, ctx = api
    ctx.script_details = ScriptDetails(ctx.ta_scripts)
    return client, ctx


def test_the_pages_star_name_and_describe_a_script_through_the_routes(pages, cross):
    client, _ = pages
    base = f"/api/ta/scripts/{cross['id']}"

    listed = {item["id"]: item for item in client.get("/api/ta/scripts").json()["items"]}
    assert (listed[cross["id"]]["favourite"], listed[cross["id"]]["family"], listed[cross["id"]]["picture"]) == (False, None, None)
    starred = client.post("/api/ta/scripts/favourites", json={"ids": [cross["id"], "ffffffffffff"], "favourite": True}, headers=SAME_ORIGIN)
    assert starred.json()["changed"] == [cross["id"]] and starred.json()["kept"][0]["id"] == "ffffffffffff"
    assert client.put(f"{base}/family", json={"family": "Trend"}, headers=SAME_ORIGIN).json()["family"] == "Trend"
    assert client.get(base).json()["favourite"] is True

    assert client.get(f"{base}/how-it-works").json() == {"text": "", "revision": 0, "by": None, "at": None, "from": None}
    saved = client.put(f"{base}/how-it-works", json={"text": "It crosses two averages.", "revision": 0}, headers=SAME_ORIGIN)
    assert saved.status_code == 200 and saved.json()["revision"] == 1 and saved.json()["by"] == "operator"
    stale = client.put(f"{base}/how-it-works", json={"text": "From nothing", "revision": 0}, headers=SAME_ORIGIN)
    assert stale.status_code == 409 and "saved meanwhile" in stale.json()["detail"]
    assert client.get(f"{base}/how-it-works/history").json()["items"][0]["text"] == "It crosses two averages."
    assert client.get(base).json()["how"]["revision"] == 1


def test_a_picture_is_uploaded_served_as_it_is_and_removed(pages, cross):
    client, _ = pages
    base = f"/api/ta/scripts/{cross['id']}"

    assert client.get(f"{base}/picture").status_code == 404
    refused = client.put(f"{base}/picture", files={"file": ("chart.svg", SVG, "image/svg+xml")}, headers=SAME_ORIGIN)
    assert refused.status_code == 422
    shown = client.put(f"{base}/picture", files={"file": ("chart.png", PNG, "image/png")}, headers=SAME_ORIGIN).json()
    assert shown["picture"].startswith(f"{base}/picture?v=") and shown["own_picture"] is True
    served = client.get(shown["picture"])
    assert served.content == PNG and served.headers["content-type"] == "image/png"
    # Asked by its version, kept by the browser; without it, as every answer of the API.
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert client.get(f"{base}/picture").headers["cache-control"] == "no-store"
    assert client.get(f"{base}/how-it-works").headers["cache-control"] == "no-store"
    assert client.delete(f"{base}/picture", headers=SAME_ORIGIN).status_code == 204
    assert client.get(f"{base}/picture").status_code == 404


def test_a_copy_made_from_the_page_takes_them_and_luxalgo_is_asked_only_for_its_own(pages, cross):
    client, ctx = pages
    base = f"/api/ta/scripts/{cross['id']}"
    client.put(f"{base}/family", json={"family": "Trend"}, headers=SAME_ORIGIN)

    copy = client.post(f"{base}/duplicate", json={"name": "Cross, mine"}, headers=SAME_ORIGIN).json()

    assert copy["family"] == "Trend" and copy["favourite"] is False
    # LuxAlgo's tools off, or a script not taken from it: nothing asked.
    refused = client.post(f"{base}/from-luxalgo", json={}, headers=SAME_ORIGIN)
    assert refused.status_code == 409 and "switched off" in refused.json()["detail"]
    ctx.luxalgo = LuxAlgo({})
    refused = client.post(f"{base}/from-luxalgo", json={}, headers=SAME_ORIGIN)
    assert refused.status_code == 409 and "not taken from LuxAlgo" in refused.json()["detail"] and ctx.luxalgo.asked == []


def test_a_saved_version_comes_back_with_its_details(pages, cross):
    client, _ = pages
    base = f"/api/ta/scripts/{cross['id']}"
    client.put(f"{base}/family", json={"family": "Trend"}, headers=SAME_ORIGIN)

    saved = client.put(base, json={"base_version": 1, "source": STRATEGY + "\n// again\n", "name": "Cross", "description": ""},
                       headers=SAME_ORIGIN)

    assert saved.status_code == 200 and saved.json()["family"] == "Trend" and saved.json()["version"] == 2
    assert json.loads(json.dumps(saved.json()))["how"] is None
