"""
What an image runs, container by container (src/release/manifest.py): the
rules of a closure on a made-up tree, the real code's closures, and the
tables (data files, dynamic imports, tools, entries) held to what they name.
"""

import ast
import json
import os
import pathlib
import re
import subprocess
import sys
import textwrap

import pytest
import yaml

from src.release import manifest
from src.release.manifest import CONTAINERS, DATA, DYNAMIC, ENTRIES, EVERY_CONTAINER, UNREAD, Source


def _tree(root: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    for path, text in files.items():
        full = root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(textwrap.dedent(text))
    return root


@pytest.fixture
def made_up(tmp_path, monkeypatch):
    """Two services: the trader, and a recorder whose main() brings the trader in."""
    monkeypatch.setattr(manifest, "ENTRIES", {"trader": "src.trader", "price_recorder": "src.price_recorder"})
    monkeypatch.setattr(manifest, "CONTAINERS", {"trader1": "trader", "price_recorder1": "price_recorder"})
    monkeypatch.setattr(manifest, "DYNAMIC", {"src.lib.loader": ("src.lib.loaded",)})
    monkeypatch.setattr(manifest, "DATA", {"src/lib/data/days.csv": ("src.lib.days",), "src/web/static": ("src.web.app",)})
    monkeypatch.setattr(manifest, "EVERY_CONTAINER", ("src/start.sh",))
    return _tree(tmp_path, {
        "VERSION": "0.1.0",
        "src/start.sh": "exec python\n",
        "src/trader/__init__.py": """
            import os
            from src.lib import shared

            def main():
                from src.lib.lazy import work
                work()

            if __name__ == "__main__":
                from src.lib.only_main import run
                run()
        """,
        "src/lib/__init__.py": "",
        "src/lib/shared.py": "from . import relative\nimport requests\n",
        "src/lib/relative.py": "",
        "src/lib/lazy.py": "",
        "src/lib/only_main.py": "",
        "src/lib/loader.py": "",
        "src/lib/loaded.py": "",
        "src/lib/days.py": "",
        "src/lib/data/days.csv": "a,b\n",
        "src/lib/child.py": "",
        "src/price_recorder/__init__.py": """
            from src.lib import loader
            from src.lib.days import read

            def main():
                from src.trader import main as trader_main
                import subprocess
                subprocess.run(["python", "-m", "src.lib.child"])

            if __name__ == "__main__":
                main()
        """,
        "src/web/__init__.py": "",
        "src/web/app.py": "",
        "src/web/static/index.html": "<html></html>",
    })


def test_an_entry_loads_its_imports_at_module_level_inside_functions_and_in_its_main(made_up):
    modules, outside = Source(str(made_up)).closure("trader")
    assert {"src.trader", "src.lib", "src.lib.shared", "src.lib.relative", "src.lib.lazy", "src.lib.only_main",
            } <= modules
    assert "src.price_recorder" not in modules
    assert {"os", "requests"} <= outside


def test_another_services_entry_counts_for_its_module_level_alone(made_up):
    modules, _ = Source(str(made_up)).closure("price_recorder")
    # Its own main(): the trader's entry is loaded by name, but only its module level.
    assert {"src.price_recorder", "src.trader", "src.lib.shared", "src.lib.child"} <= modules
    assert "src.lib.lazy" not in modules and "src.lib.only_main" not in modules
    # A module named in a string runs as __main__; a package that loads by name brings what it loads.
    assert {"src.lib.loader", "src.lib.loaded"} <= modules


def test_a_containers_files_hold_its_data_and_how_it_starts(made_up):
    found = manifest.manifest(str(made_up), with_libraries=False, with_platform=False)
    recorder = found["containers"]["price_recorder1"]["files"]
    trader = found["containers"]["trader1"]["files"]
    assert "src/lib/data/days.csv" in recorder and "src/lib/data/days.csv" not in trader
    assert "src/start.sh" in recorder and "src/start.sh" in trader
    assert "src/web/static/index.html" not in recorder
    assert found["version"] == "0.1.0"
    assert "VERSION" not in found["files"]


def test_comments_and_layout_change_nothing_and_code_does(made_up):
    before = manifest.manifest(str(made_up), with_libraries=False, with_platform=False)
    shared = made_up / "src/lib/shared.py"
    shared.write_text("# a comment\nfrom . import relative   # another\n\nimport requests\n")
    (made_up / "VERSION").write_text("0.2.0")
    same = manifest.manifest(str(made_up), with_libraries=False, with_platform=False)
    assert same["containers"] == before["containers"]
    shared.write_text("from . import relative\nimport requests\nTIMEOUT = 5\n")
    changed = manifest.manifest(str(made_up), with_libraries=False, with_platform=False)
    for container in ("trader1", "price_recorder1"):
        assert changed["containers"][container]["code"] != before["containers"][container]["code"]


def test_a_data_file_changes_only_its_readers(made_up):
    before = manifest.manifest(str(made_up), with_libraries=False, with_platform=False)
    (made_up / "src/lib/data/days.csv").write_text("a,b\n1,2\n")
    after = manifest.manifest(str(made_up), with_libraries=False, with_platform=False)
    assert after["containers"]["trader1"]["code"] == before["containers"]["trader1"]["code"]
    assert after["containers"]["price_recorder1"]["code"] != before["containers"]["price_recorder1"]["code"]


def test_it_runs_fed_on_stdin_as_the_server_feeds_it_to_an_image(made_up):
    """``python - --root /app < manifest.py``: no file of its own, nothing of WATA imported."""
    script = pathlib.Path(manifest.__file__).read_text()
    done = subprocess.run([sys.executable, "-I", "-", "--root", str(made_up), "--no-platform"], input=script,
                          capture_output=True, text=True, timeout=60, check=True)
    found = json.loads(done.stdout)
    assert found["manifest"] == manifest.MANIFEST_VERSION
    assert "src/web_server/__init__.py" not in found["files"]


# ── the real code ──


@pytest.fixture(scope="module")
def real():
    return manifest.manifest(".", with_libraries=False, with_platform=False)


def _files(real, container):
    return set(real["containers"][container]["files"])


def test_the_trading_services_never_reach_the_admin_the_backtests_or_the_research(real):
    for container in ("trader1", "position_monitor1", "web_server1", "scheduler1", "watchlist_manager1", "telegram1"):
        files = _files(real, container)
        for folder in ("src/admin/", "src/backtest/", "src/research/", "src/ta_engine/"):
            assert not any(path.startswith(folder) for path in files), (container, folder)


def test_the_ta_engine_reads_the_admins_rules_and_never_its_agent(real):
    files = _files(real, "ta_engine1")
    assert "src/admin/ta_rules.py" in files
    assert not any(path.startswith(("src/admin/agent", "src/admin/mcp_server", "src/admin/routers")) for path in files)
    assert not any(path.startswith("src/trader/") for path in files)


def test_the_agents_memory_reaches_the_admin_alone(real):
    """v0.60.0's files: what the first deploy of v0.61.0 should recreate is the admin."""
    memory = {"src/admin/agent_memory.py", "src/admin/agent/loop.py", "src/admin/mcp_server/tools/memory.py",
              "src/admin/routers/agent.py"}
    for container in real["containers"]:
        reached = memory & _files(real, container)
        assert reached == (memory if container == "admin1" else set()), container


def test_the_containers_are_the_compose_files_and_the_entries_the_start_script():
    compose = yaml.safe_load(pathlib.Path("deploy/docker-compose.yml").read_text())["services"]
    roles = {}
    for name, service in compose.items():
        for line in service.get("environment") or []:
            if line.startswith("WATA_APP_ROLE="):
                roles[name] = line.split("=", 1)[1]
    assert CONTAINERS == roles
    script = pathlib.Path("src/start_python_script.sh").read_text()
    for role, entry in ENTRIES.items():
        path = entry.replace(".", "/") + "/__init__.py"
        assert re.search(rf'"{role}"\)\s*\n\s*exec python -u (/app/)?{re.escape(path)}', script), role
    assert set(ENTRIES) == set(CONTAINERS.values())


def test_every_file_under_src_that_is_not_python_is_declared():
    declared_folders = [path for path in DATA if not os.path.splitext(path)[1]]
    for path in sorted(pathlib.Path("src").rglob("*")):
        relative = path.as_posix()
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".py" or path.name.startswith("."):
            continue
        assert (relative in DATA or relative in EVERY_CONTAINER or relative in UNREAD
                or any(relative.startswith(folder + "/") for folder in declared_folders)), relative
    for path, readers in DATA.items():
        for reader in readers:
            source = pathlib.Path(reader.replace(".", "/") + ".py").read_text()
            name = os.path.basename(path)
            assert name in source or name.split(".")[0].upper() in source, (path, reader)


def test_every_module_loaded_by_name_is_declared(real):
    """In what a container can load: src/release/check.py, run by hand in an image, is no container's."""
    loadable = {path for entry in real["containers"].values() for path in entry["files"]}
    for path in pathlib.Path("src").rglob("*.py"):
        if path.as_posix() not in loadable or "import_module(" not in path.read_text():
            continue
        module = path.with_suffix("").as_posix().replace("/", ".").removesuffix(".__init__")
        assert module in DYNAMIC, f"{path} loads modules by name: name them in DYNAMIC"


def test_every_module_run_by_name_is_a_string_the_closure_sees():
    """``python -m <module>`` from the admin: launched with literal names (src/admin/processes.py)."""
    launched = set()
    for path in pathlib.Path("src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.match(r"^src(\.\w+)+$", node.value):
                launched.add(node.value)
    modules = manifest.modules_of(".")
    assert launched and launched <= set(modules)


def test_the_servers_commands_run_on_the_newest_release_never_inside_a_service():
    """A command run with ``docker exec`` would run its container's code, and bring its files into the closure."""
    script = pathlib.Path("deploy/install_package.sh").read_text()
    assert not re.search(r"docker exec[^\n]* python -m", script)
    assert re.findall(r"watatool (\w+) python -m ([\w.]+)", script)


def test_no_module_imports_a_folder_of_src_without_its_prefix():
    """Services run as scripts have their own folder on sys.path: `import trade` would load src/trade."""
    top = {path.name for path in pathlib.Path("src").iterdir() if path.is_dir() or path.suffix == ".py"}
    top = {name.removesuffix(".py") for name in top} - {"__pycache__"}
    for path in pathlib.Path("src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in top, (str(path), name)


@pytest.mark.parametrize("role", ["trader", "position_monitor", "watchlist_manager", "web_server", "telegram",
                                  "admin", "capital_feed", "ta_engine", "price_recorder", "service_control"])
def test_what_an_entry_loads_at_import_is_inside_its_static_closure(role, real):
    """The modules Python loads importing the entry (its main() aside) are among those the manifest names."""
    program = ("import importlib, json, sys\nimportlib.import_module(sys.argv[1])\n"
               "print(json.dumps(sorted(name for name in sys.modules if name.startswith('src.'))))")
    env = {**os.environ, "PYTHONPATH": "./:src/", "WATA_CONFIG_PATH": "tests/test_config.json"}
    done = subprocess.run([sys.executable, "-c", program, ENTRIES[role]], capture_output=True, text=True,
                          timeout=120, env=env)
    if done.returncode != 0 and "ModuleNotFoundError" in done.stderr:
        pytest.skip(f"a library this environment lacks: {done.stderr.strip().splitlines()[-1]}")
    assert done.returncode == 0, done.stderr[-2000:]
    modules = manifest.modules_of(".")
    # Namespace packages (src.trade has no __init__.py) are no file.
    loaded = set(json.loads(done.stdout.strip().splitlines()[-1])) & set(modules)
    container = next(name for name, found in CONTAINERS.items() if found == role)
    files = _files(real, container)
    named = {module for module, path in modules.items() if path in files}
    assert loaded - named == set(), role
