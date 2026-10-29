"""
An image of WATA checked before any container runs on it: what CI checks of
the image it builds, on the server's own config.json. The install runs it in
the new image, without network, before it names the release the newest
(``watadeploy check-image <version>``):

    docker run --rm --network none -v /app/wata/etc/config.json:/app/etc/config.json:ro \
        --entrypoint python wata-base:<version> -m src.release.check

CI runs it on the image it builds, with tests/test_config.json. It never
prints a value of the configuration: a refusal is said by its kind, its
message cleaned as a log line is.
"""

import logging
import os
import platform
import re
import subprocess
import sys

# Every service and tool loaded as it starts; the scheduler is left out: it runs its loop at import.
IMPORTS = (
    "src.web_server", "src.trader", "src.position_monitor", "src.watchlist_manager", "src.price_recorder",
    "src.capital_feed", "src.admin", "src.admin.app", "src.admin.agent", "src.mq_telegram", "src.saxo_authen",
    "src.price_history.dukascopy", "src.backtest.run", "src.ta_engine", "src.ta_engine.engine",
    "src.ta_engine.replay", "src.vault.migrate", "src.admin.vault", "src.service_control.app", "src.release.host",
    "schedule", "duckdb", "uvloop", "httptools", "anthropic",
)
_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)(\[[^\]]+\])?==([^\s;#]+)$")


def _normalised(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(lines) -> dict:
    """{name: version} of a requirements file or of ``pip freeze``, as tests/requirements_lock.py reads them."""
    found = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _REQUIREMENT.match(line)
        if match is None:
            raise ValueError(f"Not a package at one exact version: {line!r}")
        found[_normalised(match.group(1))] = match.group(3)
    return found


def differences(wanted: dict, installed: dict) -> list[str]:
    """What sets an environment apart from the file it was installed from (tests/requirements_lock.py's words)."""
    found = [f"{name}=={installed[name]} is installed and not listed" for name in sorted(set(installed) - set(wanted))]
    found += [f"{name}=={wanted[name]} is listed and not installed" for name in sorted(set(wanted) - set(installed))]
    found += [f"{name} is listed at {wanted[name]} and installed at {installed[name]}"
              for name in sorted(set(wanted) & set(installed)) if wanted[name] != installed[name]]
    return found


def _run(command: list[str], timeout: float = 300) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def _cleaned(text: str) -> str:
    """The last line of what a check said, cleaned as a log line is."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    try:
        from src.logging_helper.redaction import redact

        last = redact(last)
    except Exception:
        pass
    return last[:300]


class Checks:
    def __init__(self, root: str = "/app", config_path: str | None = None, out=print):
        self.root = root
        self.config_path = config_path or os.environ.get("WATA_CONFIG_PATH", "/app/etc/config.json")
        self.failed: list[str] = []
        self._out = out

    def said(self, ok: bool, what: str, detail: str = "") -> None:
        self._out(("ok      " if ok else "FAILED  ") + what + (f": {detail}" if detail else ""))
        if not ok:
            self.failed.append(what)

    def versions(self) -> None:
        node = _run(["node", "--version"])
        try:
            with open(os.path.join(self.root, "VERSION"), encoding="utf-8") as version_file:
                version = version_file.read().strip()
        except OSError:
            version = "unknown"
        self.said(node.returncode == 0, f"WATA {version}, Python {platform.python_version()}, Node "
                  f"{node.stdout.strip() if node.returncode == 0 else '(none)'}")

    def libraries(self) -> None:
        checked = _run([sys.executable, "-m", "pip", "check"])
        self.said(checked.returncode == 0, "pip check", _cleaned(checked.stdout + checked.stderr)
                  if checked.returncode else "")
        frozen = _run([sys.executable, "-m", "pip", "freeze"])
        try:
            with open(os.path.join(self.root, "requirements.txt"), encoding="utf-8") as wanted_file:
                found = differences(pins(wanted_file), pins(frozen.stdout.splitlines()))
        except (OSError, ValueError) as e:
            found = [str(e)]
        self.said(not found, "the libraries are requirements.txt's and no other", "; ".join(found[:5]))

    def compiles(self) -> None:
        done = _run([sys.executable, "-W", "error::SyntaxWarning", "-m", "compileall", "-q",
                     os.path.join(self.root, "src")])
        self.said(done.returncode == 0, "every file compiles", _cleaned(done.stdout + done.stderr))

    def imports(self) -> None:
        program = "import importlib, sys\nfor name in sys.argv[1:]:\n    importlib.import_module(name)\n"
        done = _run([sys.executable, "-c", program, *IMPORTS])
        self.said(done.returncode == 0, f"every service loads ({len(IMPORTS)} modules)",
                  _cleaned(done.stderr) if done.returncode else "")

    def configuration(self) -> None:
        """The services' own validators, on the configuration as it is: the core one, and each section present."""
        logging.disable(logging.CRITICAL)
        try:
            from src.configuration import ConfigurationManager

            manager = ConfigurationManager(self.config_path)
        except Exception as e:
            self.said(False, "config.json is accepted by ConfigurationManager", f"{type(e).__name__}: {_cleaned(str(e))}")
            return
        self.said(True, "config.json is accepted by ConfigurationManager")
        sections = (
            ("src.admin.context", "AdminSettings", "admin"),
            ("src.price_recorder.settings", "RecorderSettings", "trade.config.price_recorder"),
            ("src.capital_feed.settings", "CapitalSettings", "trade.config.capital_feed"),
            ("src.ta_engine.settings", "TaEngineSettings", "trade.config.ta_engine"),
        )
        for module, name, section in sections:
            if manager.get_config_value(section) is None:
                continue
            try:
                getattr(__import__(module, fromlist=[name]), name).from_config(manager)
            except Exception as e:
                self.said(False, f"{section} is accepted by {name}", f"{type(e).__name__}: {_cleaned(str(e))}")
            else:
                self.said(True, f"{section} is accepted by {name}")

    def runner(self) -> None:
        if not os.path.isfile(os.path.join(self.root, "src", "ta_engine", "runner", "runner.mjs")):
            self.said(False, "the Pine runner is in the image", "src/ta_engine/runner/runner.mjs is missing")
            return
        done = _run([sys.executable, "-m", "src.ta_engine.runner"], timeout=120)
        self.said(done.returncode == 0, "the Pine runner runs, confined", _cleaned(done.stdout + done.stderr))

    def digests(self) -> None:
        try:
            from src.backtest.figures import code_digest

            self.said(True, f"backtests' code digest {code_digest(self.root)}")
        except Exception as e:
            self.said(False, "backtests' code digest", f"{type(e).__name__}: {_cleaned(str(e))}")

    def all(self) -> int:
        for check in (self.versions, self.libraries, self.compiles, self.imports, self.configuration, self.runner,
                      self.digests):
            try:
                check()
            except Exception as e:
                self.said(False, check.__name__, f"{type(e).__name__}: {_cleaned(str(e))}")
        self._out(f"{len(self.failed)} check(s) failed" if self.failed else "Every check passed")
        return 1 if self.failed else 0


if __name__ == "__main__":
    sys.exit(Checks().all())
