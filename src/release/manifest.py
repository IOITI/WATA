"""
What an image of WATA runs, container by container: the fingerprints two
releases are compared on, so that a deploy recreates only the containers
whose code, libraries or platform changed.

It runs inside the image it describes, fed on stdin to the image's own Python
(``docker run --rm -i --network none --entrypoint python <image> - < manifest.py``),
so that it also reads an image built before it existed. It imports nothing
of WATA and only reads files.

A container's code is every file its entry can load:

- the imports of each module reached, at module level and inside functions,
  with the packages above them (Python runs their ``__init__.py``);
- another service's entry file reached by an import counts for its module
  level alone: its ``main()`` and ``__main__`` run only in its own container
  (price_recorder's ``main()`` would otherwise bring the trader into the TA
  engine);
- the modules named in a string (``python -m src.backtest.run``), run as
  ``__main__``;
- the modules a package loads by name (``DYNAMIC``), the files the modules
  read (``DATA``) and ``src/start_python_script.sh``.

The server's commands (watasaxoauth, watawebtoken, wataprices, the admin's
CLI...) are not a container's: ``watatool`` runs each in a container of its
own, on the newest release's image.

Python is compared as its syntax tree: a change of comments or layout alone
changes nothing. VERSION is left out: it changes with every release.
tests/test_release_manifest.py holds the tables to the code.
"""

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys

MANIFEST_VERSION = 1

# Each container of the compose file that runs WATA's image, and its role (WATA_APP_ROLE).
CONTAINERS = {
    "web_server1": "web_server",
    "telegram1": "telegram",
    "scheduler1": "scheduler",
    "trader1": "trader",
    "position_monitor1": "position_monitor",
    "watchlist_manager1": "watchlist_manager",
    "admin1": "admin",
    "service_control1": "service_control",
    "price_recorder1": "price_recorder",
    "capital_feed1": "capital_feed",
    "ta_engine1": "ta_engine",
}
# The module each role runs (src/start_python_script.sh).
ENTRIES = {
    "web_server": "src.web_server",
    "trader": "src.trader",
    "position_monitor": "src.position_monitor",
    "watchlist_manager": "src.watchlist_manager",
    "scheduler": "src.scheduler",
    "telegram": "src.mq_telegram",
    "admin": "src.admin",
    "price_recorder": "src.price_recorder",
    "capital_feed": "src.capital_feed",
    "ta_engine": "src.ta_engine",
    "service_control": "src.service_control",
}
# The modules a package loads by a name it builds: no import says them.
DYNAMIC = {
    "src.saxo_openapi.definitions": (
        "src.saxo_openapi.definitions.accounthistory",
        "src.saxo_openapi.definitions.activities",
        "src.saxo_openapi.definitions.orders",
        "src.saxo_openapi.definitions.reportformats",
    ),
}
# The files the code reads, by the module that reads them; a folder stands for every file in it.
# runner/, checker/ and static/ are built by package.sh: they are in the image, not in git.
DATA = {
    "src/trade/data/macro_calendar.csv": ("src.trade.macro_calendar",),
    "src/trade/data/us_market_calendar.csv": ("src.trade.market_calendar",),
    "src/ta_engine/runner": ("src.ta_engine.runner",),
    "src/ta_engine/checker": ("src.research.study",),
    "src/admin/static": ("src.admin.app",),
}
# Read by every container: how it is started.
EVERY_CONTAINER = ("src/start_python_script.sh",)
# Kept under src/ and read by nothing.
UNREAD = ("src/saxo_authen/README.md", "src/saxo_authen/img.png")

_DIGEST_LENGTH = 16
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def digest(*parts: bytes) -> str:
    hashed = hashlib.sha256()
    for part in parts:
        hashed.update(part)
        hashed.update(b"\0")
    return hashed.hexdigest()[:_DIGEST_LENGTH]


def _is_main_guard(node: ast.AST) -> bool:
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")


