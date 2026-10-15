"""
``requirements.txt`` is the whole environment: every package installed is a
line of it, at that version, and every line is installed.

What compares the two is ``tests/requirements_lock.py``, tested here; CI runs
it on each Python it tests and on each image it builds.
"""

import ast
import pathlib
import subprocess
import sys
from importlib import metadata

import pytest

from requirements_lock import differences, main, normalised, pins, read


# ── the comparison ──


def test_an_environment_that_is_the_file_has_no_difference():
    wanted = pins(["# a comment", "", "pydantic_core==2.41.5", "httpx[http2]==0.27.0", "PyYAML==6.0.3"])
    installed = pins(["pydantic-core==2.41.5", "httpx==0.27.0", "pyyaml==6.0.3"])

    assert differences(wanted, installed) == []


def test_a_package_pip_chose_by_itself_is_reported():
    assert differences(pins(["httpx==0.27.0"]), pins(["httpx==0.27.0", "h2==4.4.1"])) == [
        "h2==4.4.1 is installed and not listed: pip chose its version"
    ]


def test_a_line_nothing_installs_and_a_version_that_differs_are_reported():
    found = differences(pins(["httpx==0.27.0", "selenium==4.20.0"]), pins(["httpx==0.28.1"]))

    assert found == [
        "selenium==4.20.0 is listed and not installed: nothing needs it here",
        "httpx is listed at 0.27.0 and installed at 0.28.1",
    ]


@pytest.mark.parametrize("line", ["websocket-client~=1.8.0", "requests>=2", "uvloop", "-r other.txt"])
def test_a_line_that_leaves_a_version_open_is_refused(line):
    with pytest.raises(ValueError, match="one exact version"):
        pins([line])


def test_a_package_listed_twice_is_refused():
    with pytest.raises(ValueError, match="Listed twice: typing-extensions"):
        pins(["typing_extensions==4.14.1", "typing-extensions==4.12.2"])


# ── as a command, the way CI runs it ──


def _files(tmp_path, wanted: str, installed: str) -> list[str]:
    (tmp_path / "requirements.txt").write_text(wanted)
    (tmp_path / "installed.txt").write_text(installed)
    return [str(tmp_path / "requirements.txt"), str(tmp_path / "installed.txt")]


def test_the_command_ends_with_0_when_the_environment_is_the_file(tmp_path, capsys):
    assert main(_files(tmp_path, "httpx[http2]==0.27.0\n", "httpx==0.27.0\n")) == 0
    assert "installed.txt: the environment of" in capsys.readouterr().out


def test_the_command_ends_with_1_and_says_what_differs(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    assert main(_files(tmp_path, "httpx==0.27.0\n", "httpx==0.27.0\nh2==4.4.1\n")) == 1

    said = capsys.readouterr().out
    assert "h2==4.4.1 is installed and not listed" in said
    assert "NOT the environment of" in said


def test_on_github_what_differs_is_shown_beside_the_job(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    main(_files(tmp_path, "httpx==0.27.0\n", "httpx==0.28.1\n"))

    assert "::error::httpx is listed at 0.27.0 and installed at 0.28.1" in capsys.readouterr().out


def test_a_file_that_leaves_a_version_open_fails_the_command_too(tmp_path, capsys):
    assert main(_files(tmp_path, "httpx>=0.27\n", "httpx==0.27.0\n")) == 1
    assert "one exact version" in capsys.readouterr().out


def test_the_command_needs_nothing_but_python(tmp_path):
    """CI runs it on a machine where pytest is not installed: no library may be needed to load it."""
    files = _files(tmp_path, "httpx==0.27.0\n", "httpx==0.27.0\n")

    # -S: without the installed packages, as on a machine that has none.
    ran = subprocess.run(
        [sys.executable, "-S", "-I", "tests/requirements_lock.py", *files], capture_output=True, text=True,
    )

    assert (ran.returncode, ran.stderr) == (0, "")


# ── the file itself ──


def test_every_line_of_the_file_is_one_package_at_one_version():
    assert len(read("requirements.txt")) > 30


def _imported_by(root: str) -> set[str]:
    """The modules from outside the standard library and WATA that the files under ``root`` import."""
    modules = set()
    for path in pathlib.Path(root).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    # ``requirements_lock`` is the file beside this one.
    return modules - set(sys.stdlib_module_names) - {"src", "tests", "requirements_lock"}


@pytest.mark.parametrize("root", ["src", "tests"])
def test_what_the_code_imports_is_listed(root):
    listed = read("requirements.txt")
    provided_by = metadata.packages_distributions()

    # A module is looked up where it is installed: asyncpg, say, is not on every development machine.
    not_listed = {
        module: sorted(normalised(package) for package in provided_by[module])
        for module in _imported_by(root)
        if module in provided_by and not any(normalised(package) in listed for package in provided_by[module])
    }

    assert not_listed == {}
