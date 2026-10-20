"""
Technical Analysis in the admin: the Pine scripts kept as files
(src/admin/ta_scripts.py), their routes (src/admin/routers/ta.py) and the
worker files' own Content-Security-Policy (src/admin/app.py).
"""

import hashlib
import json
import os

import pytest

import src.admin.ta_scripts as ta_scripts
from src.admin.app import CONTENT_SECURITY_POLICY, WORKER_CONTENT_SECURITY_POLICY
from src.admin.context import AdminSettings
from src.admin.ta_scripts import (
    InvalidScript,
    ScriptConflict,
    ScriptNotFound,
    ScriptStore,
    pine_facts,
    strip_comments,
)
from src.configuration import ConfigurationManager
from tests.test_admin_api import (  # noqa: F401 (fixtures)
    PASSWORD,
    SAME_ORIGIN,
    built_interface,
    config_path,
    ctx,
    make_client,
)

EMA = """//@version=6
indicator("EMA", overlay = true)
length = input.int(20, "Length")
plot(ta.ema(close, length), "EMA")
"""

LIBRARY = """//@version=5
// @description Helpers.
library("Helpers", true)

export double(float value) =>
    value * 2
"""

IMPORTER = """//@version=6
indicator("Doubled")
import someone/Helpers/1 as helpers
plot(helpers.double(close))
"""


@pytest.fixture
def store(tmp_path):
    return ScriptStore(str(tmp_path / "ta"))


@pytest.fixture
def ta_client(ctx, store, tmp_path):
    ctx.ta_scripts = store
    with make_client(ctx, tmp_path / "no-interface") as client:
        response = client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
        assert response.status_code == 204
        yield client


# ── what is read of a source ──


def test_the_facts_of_an_indicator_a_library_and_a_script_importing_it():
    assert pine_facts(EMA) == ta_scripts.PineFacts(pine_version=6, kind="indicator", library_name=None, imports=())
    assert pine_facts(LIBRARY) == ta_scripts.PineFacts(pine_version=5, kind="library", library_name="Helpers", imports=())
    assert pine_facts(IMPORTER).imports == (
        {"user": "someone", "name": "Helpers", "version": 1, "alias": "helpers"},
    )


def test_an_import_without_alias_is_known_by_the_library_name():
    source = '//@version=6\nindicator("x")\nimport someone/Helpers/2\nplot(close)\n'

    assert pine_facts(source).imports == ({"user": "someone", "name": "Helpers", "version": 2, "alias": "Helpers"},)


def test_a_library_title_may_be_on_the_next_line_and_given_by_name():
    assert pine_facts('//@version=6\nlibrary(\n    title = "Multi"\n)\n').library_name == "Multi"


def test_comments_are_left_out_but_strings_and_lines_stay():
    source = 'a = "x // not a comment" // a comment\nb = \'y\' // another\n'

    assert strip_comments(source) == 'a = "x // not a comment" \nb = \'y\' \n'


@pytest.mark.parametrize("source, refusal", [
    ("", "empty"),
    ("//@PineTS\nindicator('x')\n", "//@PineTS"),
    ('indicator("x")\nplot(close)\n', "//@version=5 or //@version=6"),
    ('//@version=4\nstudy("x")\n', "version 4 is not supported"),
    ('//@version=6\nplot(close)\n', "must start with indicator(), strategy() or library()"),
    ('//@version=6\n  indicator("x")\n', "must start with"),
    ('//@version=6\nlibrary()\n', "A library must be named"),
])
def test_a_source_that_is_not_native_pine_of_a_supported_version_is_refused(source, refusal):
    with pytest.raises(InvalidScript, match=refusal.replace("(", r"\(").replace(")", r"\)")):
        pine_facts(source)


def test_a_source_too_large_is_refused(monkeypatch):
    monkeypatch.setattr(ta_scripts, "MAX_SOURCE_BYTES", 64)

    with pytest.raises(InvalidScript, match="KB at most"):
        pine_facts(EMA + "// " + "x" * 64)


# ── the store ──