def modules_of(root: str) -> dict[str, str]:
    """Every module under ``root``/src, by its dotted name, to its file relative to ``root``."""
    found = {}
    for folder, folders, files in os.walk(os.path.join(root, "src")):
        folders[:] = sorted(name for name in folders if name != "__pycache__")
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            relative = os.path.relpath(os.path.join(folder, name), root)
            module = relative[:-3].replace(os.sep, ".")
            found[module[: -len(".__init__")] if module.endswith(".__init__") else module] = relative
    return found


class Source:
    """The modules of a tree, their imports and their fingerprints, each read once."""

    def __init__(self, root: str):
        self.root = root
        self.modules = modules_of(root)
        self._trees: dict[str, ast.Module | None] = {}
        self._edges: dict[tuple[str, bool], tuple[frozenset, frozenset, frozenset]] = {}
        self._files: dict[str, str] = {}

    def _tree(self, module: str) -> ast.Module | None:
        if module not in self._trees:
            try:
                with open(os.path.join(self.root, self.modules[module]), encoding="utf-8") as source:
                    self._trees[module] = ast.parse(source.read())
            except (OSError, SyntaxError, ValueError):
                self._trees[module] = None
        return self._trees[module]

    def file_digest(self, path: str) -> str:
        """A file's fingerprint: its syntax tree for Python (comments and layout left out), its bytes otherwise."""
        if path not in self._files:
            full = os.path.join(self.root, path)
            try:
                with open(full, "rb") as data:
                    content = data.read()
            except OSError:
                self._files[path] = "absent"
                return self._files[path]
            if path.endswith(".py"):
                try:
                    content = ast.dump(ast.parse(content.decode("utf-8"))).encode("utf-8")
                except (SyntaxError, ValueError, UnicodeDecodeError):
                    pass
            self._files[path] = digest(content)
        return self._files[path]

    def edges(self, module: str, as_main: bool) -> tuple[frozenset, frozenset, frozenset]:
        """
        What ``module`` loads: (modules of src, modules of src run as ``__main__``,
        top-level names of other packages). An entry file reached from another
        service gives its module level only.
        """
        key = (module, as_main)
        if key in self._edges:
            return self._edges[key]
        tree = self._tree(module)
        if tree is None:
            self._edges[key] = (frozenset(), frozenset(), frozenset())
            return self._edges[key]
        shallow = module in ENTRIES.values() and not as_main
        package = module if self.modules[module].endswith("__init__.py") else module.rpartition(".")[0]
        named, run, outside = set(), set(), set()

        def resolve(node: ast.AST) -> list[str]:
            if isinstance(node, ast.Import):
                return [alias.name for alias in node.names]
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                parts = parts[: len(parts) - (node.level - 1)]
                base = ".".join(parts + ([base] if base else []))
            return [base] + [f"{base}.{alias.name}" for alias in node.names]

        def visit(node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                if node is tree and _is_main_guard(child) and not as_main:
                    continue
                if shallow and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    for name in resolve(child):
                        if name == "src" or name.startswith("src."):
                            named.add(name)
                        elif name and not (isinstance(child, ast.ImportFrom) and child.level):
                            outside.add(name.split(".")[0])
                elif isinstance(child, ast.Constant) and isinstance(child.value, str):
                    if child.value.startswith("src.") and child.value in self.modules:
                        run.add(child.value)
                visit(child)

        visit(tree)
        known = set()
        for name in named:
            parts = name.split(".")
            known.update(".".join(parts[:i]) for i in range(1, len(parts) + 1) if ".".join(parts[:i]) in self.modules)
        known.update(DYNAMIC.get(module, ()))
        self._edges[key] = (frozenset(known), frozenset(run), frozenset(outside))
        return self._edges[key]

    def closure(self, role: str) -> tuple[set[str], set[str]]:
        """The modules a role's container can load, and the top-level names of the other packages they import."""
        todo = [(ENTRIES[role], True)]
        seen: set[tuple[str, bool]] = set()
        outside: set[str] = set()
        while todo:
            module, as_main = todo.pop()
            if (module, as_main) in seen or module not in self.modules:
                continue
            seen.add((module, as_main))
            parts = module.split(".")
            todo.extend((".".join(parts[:i]), False) for i in range(1, len(parts)) if ".".join(parts[:i]) in self.modules)
            named, run, names = self.edges(module, as_main)
            todo.extend((name, False) for name in named)
            todo.extend((name, True) for name in run)
            outside.update(names)
        return {module for module, _ in seen}, outside

    def data_files(self, modules: set[str]) -> list[str]:
        """The files of ``DATA`` the given modules read, each file of a folder named."""
        found = []
        for path, readers in DATA.items():
            if not modules.intersection(readers):
                continue
            full = os.path.join(self.root, path)
            if os.path.isdir(full):
                for folder, folders, files in os.walk(full):
                    folders[:] = sorted(name for name in folders if name != "__pycache__")
                    found.extend(os.path.relpath(os.path.join(folder, name), self.root)
                                 for name in sorted(files) if not name.startswith("."))
            else:
                found.append(path)
        return found


def _normalised(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def libraries(names: set[str]) -> dict[str, str]:
    """
    The installed distributions behind the top-level ``names`` a container
    imports, and what they require in turn (extras and markers included: more
    rather than fewer), each at its version.
    """
    standard = set(getattr(sys, "stdlib_module_names", ())) | set(sys.builtin_module_names) | {"__future__"}
    by_name = importlib.metadata.packages_distributions()
    todo = []
    for name in names - standard:
        todo.extend(by_name.get(name, ()))
    found: dict[str, str] = {}
    while todo:
        distribution = _normalised(todo.pop())
        if distribution in found:
            continue
        try:
            found[distribution] = importlib.metadata.version(distribution)
            requires = importlib.metadata.requires(distribution) or []
        except importlib.metadata.PackageNotFoundError:
            continue
        for requirement in requires:
            matched = _REQUIREMENT_NAME.match(requirement)
            if matched:
                todo.append(matched.group(1))
    return dict(sorted(found.items()))


def _run(command: list[str]) -> str | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def platform_of() -> dict:
    """What every container shares under its code: Python, Node and the system's packages."""
    node = (_run(["node", "--version"]) or "").strip() or None
    packages = _run(["dpkg-query", "-W", "-f", "${Package}=${Version}\\n"])
    system = digest("\n".join(sorted((packages or "").splitlines())).encode()) if packages else None
    found = {"python": platform.python_version(), "python_build": sys.version, "node": node, "system": system}
    found["digest"] = digest(json.dumps(found, sort_keys=True).encode())
    return found


def read_version(root: str) -> str | None:
    try:
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as version:
            return version.read().strip() or None
    except OSError:
        return None


def manifest(root: str, with_libraries: bool = True, with_platform: bool = True) -> dict:
    """The fingerprints of the tree at ``root`` (``/app`` in the image), container by container."""
    source = Source(root)
    files: dict[str, str] = {}
    containers = {}
    for container, role in CONTAINERS.items():
        modules, outside = source.closure(role)
        paths = sorted({source.modules[module] for module in modules}
                       | set(source.data_files(modules)) | set(EVERY_CONTAINER))
        for path in paths:
            files[path] = source.file_digest(path)
        entry = {
            "role": role,
            "files": paths,
            "code": digest(*(f"{path}={files[path]}".encode() for path in paths)),
            "imports": sorted(outside),
        }
        if with_libraries:
            found = libraries(outside)
            entry["distributions"] = found
            entry["libraries"] = digest(*(f"{name}=={version}".encode() for name, version in found.items()))
        containers[container] = entry
    return {
        "manifest": MANIFEST_VERSION,
        "version": read_version(root),
        "platform": platform_of() if with_platform else None,
        "files": dict(sorted(files.items())),
        "containers": containers,
    }


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description="What an image of WATA runs, container by container.")
    parser.add_argument("--root", default=os.getcwd(), help="the folder holding src/ (/app in the image)")
    parser.add_argument("--no-libraries", action="store_true", help="leave the installed distributions out")
    parser.add_argument("--no-platform", action="store_true", help="leave Python, Node and the system out")
    options = parser.parse_args(arguments)
    json.dump(manifest(options.root, not options.no_libraries, not options.no_platform), sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
