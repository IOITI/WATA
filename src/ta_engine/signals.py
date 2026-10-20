"""
What an alert of a rule's script becomes: a signal for the trader, or nothing.

An alertcondition() is known by its title, which the rule maps to an action
(long, short, close-position), to "message", or to nothing. An alert() call has
no title: the rule reads all of them as messages, or none. A message is WATA's
webhook JSON, as TradingView's alerts send it: its action is read, its indice
must be the rule's, and its confidence is kept. TradingView's placeholders are
filled in first ({{close}}, {{time}}, {{plot("Title")}}, ...).

WATA stamps its signals itself, whatever a message says: signal_timestamp is
when the bar closed, alert_timestamp when the engine sends it, both in whole
seconds as the trader reads them. The web server keeps the latest signal of
each indice by signal_timestamp: the close of the bar is never older than a
signal that came before it.
"""

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import jsonschema

from src.admin.ta_rules import IGNORE, MESSAGE, SIGNAL_ACTIONS
from src.schema import SchemaLoader

# The confidence the webhook schema accepts.
CONFIDENCE_RANGE = (0.0, 2.0)
_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_PLOT_BY_TITLE = re.compile(r'^plot\(\s*"([^"]*)"\s*\)$')
_PLOT_BY_INDEX = re.compile(r"^plot_(\d+)$")


@dataclass(frozen=True)
class Candidate:
    """An alert that is to become a signal; ``action`` None when it cannot, ``detail`` saying why."""

    key: str
    title: str | None
    message: str
    action: str | None
    confidence: float | None
    detail: str | None = None


def iso_seconds(moment: float) -> str:
    """A moment as the trader reads it: UTC, whole seconds."""
    return datetime.fromtimestamp(int(moment), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _number_text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and math.isfinite(value):
        return repr(value) if isinstance(value, float) else str(value)
    return "NaN"


def fill_placeholders(message: str, *, bar: list, ticker: str, timeframe: str, plots: dict, now: float) -> str:
    """
    TradingView's placeholders as they would be at the bar's close: {{open}},
    {{high}}, {{low}}, {{close}}, {{volume}}, {{time}} (the bar's open),
    {{timenow}}, {{ticker}}, {{interval}}, {{plot("Title")}} and {{plot_0}}
    (in the order the plots came). Any other is left as it is.
    """
    titles = list(plots)
    values = {
        "open": _number_text(bar[1]), "high": _number_text(bar[2]), "low": _number_text(bar[3]),
        "close": _number_text(bar[4]), "volume": _number_text(bar[5]),
        "time": iso_seconds(bar[0] / 1000), "timenow": iso_seconds(now),
        "ticker": ticker, "interval": timeframe,
    }
    at = str(bar[0])

    def filled(match: re.Match) -> str:
        name = match.group(1)
        if name in values:
            return values[name]
        by_title = _PLOT_BY_TITLE.match(name)
        if by_title and by_title.group(1) in plots:
            return _number_text(plots[by_title.group(1)].get(at))
        by_index = _PLOT_BY_INDEX.match(name)
        if by_index and int(by_index.group(1)) < len(titles):
            return _number_text(plots[titles[int(by_index.group(1))]].get(at))
        return match.group(0)

    return _PLACEHOLDER.sub(filled, message)


def _confidence(value) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    low, high = CONFIDENCE_RANGE
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None, f"a confidence of {value!r} is not a number: sent without"
    if not low <= value <= high:
        return None, f"a confidence of {value:g} is outside {low:g} to {high:g}: sent without"
    return float(value), None


def alert_key(alert: dict) -> str:
    """What makes an alert one of its bar: the title of an alertcondition(), the call and message of an alert()."""
    if alert.get("type") == "alertcondition":
        return f"alertcondition:{alert.get('title')}"
    return f"alert:{alert.get('id')}:{alert.get('message')}"


def interpret(rule: dict, alert: dict, *, bar: list, ticker: str, timeframe: str, plots: dict, now: float) -> Candidate | None:
    """The signal ``alert`` makes under ``rule``; None when the rule ignores it."""
    title = alert.get("title") if isinstance(alert.get("title"), str) else None
    message = alert.get("message") if isinstance(alert.get("message"), str) else ""
    if alert.get("type") == "alertcondition":
        mapped = next((entry["action"] for entry in rule["alerts"] if entry["title"] == title), IGNORE)
    else:
        mapped = rule["alert_calls"]
    if mapped == IGNORE:
        return None
    key = alert_key(alert)
    filled = fill_placeholders(message, bar=bar, ticker=ticker, timeframe=timeframe, plots=plots, now=now)
    notes: list[str] = []
    confidence = None
    if mapped == MESSAGE:
        try:
            content = json.loads(filled)
        except ValueError:
            return Candidate(key, title, filled, None, None, "The message is not JSON")
        if not isinstance(content, dict):
            return Candidate(key, title, filled, None, None, "The message is not a JSON object")
        action = content.get("action")
        if action not in SIGNAL_ACTIONS:
            return Candidate(key, title, filled, None, None, f"The message's action {action!r} is not one WATA sends")
        indice = content.get("indice")
        if indice is not None and indice != rule["indice"]:
            return Candidate(key, title, filled, None, None, f"The message is for {indice!r}, the rule for {rule['indice']!r}")
        confidence, note = _confidence(content.get("confidence"))
        if note:
            notes.append(note)
    else:
        action = mapped
    if confidence is None and rule.get("confidence_plot"):
        value = plots.get(rule["confidence_plot"], {}).get(str(bar[0]))
        confidence, note = _confidence(value)
        if note:
            notes.append(f"the plot {rule['confidence_plot']!r}: {note}")
    return Candidate(key, title, filled, action, confidence, "; ".join(notes) or None)


def payload(rule: dict, candidate: Candidate, *, bar_closed_at: float, now: float) -> dict:
    """What is sent to the web server, checked against the webhook's schema."""
    body = {
        "action": candidate.action,
        "indice": rule["indice"],
        "signal_timestamp": iso_seconds(bar_closed_at),
        "alert_timestamp": iso_seconds(now),
    }
    if candidate.confidence is not None and candidate.action in ("long", "short"):
        body["confidence"] = candidate.confidence
    jsonschema.validate(instance=body, schema=SchemaLoader.get_webhook_schema())
    return body
