"""
Is an environment exactly what ``requirements.txt`` names?

    pip freeze > installed.txt
    python tests/requirements_lock.py requirements.txt installed.txt

Exits with 1 and says what differs when it is not. It only needs Python: CI
runs it where no library is installed, on what an image answers to
``pip freeze``.
"""

import os
import re
import sys

REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)(\[[^\]]+\])?==([^\s;#]+)$")


def normalised(name: str) -> str:
    """A package's name as pip compares them: ``pydantic_core`` is ``pydantic-core``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(lines) -> dict:
    """{name: version} of a requirements file, refusing a line that leaves a version open."""
    found = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = REQUIREMENT.match(line)
        if match is None:
            raise ValueError(f"Not a package at one exact version: {line!r}")
        name = normalised(match.group(1))
        if name in found:
            raise ValueError(f"Listed twice: {name}")
        found[name] = match.group(3)
    return found


def differences(wanted: dict, installed: dict) -> list:
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


def read(path: str) -> dict:
    with open(path, encoding="utf-8") as lines:
        return pins(lines)


def main(arguments: list) -> int:
    wanted_path, installed_path = arguments
    try:
        problems = differences(read(wanted_path), read(installed_path))
    except ValueError as e:
        problems = [str(e)]
    for problem in problems:
        # Shown beside the job on GitHub, where its log is not always at hand.
        print(f"::error::{problem}" if os.environ.get("GITHUB_ACTIONS") else problem)
    print(f"{installed_path}: {'NOT ' if problems else ''}the environment of {wanted_path}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