def test_a_created_script_is_a_folder_with_its_description_and_its_first_version(store):
    script = store.create("  My   EMA ", EMA, "An average")

    assert len(script["id"]) == 12 and int(script["id"], 16) >= 0
    assert script["name"] == "My EMA"
    assert script["kind"] == "indicator" and script["pine_version"] == 6
    assert script["version"] == 1 and script["source"] == EMA
    folder = os.path.join(store.root, "scripts", script["id"])
    with open(os.path.join(folder, "v0001.pine"), encoding="utf-8") as pine_file:
        assert pine_file.read() == EMA
    with open(os.path.join(folder, "script.json"), encoding="utf-8") as meta_file:
        meta = json.load(meta_file)
    assert meta["versions"] == [{
        "number": 1, "saved_at": meta["created_at"], "size": len(EMA.encode()),
        "sha256": hashlib.sha256(EMA.encode()).hexdigest(),
    }]


def test_a_changed_source_is_a_new_version_and_the_old_one_is_kept(store):
    script = store.create("EMA", EMA)
    changed = EMA.replace("20", "50")

    saved = store.save(script["id"], 1, source=changed)

    assert saved["version"] == 2 and saved["source"] == changed
    assert [entry["number"] for entry in saved["versions"]] == [1, 2]
    assert store.source(script["id"], 1) == EMA
    assert store.version(script["id"], 2)["source"] == changed


def test_the_same_source_with_a_new_name_changes_the_name_and_makes_no_version(store):
    script = store.create("EMA", EMA)

    saved = store.save(script["id"], 1, source=EMA, name="EMA 20", description="Twenty bars")

    assert (saved["version"], saved["name"], saved["description"]) == (1, "EMA 20", "Twenty bars")


def test_a_save_made_on_an_older_version_is_refused(store):
    script = store.create("EMA", EMA)
    store.save(script["id"], 1, source=EMA.replace("20", "30"))

    with pytest.raises(ScriptConflict, match="saved meanwhile"):
        store.save(script["id"], 1, source=EMA.replace("20", "40"))
    assert store.script(script["id"])["version"] == 2


def test_names_are_unique_whatever_their_case(store):
    store.create("EMA", EMA)

    with pytest.raises(ScriptConflict, match="already exists"):
        store.create("ema", EMA)


def test_a_library_is_found_by_the_name_it_declares_and_two_cannot_share_it(store):
    created = store.create("My helpers", LIBRARY)

    assert store.library("Helpers")["id"] == created["id"]
    assert store.library("Helpers")["source"] == LIBRARY
    with pytest.raises(ScriptConflict, match="A library named 'Helpers' already exists"):
        store.create("Other helpers", LIBRARY)
    with pytest.raises(ScriptNotFound):
        store.library("Unknown")


def test_a_duplicate_is_a_new_script_with_the_current_source(store):
    script = store.create("EMA", EMA, "An average")
    store.save(script["id"], 1, source=EMA.replace("20", "30"))

    copy = store.duplicate(script["id"], "EMA copy")

    assert copy["id"] != script["id"] and copy["version"] == 1
    assert copy["source"] == EMA.replace("20", "30") and copy["description"] == "An average"


def test_a_library_cannot_be_duplicated(store):
    library = store.create("Helpers", LIBRARY)

    with pytest.raises(InvalidScript, match="known by the name it declares"):
        store.duplicate(library["id"], "Helpers copy")


def test_a_deleted_script_goes_to_the_trash(store):
    script = store.create("EMA", EMA)

    store.delete(script["id"])

    assert store.scripts() == []
    trashed = os.listdir(os.path.join(store.root, "trash"))
    assert len(trashed) == 1 and trashed[0].startswith(script["id"] + "-")
    with pytest.raises(ScriptNotFound):
        store.script(script["id"])


def test_a_script_still_in_use_is_not_deleted(store):
    library = store.create("Helpers", LIBRARY)
    importer = store.create("Doubled", IMPORTER)

    assert store.importers("Helpers") == ["Doubled"]
    with pytest.raises(ScriptConflict, match="used by the script 'Doubled'"):
        store.delete(library["id"])
    # What else relies on a script is asked under the store's lock.
    with pytest.raises(ScriptConflict, match="'Doubled' is used by the signal rule 'R'$"):
        store.delete(importer["id"], lambda meta: ["the signal rule 'R'"])
    assert store.script(library["id"])["name"] == "Helpers"


