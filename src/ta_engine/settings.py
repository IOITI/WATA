"""
Settings of the TA engine (``trade.config.ta_engine``), read and checked here,
by the engine, as the Capital.com feed reads its own: a mistake in this section
must not keep the trader from starting. Off unless switched on.
"""

import re
from dataclasses import dataclass

SECTION = "trade.config.ta_engine"

# Where the admin's Technical Analysis folder is mounted, read-only, in the
# engine's container, and the Node the image has. Not settings: the
# configuration can be edited from the admin, and whoever could change these
# would choose what the engine reads and starts.
RULES_PATH = "/app/var/lib/ta"
NODE = "node"
DEFAULT_SERVICE_URL = "http://web_server1:80"
_TIME_OF_DAY = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")


def _flag(section: dict, key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Config field '{SECTION}.{key}' must be true or false")
    return value


def _whole(section: dict, key: str, default: int, *, minimum: int, maximum: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"Config field '{SECTION}.{key}' must be a whole number from {minimum} to {maximum}")
    return value


def _text(section: dict, key: str, default: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Config field '{SECTION}.{key}' must be a text")
    return value.strip()


@dataclass(frozen=True)
class TaEngineSettings:
    enabled: bool = False
    # Days of bars before the last ``restart_time`` a rule's run begins with. Each
    # new bar runs the script over all of a rule's bars: between this many days
    # and one more.
    history_days: int = 1
    # When (UTC, HH:MM) every rule starts again: best while the market is closed,
    # as in the Nasdaq 100's daily break.
    restart_time: str = "21:15"
    # The signal of a bar that closed longer ago is journaled, never sent.
    max_signal_age_seconds: int = 60
    # Without ten seconds recorded by the Capital.com feed for this long, nothing is sent.
    stale_feed_seconds: int = 90
    # A run that takes longer stops the runner, which starts again for the next bar.
    run_timeout_seconds: int = 30
    runner_memory_mb: int = 512
    # The shadow rules run on a runner of their own (they never reload a live rule), with this heap...
    shadow_runner_memory_mb: int = 256
    # ...and at most this long of each tick, after the live rules: the rest wait for the next tick.
    shadow_seconds_per_tick: int = 5
    rules_path: str = RULES_PATH
    node_path: str = NODE
    # The web server that takes the signals, as the scheduler reaches it (a protected setting of the admin).
    service_url: str = DEFAULT_SERVICE_URL
    request_timeout_seconds: int = 3
    # The days the engine keeps what it measures of itself (ta_engine_tick, ta_rule_run), which
    # the admin's Engine monitor reads: it removes older rows at its start and each restart_time.
    monitoring_days: int = 7

    @classmethod
    def from_config(cls, config_manager) -> "TaEngineSettings":
        section = config_manager.get_config_value(SECTION, {}) or {}
        if not isinstance(section, dict):
            raise ValueError(f"Config field '{SECTION}' must be an object")
        restart_time = _text(section, "restart_time", cls.restart_time)
        if not _TIME_OF_DAY.match(restart_time):
            raise ValueError(f"Config field '{SECTION}.restart_time' must be a time of day, HH:MM (UTC)")
        polling = config_manager.get_config_value("trade.config.signal_polling", {}) or {}
        service_url = polling.get("service_url", DEFAULT_SERVICE_URL) if isinstance(polling, dict) else DEFAULT_SERVICE_URL
        return cls(
            enabled=_flag(section, "enabled", cls.enabled),
            history_days=_whole(section, "history_days", cls.history_days, minimum=0, maximum=30),
            restart_time=restart_time,
            max_signal_age_seconds=_whole(section, "max_signal_age_seconds", cls.max_signal_age_seconds, minimum=5, maximum=3600),
            stale_feed_seconds=_whole(section, "stale_feed_seconds", cls.stale_feed_seconds, minimum=20, maximum=3600),
            run_timeout_seconds=_whole(section, "run_timeout_seconds", cls.run_timeout_seconds, minimum=5, maximum=600),
            runner_memory_mb=_whole(section, "runner_memory_mb", cls.runner_memory_mb, minimum=128, maximum=4096),
            shadow_runner_memory_mb=_whole(section, "shadow_runner_memory_mb", cls.shadow_runner_memory_mb, minimum=128, maximum=4096),
            shadow_seconds_per_tick=_whole(section, "shadow_seconds_per_tick", cls.shadow_seconds_per_tick, minimum=1, maximum=8),
            service_url=str(service_url).rstrip("/"),
            request_timeout_seconds=_whole(section, "request_timeout_seconds", cls.request_timeout_seconds, minimum=1, maximum=30),
            monitoring_days=_whole(section, "monitoring_days", cls.monitoring_days, minimum=1, maximum=30),
        )

    @property
    def restart_minute(self) -> int:
        """``restart_time`` as minutes after midnight, UTC."""
        hours, minutes = self.restart_time.split(":")
        return int(hours) * 60 + int(minutes)
