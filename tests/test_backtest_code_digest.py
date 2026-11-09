"""
The code a backtest's result rests on (``_CODE``, ``code_digest`` in src/backtest/figures.py): a run made
with other code is marked stale. Of the TA engine, only what a replay runs counts, so that a change to
the live loop alone leaves every run as it was; a file it runs that is left out would be a change unseen.
"""

import ast
import os
import shutil

from src.backtest.figures import _CODE, code_digest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _python_files(part: str) -> list[str]:
    path = os.path.join(ROOT, part)
    if os.path.isdir(path):
        return sorted(os.path.join(folder, name) for folder, _, names in os.walk(path) for name in names if name.endswith(".py"))
    return [path]


def _ta_engine_modules(path: str) -> set[str]:
    """The files of src/ta_engine a file imports, at its top or inside a function."""
    with open(path, encoding="utf-8") as source:
        tree = ast.parse(source.read(), path)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src.ta_engine"):
            if node.module == "src.ta_engine":
                found.update(f"src/ta_engine/{alias.name}.py" for alias in node.names)
            else:
                found.add(node.module.replace(".", "/") + ".py")
        elif isinstance(node, ast.Import):
            found.update(alias.name.replace(".", "/") + ".py" for alias in node.names if alias.name.startswith("src.ta_engine."))
    return found


def test_every_file_of_the_ta_engine_a_backtest_runs_counts_in_its_code():
    listed = set(_CODE)
    imported = {
        (module, os.path.relpath(path, ROOT))
        for part in _CODE for path in _python_files(part) for module in _ta_engine_modules(path)
    }
    assert {module for module, _ in imported}, "nothing of the TA engine found: the scan is wrong"
    assert sorted((module, by) for module, by in imported if module not in listed) == []


def test_the_live_engine_alone_is_not_part_of_a_runs_code():
    for live in ("src/ta_engine/engine.py", "src/ta_engine/monitoring.py", "src/ta_engine/sender.py", "src/ta_engine/__init__.py"):
        assert live not in _CODE and "src/ta_engine" not in _CODE


def test_a_change_to_what_a_replay_runs_changes_the_digest_and_one_to_the_live_loop_does_not(tmp_path):
    for part in (*_CODE, "src/ta_engine/engine.py"):
        source = os.path.join(ROOT, part)
        copy = tmp_path / part
        if os.path.isdir(source):
            shutil.copytree(source, copy, ignore=shutil.ignore_patterns("__pycache__", "*.mjs", "node_modules"))
        else:
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source, copy)
    before = code_digest(str(tmp_path))

    (tmp_path / "src/ta_engine/engine.py").write_text("# the live loop, changed\n", encoding="utf-8")
    assert code_digest(str(tmp_path)) == before
    with open(tmp_path / "src/ta_engine/replay.py", "a", encoding="utf-8") as replay:
        replay.write("\n# changed\n")
    assert code_digest(str(tmp_path)) != before


# Of WATA's code outside src/backtest and src/ta_engine, what a backtest imports without its result resting on it.
SERVES_ONLY = {
    "src/admin/context.py",       # the admin's settings, read for a run's folder
    "src/admin/data.py",          # the Data section; the files of prices it serves are read by src/price_history/files.py
    "src/admin/ta_scripts.py",    # the store a script's source is read from
    "src/capital_feed/settings.py",  # the form of an epic
    "src/price_history/process.py",  # a child process's priority and memory
    "src/database/postgres.py",   # the connection the recorded days are read through
    "src/saxo_openapi/async_client.py",  # the live services' client to Saxo, imported with them
    "src/saxo_openapi/contrib/orders/helper.py",
    "src/saxo_openapi/exceptions.py",
}


def _wata_modules(path: str) -> set[str]:
    """The files of src/ (but src/backtest and src/ta_engine) a file imports, at its top or inside a function."""
    with open(path, encoding="utf-8") as source:
        tree = ast.parse(source.read(), path)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src."):
            found.add(node.module.replace(".", "/") + ".py")
    return {module for module in found if not module.startswith(("src/backtest/", "src/ta_engine/"))}


def test_every_file_of_wata_a_backtest_rests_on_counts_in_its_code():
    listed = set(_CODE)
    imported = {
        (module, os.path.relpath(path, ROOT))
        for part in _CODE for path in _python_files(part) for module in _wata_modules(path)
        if os.path.exists(os.path.join(ROOT, module))
    }
    assert sorted((module, by) for module, by in imported if module not in listed and module not in SERVES_ONLY) == []


def test_the_built_runner_counts_when_it_is_there(tmp_path):
    (tmp_path / "src/backtest").mkdir(parents=True)
    (tmp_path / "src/backtest/a.py").write_text("x = 1\n", encoding="utf-8")
    before = code_digest(str(tmp_path))
    runner = tmp_path / "src/ta_engine/runner/runner.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// PineTS 0.12.0\n", encoding="utf-8")
    with_runner = code_digest(str(tmp_path))
    runner.write_text("// PineTS 0.13.0\n", encoding="utf-8")

    assert len({before, with_runner, code_digest(str(tmp_path))}) == 3