@pytest.mark.parametrize("script_id", ["../etc", "0123456789AB", "0123456789a", "", "0123456789ab/.."])
def test_an_id_that_is_not_one_of_ours_reaches_no_path(store, script_id):
    with pytest.raises(ScriptNotFound):
        store.script(script_id)


def test_the_number_of_scripts_is_bounded(store, monkeypatch):
    monkeypatch.setattr(ta_scripts, "MAX_SCRIPTS", 1)
    store.create("EMA", EMA)

    with pytest.raises(InvalidScript, match="1 scripts at most"):
        store.create("EMA 2", EMA)


def test_a_layout_is_kept_as_it_comes(store):
    assert store.workspace("main") is None

    store.save_workspace("main", '{"version":1,"layout":"1","charts":[]}')

    assert store.workspace("main") == '{"version":1,"layout":"1","charts":[]}'


@pytest.mark.parametrize("key", ["../x", "Main", "", "a" * 65])
def test_a_layout_key_is_checked_before_any_path(store, key):
    with pytest.raises(InvalidScript, match="layout key"):
        store.save_workspace(key, "{}")


def test_a_layout_too_large_is_refused(store, monkeypatch):
    monkeypatch.setattr(ta_scripts, "MAX_WORKSPACE_BYTES", 10)

    with pytest.raises(InvalidScript, match="KB at most"):
        store.save_workspace("main", "x" * 11)


# ── the routes ──


def test_scripts_are_created_listed_read_saved_and_deleted(ta_client):
    created = ta_client.post("/api/ta/scripts", json={"name": "EMA", "source": EMA}, headers=SAME_ORIGIN)
    assert created.status_code == 201
    script_id = created.json()["id"]

    listed = ta_client.get("/api/ta/scripts").json()["items"]
    assert [(item["id"], item["name"], item["kind"]) for item in listed] == [(script_id, "EMA", "indicator")]
    assert "source" not in listed[0]

    saved = ta_client.put(
        f"/api/ta/scripts/{script_id}", json={"base_version": 1, "source": EMA.replace("20", "30")},
        headers=SAME_ORIGIN,
    )
    assert saved.status_code == 200 and saved.json()["version"] == 2
    stale = ta_client.put(
        f"/api/ta/scripts/{script_id}", json={"base_version": 1, "source": EMA}, headers=SAME_ORIGIN,
    )
    assert stale.status_code == 409 and "saved meanwhile" in stale.json()["detail"]

    assert ta_client.get(f"/api/ta/scripts/{script_id}/versions/1").json()["source"] == EMA
    assert ta_client.get(f"/api/ta/scripts/{script_id}/versions/9").status_code == 404

    assert ta_client.delete(f"/api/ta/scripts/{script_id}", headers=SAME_ORIGIN).status_code == 204
    assert ta_client.get(f"/api/ta/scripts/{script_id}").status_code == 404


def test_a_source_pinets_would_run_as_javascript_is_refused_by_the_route(ta_client):
    response = ta_client.post(
        "/api/ta/scripts", json={"name": "JS", "source": "//@PineTS\nindicator('x')"}, headers=SAME_ORIGIN,
    )

    assert response.status_code == 422 and "//@PineTS" in response.json()["detail"]


def test_a_change_from_another_origin_is_refused(ta_client):
    response = ta_client.post(
        "/api/ta/scripts", json={"name": "EMA", "source": EMA}, headers={"Origin": "https://elsewhere.test"},
    )

    assert response.status_code == 403


