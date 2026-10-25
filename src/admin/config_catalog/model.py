"""
What a parameter of config.json is, for the Configuration page: its place in
the file, what it does, which services read it (and must start again for a
change to apply), what else it changes, and whether the page may change it.

Data only. The validators (ConfigurationManager, the sections' own) remain the
authority on what a value may be; tests/test_config_catalog.py holds the
catalog to them and to the files, so that no key is left undescribed.

Paths are dotted. A key of a trading rule is written
``trade.rules[<rule_type>].rule_config.<key>``, since the services find a
rule by its type, never by its place in the list.
"""

from dataclasses import dataclass, field, fields, replace

# Who can change a parameter.
EDITABLE = "editable"
# Changed on the server only: ``reason`` says why, ``how`` what to do there.
SERVER = "server"
# A credential: kept apart from the parameters, and never shown.
CREDENTIAL = "credential"
# Nothing reads it any more: ``reason`` says why it is still there.
UNUSED = "unused"
ACCESSES = (EDITABLE, SERVER, CREDENTIAL, UNUSED)

# What a change can do.
TRADING = "trading"  # what is bought or sold, when, at what size
SERVICE = "service"  # how a service runs, what it records or says
NONE = "none"  # nothing the services do changes
RISKS = (TRADING, SERVICE, NONE)

KINDS = (
    "int", "number", "bool", "text", "enum", "time", "timezone",
    "dates", "map", "rows", "strings", "numbers", "rule", "json", "secret",
)


@dataclass(frozen=True)
class Service:
    """A WATA service, by the id its health entry has (src/admin/health.py)."""

    id: str
    name: str
    container: str
    # The compose profile without which it is not started; None: always.
    profile: str | None = None


@dataclass(frozen=True)
class Consumer:
    """What reads a parameter without being a service: a page, a run, an export."""

    id: str
    name: str
    # Whether it reads the file again each time (True) or what the admin read at its start (False).
    live: bool = True


# The menu's sections, in its order (admin_ui/src/components/navigation.ts): a group is listed under one.
SECTIONS = ("Trading", "Analysis", "Data", "Technical Analysis", "Operations")


@dataclass(frozen=True)
class Group:
    id: str
    title: str
    summary: str
    # The section of the menu its parameters belong with.
    section: str = "Operations"


@dataclass(frozen=True)
class Block:
    """Parameters of a group that go together, under one heading of its page."""

    group: str
    id: str
    title: str
    # Each a parameter's path, or the start of the paths it holds (up to a dot or a bracket).
    paths: tuple[str, ...] = ()
    summary: str = ""

    def holds(self, path: str) -> bool:
        return any(path == start or path.startswith((f"{start}.", f"{start}[")) for start in self.paths)


@dataclass(frozen=True)
class Column:
    """A field of each row of a ``rows`` parameter."""

    key: str
    label: str
    kind: str
    help: str = ""
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    optional: bool = False


@dataclass(frozen=True)
class Param:
    path: str
    group: str
    label: str
    kind: str
    help: str
    access: str = EDITABLE
    # Why it is not editable here, and how it is changed instead.
    reason: str | None = None
    how: str | None = None
    # The services that read it when they start: a change applies at their next start.
    readers: tuple[str, ...] = ()
    # The pages, runs and exports that read it (Consumer ids).
    consumers: tuple[str, ...] = ()
    risk: str = SERVICE
    # What it changes, one sentence each.
    effects: tuple[str, ...] = ()
    # Other parameters it works with.
    related: tuple[str, ...] = ()
    # What is easy to get wrong about it.
    caveats: tuple[str, ...] = ()
    # What else it takes to have an effect (a compose profile, another switch).
    also_needs: tuple[str, ...] = ()
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    # May be absent from the file; ``default`` is then what the services use (None: off, nothing).
    optional: bool = False
    default: object = None
    # A ``rows`` parameter's fields.
    columns: tuple[Column, ...] = ()
    # A ``map`` parameter's names of its keys and values.
    key_label: str | None = None
    value_label: str | None = None
    # A ``rule`` parameter: the rule_config it is added with.
    rule_default: dict | None = None
    # The block of its group it is shown in (Catalog sets it from the blocks' paths).
    block: str | None = None

    def as_dict(self) -> dict:
        data = {item.name: getattr(self, item.name) for item in fields(self)}
        data["columns"] = [column.__dict__ for column in self.columns]
        return {key: list(value) if isinstance(value, tuple) else value for key, value in data.items()}


# The block every group ends with when it holds keys nothing reads.
UNUSED_BLOCK = "unused"


@dataclass(frozen=True)
class Catalog:
    services: tuple[Service, ...]
    consumers: tuple[Consumer, ...]
    groups: tuple[Group, ...]
    params: tuple[Param, ...]
    blocks: tuple[Block, ...] = ()
    _by_path: dict = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self):
        unused = tuple(
            Block(group.id, UNUSED_BLOCK, "Read by nothing",
                  summary="Still in the file, read by no service: left by an earlier WATA. Nothing changes if removed.")
            for group in self.groups if any(param.group == group.id and param.access == UNUSED for param in self.params)
        )
        blocks = self.blocks + unused
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(self, "params", tuple(self._placed(param, blocks) for param in self.params))
        self._by_path.update({param.path: param for param in self.params})

    @staticmethod
    def _placed(param: "Param", blocks: tuple[Block, ...]) -> "Param":
        """The parameter with its block: the unused one for a key nothing reads, else the first holding its path."""
        if param.access == UNUSED and any(block.group == param.group and block.id == UNUSED_BLOCK for block in blocks):
            return replace(param, block=UNUSED_BLOCK)
        found = next((block.id for block in blocks if block.group == param.group and block.holds(param.path)), None)
        return replace(param, block=found)

    def get(self, path: str) -> Param | None:
        return self._by_path.get(path)

    def as_dict(self) -> dict:
        return {
            "services": [service.__dict__ for service in self.services],
            "consumers": [consumer.__dict__ for consumer in self.consumers],
            "sections": list(SECTIONS),
            "groups": [group.__dict__ for group in self.groups],
            "blocks": [{key: list(value) if isinstance(value, tuple) else value for key, value in block.__dict__.items()}
                       for block in self.blocks],
            "params": [param.as_dict() for param in self.params],
        }
