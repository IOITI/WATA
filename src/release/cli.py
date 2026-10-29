"""
watadeploy: what runs on the server, what a release changes, and applying it
container by container.

    watadeploy status                     each container, its release, and what waits
    watadeploy plan [--services a,b]      what the newest release changes, and what may go now
    watadeploy apply [--services a,b]     recreate what changed and may go now (--force: whatever holds it)
    watadeploy rollback [container ...]   the last apply's containers back on their images of before
    watadeploy full                       every container stopped and started again (infrastructure changes)
    watadeploy check-config               does every image in use still start on config.json?
    watadeploy check-image VERSION        the checks of src/release/check.py, in that release's image
    watadeploy serve-requests             the admin's requests (started by watadeploy-requests.path)
    watadeploy bootstrap                  pins for a server that has none (the install runs it)
    watadeploy prune [--keep N]           older releases' folders and images

Run as root on the server (the wrapper /usr/local/bin/watadeploy sets
PYTHONPATH to the newest release's folder). Exit codes: 0 done, 1 refused or
impossible, 3 an apply stopped on a container that did not come back or was
not seen at work.
"""

import argparse
import json
import sys

from src.release.host import (
    FAILED,
    NOT_READY,
    ROLLED_BACK,
    DeployError,
    Host,
    python_is_enough,
)
from src.release.plan import said, summary


def _names(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [name.strip() for name in value.split(",") if name.strip()]


def _outcome(record: dict) -> int:
    states = {step.get("state") for step in record.get("steps") or []}
    return 3 if states & {FAILED, ROLLED_BACK, NOT_READY} else 0


def _said_record(host: Host, record: dict) -> None:
    for step in record.get("steps") or []:
        detail = f": {step['detail']}" if step.get("detail") else ""
        host._out(f"  {step['container']}: {step['state']}{detail}")


def main(arguments: list[str], host: Host | None = None) -> int:
    parser = argparse.ArgumentParser(prog="watadeploy", description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="print the outcome as JSON as well")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    for name in ("plan", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--services", help="containers, comma-separated (all that changed when left out)")
        command.add_argument("--platform", action="store_true", help="also those whose Python or system alone changed")
        command.add_argument("--force", action="store_true", help="whatever holds them back (said in the history)")
        if name == "apply":
            command.add_argument("--no-dump", action="store_true", help="no dump of the database before")
            command.add_argument("--by", default="watadeploy", help="who asks, for the history")
    rollback = commands.add_parser("rollback")
    rollback.add_argument("containers", nargs="*")
    rollback.add_argument("--force", action="store_true")
    full = commands.add_parser("full")
    full.add_argument("--force", action="store_true")
    commands.add_parser("check-config")
    check = commands.add_parser("check-image")
    check.add_argument("version")
    commands.add_parser("serve-requests")
    commands.add_parser("bootstrap")
    prune = commands.add_parser("prune")
    prune.add_argument("--keep", type=int, default=3)
    options = parser.parse_args(arguments)

    if not python_is_enough():
        print("watadeploy needs Python 3.10 or newer on the server", file=sys.stderr)
        return 1
    host = host or Host()
    try:
        if options.command in ("status", "plan"):
            wanted = _names(getattr(options, "services", None))
            facts, steps = host.plan(wanted, force=getattr(options, "force", False),
                                     platform=getattr(options, "platform", False))
            host._out(f"Newest release installed: {facts.newest}")
            for step in steps:
                host._out("  " + said(step))
            status = host.write_status(facts=facts, steps=steps)
            if options.json:
                print(json.dumps({"plan": summary(steps), "containers": status.get("containers")}, indent=1))
            return 0
        if options.command == "apply":
            record = host.apply(_names(options.services), force=options.force, platform=options.platform,
                                by=options.by, dump=not options.no_dump)
            _said_record(host, record)
            if options.json:
                print(json.dumps(record, indent=1, sort_keys=True))
            return _outcome(record)
        if options.command == "rollback":
            record = host.rollback(options.containers or None, force=options.force)
            _said_record(host, record)
            return _outcome(record)
        if options.command == "full":
            record = host.full(force=options.force)
            _said_record(host, record)
            return _outcome(record)
        if options.command == "check-config":
            refused = 0
            for found in host.check_config():
                state = "accepts it" if found["accepted"] else f"REFUSES it: {found['detail']}"
                users = ", ".join(found["containers"]) or "the newest release"
                host._out(f"{found['image']} ({users}) {state}")
                refused += 0 if found["accepted"] else 1
            return 1 if refused else 0
        if options.command == "check-image":
            return host.check_image(options.version)
        if options.command == "serve-requests":
            served = host.serve_requests()
            host._out(f"{served} request(s) served")
            return 0
        if options.command == "bootstrap":
            for name, image in sorted(host.bootstrap().items()):
                host._out(f"{name}={image}")
            return 0
        if options.command == "prune":
            removed = host.prune(options.keep)
            host._out("Removed: " + (", ".join(removed) or "nothing"))
            return 0
    except (DeployError, ValueError) as e:
        print(f"watadeploy: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
