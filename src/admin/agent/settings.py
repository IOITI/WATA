"""
What the admin's AI agent may do and spend (``admin.ai_agent`` in config.json,
each with a default). Read at the admin's start, like every ``admin.*`` key, and
checked here as ``admin.mcp`` is by McpLimits: a wrong value stops the save that
would write it, never the admin.

No key holds "token" in its name: the editor masks such keys as secrets.
"""

from dataclasses import dataclass, fields

PROVIDERS = ("anthropic",)
MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# name: (default, choices)
_CHOICES = {
    "provider": ("anthropic", PROVIDERS),
    "model": ("claude-opus-5-5", MODELS),
    # How much the model thinks before it acts and answers: an analysis is worth high.
    "effort": ("high", EFFORTS),
    # The daily debrief's: a day's account needs less than an analysis.
    "debrief_model": ("claude-sonnet-5-5", MODELS),
    "debrief_effort": ("medium", EFFORTS),
    # The morning briefing's: an account of facts the tools give, on the cheapest model.
    "briefing_model": ("claude-haiku-5-5", MODELS),
    "briefing_effort": ("low", EFFORTS),
    # An alert's triage: a diagnosis, worth a model that reasons.
    "triage_model": ("claude-sonnet-5-5", MODELS),
    "triage_effort": ("medium", EFFORTS),
}
# name: (default, lowest, highest)
_WHOLE = {
    # Tool calls one request may make, and backtests it may start (derived runs included).
    "max_tool_calls": (60, 5, 200),
    "max_backtests": (8, 0, 30),
    # Requests worked on at once; the others wait their turn.
    "max_running": (2, 1, 4),
    # Conversations kept, from their last change.
    "keep_days": (365, 30, 3650),
    # Backtests one research task may start in all, over its requests.
    "max_task_backtests": (40, 0, 200),
    # How long before the trading start the morning briefing is written, in minutes.
    "briefing_minutes_before": (30, 5, 240),
    # Alerts triaged in a day at most, all together.
    "triage_max_per_day": (6, 1, 30),
    # Routines kept at most: research tasks that come back by themselves.
    "max_routines": (10, 1, 50),
}
# name: default, a switch.
_SWITCHES = {
    # Whether the agent may read the services' log lines (cleaned of secrets): they go to Anthropic with the request.
    "read_logs": True,
    # A debrief of each trading day once the market is closed, said on Telegram when debrief_telegram.
    "debrief_enabled": False,
    "debrief_telegram": True,
    # Its memory between conversations (src/admin/agent_memory.py): its index shared at each one's start, its tools.
    "memory": True,
    # A briefing before each trading day's start, said on Telegram when briefing_telegram.
    "briefing_enabled": False,
    "briefing_telegram": True,
    # An alert (a sale refused, a service down in trading hours, live drift...) triaged by the agent itself, its
    # diagnosis said on Telegram when triage_telegram. Read at the admin's start: it listens to the admin's events.
    "triage_enabled": False,
    "triage_telegram": True,
    # Whether it may propose actions on live trading (a pause, a close, a restart, a rule live or out), each applied
    # only by the operator's click through the page's own confirmation.
    "action_proposals": True,
    # A go-live dossier written by itself once an operator's rule in shadow meets its readiness.
    "dossier_when_ready": False,
}
# name: (default, lowest, highest), in US dollars as the Claude API bills them.
_AMOUNTS = {
    "max_request_usd": (5.0, 0.5, 100.0),
    "monthly_budget_usd": (50.0, 1.0, 10_000.0),
    # A research task's whole budget at most, and its hours (checked between its requests).
    "max_task_usd": (20.0, 1.0, 1_000.0),
    "max_task_hours": (12.0, 0.5, 72.0),
    # What one daily debrief may cost.
    "debrief_max_usd": (0.5, 0.1, 5.0),
    # What one morning briefing, and one triage, may cost.
    "briefing_max_usd": (0.3, 0.05, 2.0),
    "triage_max_usd": (0.5, 0.1, 5.0),
}


@dataclass(frozen=True)
class AgentSettings:
    # Off until the operator switches it on and sets the Claude API key (Credentials).
    enabled: bool = False
    provider: str = "anthropic"
    model: str = "claude-opus-5-5"
    effort: str = "high"
    max_tool_calls: int = 60
    max_backtests: int = 8
    max_request_usd: float = 5.0
    monthly_budget_usd: float = 50.0
    max_running: int = 2
    keep_days: int = 365
    read_logs: bool = True
    max_task_usd: float = 20.0
    max_task_backtests: int = 40
    max_task_hours: float = 12.0
    debrief_enabled: bool = False
    debrief_model: str = "claude-sonnet-5-5"
    debrief_effort: str = "medium"
    debrief_max_usd: float = 0.5
    debrief_telegram: bool = True
    memory: bool = True
    briefing_enabled: bool = False
    briefing_model: str = "claude-haiku-5-5"
    briefing_effort: str = "low"
    briefing_max_usd: float = 0.3
    briefing_minutes_before: int = 30
    briefing_telegram: bool = True
    triage_enabled: bool = False
    triage_model: str = "claude-sonnet-5-5"
    triage_effort: str = "medium"
    triage_max_usd: float = 0.5
    triage_max_per_day: int = 6
    triage_telegram: bool = True
    action_proposals: bool = True
    dossier_when_ready: bool = False
    max_routines: int = 10

    @classmethod
    def from_config(cls, section) -> "AgentSettings":
        """``admin.ai_agent``: a value missing takes its default; one of the wrong kind or out of bounds is refused."""
        section = {} if section is None else section
        if not isinstance(section, dict):
            raise ValueError("Config field 'admin.ai_agent' must be an object")
        unknown = set(section) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown field(s) in 'admin.ai_agent': {', '.join(sorted(unknown))}")
        values = {}
        enabled = section.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("Config field 'admin.ai_agent.enabled' must be true or false")
        for name, (default, choices) in _CHOICES.items():
            value = section.get(name, default)
            if value not in choices:
                raise ValueError(f"Config field 'admin.ai_agent.{name}' must be one of {', '.join(choices)}")
            values[name] = value
        for name, (default, lowest, highest) in _WHOLE.items():
            value = section.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int) or not lowest <= value <= highest:
                raise ValueError(f"Config field 'admin.ai_agent.{name}' must be a whole number from {lowest} to {highest}")
            values[name] = value
        for name, default in _SWITCHES.items():
            value = section.get(name, default)
            if not isinstance(value, bool):
                raise ValueError(f"Config field 'admin.ai_agent.{name}' must be true or false")
            values[name] = value
        for name, (default, lowest, highest) in _AMOUNTS.items():
            value = section.get(name, default)
            # A whole number typed on the page comes back as one: 5 is 5.0.
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lowest <= value <= highest:
                raise ValueError(f"Config field 'admin.ai_agent.{name}' must be an amount from {lowest:g} to {highest:g}")
            values[name] = float(value)
        return cls(enabled=enabled, **values)

    def as_dict(self) -> dict:
        return {field.name: getattr(self, field.name) for field in fields(self)}
