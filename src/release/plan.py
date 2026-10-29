"""
What a release changes, container by container, and what of it may be
applied now: pure, over the facts src/release/host.py gathers (each
container's fingerprints before and after, its compose definition, the
guards' situation).

- unchanged: nothing it runs differs; it is not touched.
- change: its code, its libraries, its compose definition or its image's own
  settings differ; it is recreated on the release's image.
- optional: only Python, Node or the system's packages differ; applied when
  asked, or with the next change.
- infrastructure: Traefik, PostgreSQL or RabbitMQ changed; only a full restart
  applies it.
- absent: its compose profile is on but it has no container; ``watastart``
  makes one.

Containers that change because of the same file form a group, and a group
moves together: a newer src/admin/ta_rules.py in the admin than in the TA
engine would let one write rules the other refuses. A group goes now when
none of its members is held back (src/release/guards.py); otherwise it waits.
"""

from dataclasses import dataclass, field

from src.release.guards import ORDER, Situation, deploy_refusal

UNCHANGED, CHANGE, OPTIONAL, INFRASTRUCTURE, ABSENT = "unchanged", "change", "optional", "infrastructure", "absent"
NOW, PENDING, SKIP = "now", "pending", "skip"
WATA, BACKUP, INFRA = "wata", "backup", "infrastructure"
# Files listed in a reason before "and N more".
SHOWN_FILES = 12


@dataclass
class Container:
    """A container of the compose project as it runs, and what it would run on the newest release."""

    name: str
    role: str
    kind: str = WATA
    running: bool = True
    # The pinned image (``wata-base:0.59.1``) and the release it is.
    image: str | None = None
    version: str | None = None
    # Fingerprints: {"code", "files": {path: digest}, "libraries", "distributions": {name: version}, "platform"}.
    current: dict | None = None
    target: dict | None = None
    # The keys of its compose definition that changed; None when the files that made it are gone.
    compose: list[str] | None = field(default_factory=list)
    # The image's own settings (environment, command, user) differ from the newest's.
    image_settings: bool = False


@dataclass
class Step:
    container: str
    role: str
    kind: str
    when: str
    version: str | None = None
    target: str | None = None
    reasons: dict = field(default_factory=dict)
    held: str | None = None
    group: int | None = None
    # The members of its group that hold it back, when it waits for them.
    held_with: list[str] = field(default_factory=list)
    # Asked for alone although its group has other members: said, not refused.
    ahead_of: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "container": self.container, "role": self.role, "kind": self.kind, "when": self.when,
            "version": self.version, "target": self.target, "reasons": self.reasons, "held": self.held,
            "group": self.group, "held_with": self.held_with, "ahead_of": self.ahead_of,
        }


def changed_files(current: dict | None, target: dict | None) -> list[str]:
    """The files of either fingerprint whose content differs, or that one of them lacks."""
    before = (current or {}).get("files") or {}
    after = (target or {}).get("files") or {}
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def changed_libraries(current: dict | None, target: dict | None) -> list[dict]:
    before = (current or {}).get("distributions") or {}
    after = (target or {}).get("distributions") or {}
    return [{"name": name, "from": before.get(name), "to": after.get(name)}
            for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)]


def reasons_of(container: Container) -> tuple[str, dict]:
    """What differs between what ``container`` runs and the newest release, and the kind of change it makes."""
    if container.kind == INFRA:
        return (INFRASTRUCTURE, {"compose": container.compose}) if container.compose else (UNCHANGED, {})
    if not container.running:
        return ABSENT, {}
    current, target = container.current, container.target
    reasons: dict = {}
    if current is None or target is None:
        # What it runs cannot be read: changed, rather than left behind unseen.
        reasons["unknown"] = "what it runs could not be read"
    else:
        if current.get("code") != target.get("code"):
            files = changed_files(current, target)
            reasons["code"] = files or ["(its files differ)"]
        if current.get("libraries") != target.get("libraries"):
            reasons["libraries"] = changed_libraries(current, target)
    if container.compose is None:
        reasons["compose"] = ["(the files that made it are gone)"]
    elif container.compose:
        reasons["compose"] = list(container.compose)
    if container.image_settings:
        reasons["image"] = "the image's own settings (environment, command)"
    if reasons:
        return CHANGE, reasons
    if current is not None and target is not None and current.get("platform") != target.get("platform"):
        return OPTIONAL, {"platform": target.get("platform_said") or "Python, Node or the system's packages"}
    return UNCHANGED, {}


def _sort_key(step: Step) -> tuple[int, str]:
    """pg_backup1 first, then the order of the admin's restarts, the control service just before the admin."""
    if step.kind == INFRASTRUCTURE:
        return -2, step.container
    if step.role == "pg_backup":
        return -1, step.container
    if step.role == "service_control":
        return ORDER.index("admin") * 2 - 1, step.container
    if step.role in ORDER:
        return ORDER.index(step.role) * 2, step.container
    return len(ORDER) * 2, step.container


