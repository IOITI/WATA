"""Shared by every test file."""

import os


def pytest_runtest_logreport(report):
    """On GitHub, name each test that fails beside its job: the log is only shown to who is signed in."""
    if not report.failed or not os.environ.get("GITHUB_ACTIONS"):
        return
    # The lines pytest marks with "E": what was expected and what came.
    reasons = [line[1:].strip() for line in report.longreprtext.splitlines() if line.startswith("E ") and line[1:].strip()]
    message = " | ".join(reasons[:4]) or f"failed during {report.when}"
    # A workflow command is one line, and reads % and line ends as its own.
    for raw, escaped in (("%", "%25"), ("\r", "%0D"), ("\n", "%0A")):
        message = message.replace(raw, escaped)
    print(f"\n::error title={report.nodeid} ({report.when})::{message[:900]}")