def test_a_library_in_use_is_not_deleted_by_the_route(ta_client):
    library = ta_client.post("/api/ta/scripts", json={"name": "Helpers", "source": LIBRARY}, headers=SAME_ORIGIN)
    ta_client.post("/api/ta/scripts", json={"name": "Doubled", "source": IMPORTER}, headers=SAME_ORIGIN)

    response = ta_client.delete(f"/api/ta/scripts/{library.json()['id']}", headers=SAME_ORIGIN)

    assert response.status_code == 409 and "Doubled" in response.json()["detail"]
    assert ta_client.get("/api/ta/libraries/Helpers").json()["source"] == LIBRARY
    assert ta_client.get("/api/ta/libraries/Unknown").status_code == 404


def test_a_script_is_downloaded_as_a_pine_file(ta_client):
    created = ta_client.post(
        "/api/ta/scripts", json={"name": "My EMA (20)", "source": EMA}, headers=SAME_ORIGIN,
    ).json()

    response = ta_client.get(f"/api/ta/scripts/{created['id']}/source.pine")

    assert response.text == EMA
    assert response.headers["content-disposition"] == 'attachment; filename="My-EMA--20.pine"'


def test_a_copy_is_made_through_the_route(ta_client):
    created = ta_client.post("/api/ta/scripts", json={"name": "EMA", "source": EMA}, headers=SAME_ORIGIN).json()

    copy = ta_client.post(
        f"/api/ta/scripts/{created['id']}/duplicate", json={"name": "EMA copy"}, headers=SAME_ORIGIN,
    )

    assert copy.status_code == 201 and copy.json()["name"] == "EMA copy"


def test_a_layout_is_stored_and_read_back(ta_client):
    assert ta_client.get("/api/ta/workspaces/main").json() == {"value": None}

    stored = ta_client.put("/api/ta/workspaces/main", json={"value": "{\"version\":1}"}, headers=SAME_ORIGIN)

    assert stored.status_code == 204
    assert ta_client.get("/api/ta/workspaces/main").json() == {"value": "{\"version\":1}"}
    assert ta_client.put("/api/ta/workspaces/Main", json={"value": "{}"}, headers=SAME_ORIGIN).status_code == 422


def test_without_a_store_the_routes_say_so(ctx, tmp_path):
    with make_client(ctx, tmp_path / "no-interface") as client:
        client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)

        response = client.get("/api/ta/scripts")

    assert response.status_code == 503


# ── where the store lives ──


def test_scripts_live_in_the_admins_own_folder_unless_configured(config_path, tmp_path):
    settings = AdminSettings.from_config(ConfigurationManager(config_path))
    assert settings.ta_path == "/app/var/lib/admin/ta"

    with open(config_path, encoding="utf-8") as config_file:
        config = json.load(config_file)
    config.setdefault("admin", {}).setdefault("persistant", {})["ta_path"] = str(tmp_path / "ta")
    with open(config_path, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file)

    assert AdminSettings.from_config(ConfigurationManager(config_path)).ta_path == str(tmp_path / "ta")


# ── the worker files' own policy ──


@pytest.mark.parametrize("name", ["pine-worker-BRHV8TNI.js", "vela-pine-worker-acb73877c9ca.js"])
def test_only_the_pine_workers_may_evaluate_code_and_they_may_connect_nowhere(ctx, built_interface, name):
    (built_interface / "assets" / name).write_text("self.onmessage = () => {}")

    with make_client(ctx, built_interface) as client:
        worker = client.get(f"/assets/{name}")
        page = client.get("/ta/charts")
        other = client.get("/assets/app.js")

    assert worker.status_code == 200
    assert worker.headers["content-security-policy"] == WORKER_CONTENT_SECURITY_POLICY
    assert "'unsafe-eval'" in WORKER_CONTENT_SECURITY_POLICY
    assert "connect-src" not in WORKER_CONTENT_SECURITY_POLICY and "default-src 'none'" in WORKER_CONTENT_SECURITY_POLICY
    assert page.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert other.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert "'unsafe-eval'" not in CONTENT_SECURITY_POLICY


@pytest.mark.parametrize("path", ["/pine-worker-abc.js", "/assets/pine-worker-abc.js.map", "/assets/x/pine-worker-abc.js"])
def test_a_file_named_like_a_worker_elsewhere_keeps_the_pages_policy(ctx, built_interface, path):
    with make_client(ctx, built_interface) as client:
        response = client.get(path)

    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