def groups_of(steps: list[Step]) -> None:
    """Link the changing containers that share a changed file; number each group."""
    changing = [step for step in steps if step.kind == CHANGE]
    parent = {step.container: step.container for step in changing}

    def root(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    owners: dict[str, str] = {}
    for step in changing:
        for path in step.reasons.get("code") or []:
            if path in owners:
                parent[root(step.container)] = root(owners[path])
            else:
                owners[path] = step.container
    numbers: dict[str, int] = {}
    for step in sorted(changing, key=_sort_key):
        step.group = numbers.setdefault(root(step.container), len(numbers) + 1)


def make_plan(containers: list[Container], situation: Situation, *, holds: dict[str, str] | None = None,
              target: str | None = None, wanted: list[str] | None = None, force: bool = False,
              platform: bool = False) -> list[Step]:
    """
    Each container's step, in the order an apply goes. ``wanted`` names the
    containers asked for (all that change when None); ``holds`` adds what the
    host saw holding one back (a dump being written, a restart under way).
    """
    holds = holds or {}
    steps = []
    for container in containers:
        kind, reasons = reasons_of(container)
        held = None
        if kind in (CHANGE, OPTIONAL):
            found = [deploy_refusal(container.role, situation) if container.role in ORDER else None,
                     holds.get(container.name)]
            held = "; ".join(reason for reason in found if reason) or None
        steps.append(Step(container=container.name, role=container.role, kind=kind, when=SKIP,
                          version=container.version, target=target if kind in (CHANGE, OPTIONAL) else None,
                          reasons=reasons, held=held))
    groups_of(steps)
    named = set(wanted) if wanted is not None else None
    if named is not None:
        unknown = named - {step.container for step in steps}
        if unknown:
            raise ValueError(f"Not containers of this deploy: {', '.join(sorted(unknown))}")
    by_group: dict[int, list[Step]] = {}
    for step in steps:
        if step.group is not None:
            by_group.setdefault(step.group, []).append(step)
    for step in steps:
        if step.kind == CHANGE:
            asked = named is None or step.container in named
        elif step.kind == OPTIONAL:
            asked = (named is not None and step.container in named) or (named is None and platform)
        else:
            continue
        if not asked:
            continue
        members = by_group.get(step.group, [step]) if step.group is not None else [step]
        if named is not None:
            step.ahead_of = sorted(member.container for member in members
                                   if member is not step and member.container not in named)
            # A group split on purpose: only the members asked for count.
            members = [member for member in members if member.container in named] or [step]
        if force:
            step.when = NOW
            continue
        holding = [member.container for member in members if member.held]
        if step.held:
            step.when = PENDING
        elif holding:
            step.when = PENDING
            step.held_with = holding
        else:
            step.when = NOW
    return sorted(steps, key=_sort_key)


def summary(steps: list[Step]) -> dict:
    """The plan in a few lists, for the playbook's output and the System page."""
    return {
        "now": [step.container for step in steps if step.when == NOW],
        "pending": [step.container for step in steps if step.when == PENDING],
        "optional": [step.container for step in steps if step.kind == OPTIONAL and step.when != NOW],
        "infrastructure": [step.container for step in steps if step.kind == INFRASTRUCTURE],
        "unchanged": [step.container for step in steps if step.kind == UNCHANGED],
    }


def said(step: Step) -> str:
    """One line for a person: what changes and whether it goes now."""
    parts = []
    reasons = step.reasons
    if "code" in reasons:
        files = reasons["code"]
        shown = ", ".join(files[:SHOWN_FILES]) + (f" and {len(files) - SHOWN_FILES} more" if len(files) > SHOWN_FILES else "")
        parts.append(f"code ({len(files)} file{'s' if len(files) != 1 else ''}: {shown})")
    if "libraries" in reasons:
        parts.append("libraries (" + ", ".join(
            f"{found['name']} {found['from'] or '-'} → {found['to'] or '-'}" for found in reasons["libraries"]) + ")")
    if "compose" in reasons:
        parts.append(f"compose ({', '.join(reasons['compose'])})")
    if "image" in reasons:
        parts.append(reasons["image"])
    if "unknown" in reasons:
        parts.append(reasons["unknown"])
    if "platform" in reasons:
        parts.append(f"platform only ({reasons['platform']}): optional")
    what = "; ".join(parts) or "nothing"
    if step.when == NOW:
        return f"{step.container}: {what} → now"
    if step.when == PENDING:
        why = step.held or f"waits with {', '.join(step.held_with)}"
        return f"{step.container}: {what} → pending ({why})"
    if step.kind == INFRASTRUCTURE:
        return f"{step.container}: {what} → only by a full restart (--full)"
    if step.kind == ABSENT:
        return f"{step.container}: no container (watastart makes it)"
    return f"{step.container}: {'unchanged' if step.kind == UNCHANGED else what}"
