"""
``requirements.txt`` is the whole environment: every package installed is a
line of it, at that version, and every line is installed.

The comparison is a function, tested here. CI runs it for real on each Python
it tests and inside each image it builds:

    pip freeze > installed.txt
    python tests/test_requirements.py requirements.txt installed.txt
"""

import ast
import pathlib
import re
import sys
from importlib import metadata

import pytest

REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)(\[[^\]]+\])?==([^\s;#]+)$")


def _name(name: str) -> str:
    """A package's name as pip compares them: ``pydantic_core`` is ``pydantic-core``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(lines) -> dict[str, str]:
    """{name: version} of a requirements file, refusing a line that leaves a version open."""
    found = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = REQUIREMENT.match(line)
        if match is None:
            raise ValueError(f"Not a package at one exact version: {line!r}")
        name = _name(match.group(1))
        if name in found:
            raise ValueError(f"Listed twice: {name}")
        found[name] = match.group(3)
    return found


def differences(wanted: dict[str, str], installed: dict[str, str]) -> list[str]:
    """What sets an environment apart from the file it was installed from; empty when nothing does."""
    found = []
    for name in sorted(set(installed) - set(wanted)):
        found.append(f"{name}=={installed[name]} is installed and not listed: pip chose its version")
    for name in sorted(set(wanted) - set(installed)):
        found.append(f"{name}=={wanted[name]} is listed and not installed: nothing needs it here")
    for name in sorted(set(wanted) & set(installed)):
        if wanted[name] != installed[name]:
            found.append(f"{name} is listed at {wanted[name]} and installed at {installed[name]}")
    return found


def _read(path: str) -> dict[str, str]:
    with open(path, encoding="utf-8") as lines:
        return pins(lines)


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


# ── the file itself ──


def test_every_line_of_the_file_is_one_package_at_one_version():
    assert len(_read("requirements.txt")) > 30


def _imported_by(root: str) -> set[str]:
    """The modules from outside the standard library and WATA that the files under ``root`` import."""
    modules = set()
    for path in pathlib.Path(root).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return modules - set(sys.stdlib_module_names) - {"src", "tests"}


@pytest.mark.parametrize("root", ["src", "tests"])
def test_what_the_code_imports_is_listed(root):
    listed = _read("requirements.txt")
    provided_by = metadata.packages_distributions()

    # A module is looked up where it is installed: asyncpg, say, is not on every development machine.
    not_listed = {
        module: sorted(_name(package) for package in provided_by[module])
        for module in _imported_by(root)
        if module in provided_by and not any(_name(package) in listed for package in provided_by[module])
    }

    assert not_listed == {}


if __name__ == "__main__":
    problems = differences(_read(sys.argv[1]), _read(sys.argv[2]))
    for problem in problems:
        print(problem)
    print(f"{sys.argv[2]}: {'NOT ' if problems else ''}the environment of {sys.argv[1]}")
    sys.exit(1 if problems else 0)
