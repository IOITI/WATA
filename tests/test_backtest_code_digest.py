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
