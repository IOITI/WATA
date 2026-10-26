"""
The webhooks the web server answered without storing them, by index: each an
alert of TradingView for an index that takes the TA engine's signals
(src/trade/signal_sources.py). Counted since the web server started, in memory,
for the admin's Signals page: an alert left on in TradingView is seen there.
"""


class IgnoredWebhooks:
    def __init__(self):
        self._by_indice: dict[str, dict] = {}

    def note(self, indice: str, received_timestamp: str, action: str | None = None) -> None:
        counted = self._by_indice.setdefault(indice, {"count": 0, "first_at": received_timestamp})
        counted["count"] += 1
        counted["last_at"] = received_timestamp
        counted["last_action"] = action

    def as_dict(self) -> dict[str, dict]:
        return {indice: dict(counted) for indice, counted in self._by_indice.items()}
