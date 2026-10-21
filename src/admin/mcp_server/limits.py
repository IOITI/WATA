"""
What Claude may use of the server through the MCP endpoint (``admin.mcp`` in
config.json, each with a default). The admin shares one processor and a
gigabyte with the backtests, and the disk with PostgreSQL: an agent left to
work through a night must stay inside these.
"""

from dataclasses import dataclass, fields

# name: (default, lowest, highest)
_BOUNDS = {
    # Claude's runs waiting at once: the operator's runs and the nightly paper runs always find room.
    "max_waiting_runs": (3, 1, 10),
    # Runs and sweeps Claude may start in a day (UTC).
    "runs_per_day": (60, 1, 1000),
    # Days simulated by one request: days × (combinations of a sweep, or 1 + random repetitions).
    "max_day_simulations": (10_000, 100, 1_000_000),
    # Minutes of Pine studies (check, run, signals) in any hour.
    "study_minutes_per_hour": (20, 1, 60),
    # The study's Node heap and time limit. The heap fits beside a backtest in the admin's 3 GB.
    "study_memory_mb": (768, 128, 1024),
    "study_timeout_seconds": (90, 10, 300),
    # Scripts Claude keeps at once.
    "max_scripts": (100, 1, 500),
    # Shadow rules Claude keeps at once.
    "max_shadow_rules": (3, 0, 10),
    # What a day of the engine may cost one of them, in ms: the engine runs its script over
    # all of its bars at each new one, so a day's run is about what each bar costs at its end.
    "shadow_day_ms": (2000, 500, 10_000),
    # Nothing is started when the disk has less than this free.
    "min_free_disk_gb": (5, 1, 1000),
}


@dataclass(frozen=True)
class McpLimits:
    max_waiting_runs: int = 3
    runs_per_day: int = 60
    max_day_simulations: int = 10_000
    study_minutes_per_hour: int = 20
    study_memory_mb: int = 768
    study_timeout_seconds: int = 90
    max_scripts: int = 100
    max_shadow_rules: int = 3
    shadow_day_ms: int = 2000
    min_free_disk_gb: int = 5
    # Claude's shadow rules: off until the operator has watched the isolated TA engine at work.
    shadow_rules: bool = False

    @classmethod
    def from_config(cls, section) -> "McpLimits":
        """``admin.mcp``: a value missing takes its default; one of the wrong kind or out of bounds is refused."""
        section = {} if section is None else section
        if not isinstance(section, dict):
            raise ValueError("Config field 'admin.mcp' must be an object")
        unknown = set(section) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown field(s) in 'admin.mcp': {', '.join(sorted(unknown))}")
        values = {}
        for name, (default, lowest, highest) in _BOUNDS.items():
            value = section.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int) or not lowest <= value <= highest:
                raise ValueError(f"Config field 'admin.mcp.{name}' must be a whole number from {lowest} to {highest}")
            values[name] = value
        shadow_rules = section.get("shadow_rules", False)
        if not isinstance(shadow_rules, bool):
            raise ValueError("Config field 'admin.mcp.shadow_rules' must be true or false")
        return cls(**values, shadow_rules=shadow_rules)

    def as_dict(self) -> dict:
        return {field.name: getattr(self, field.name) for field in fields(self)}
