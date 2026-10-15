"""
The requests sent to Saxo's order endpoint (table ``order_request``), as the
Orders page shows them: those Saxo did not accept, grouped into runs (a row of
the page each), and the requests that belong to each order.

Pure: rows as read, rows out. What the pages say of them is
``admin_ui/src/features/positions/orderRequests.ts``.
"""

from datetime import timedelta

PLACE = "place"
CANCEL = "cancel"
ACCEPTED = "accepted"
REFUSED = "refused"
NO_ANSWER = "no_answer"
ERROR = "error"

# Two attempts further apart than this are two runs, even for the same position.
RUN_GAP = timedelta(minutes=5)
# The attempts a run carries, the most recent: its count and its answers are over all of them.
MAX_RUN_ATTEMPTS = 50
# Resting orders cancelled before a sell are part of that attempt to sell when cancelled this soon before it.
CANCEL_BEFORE_SELL = timedelta(seconds=30)

# A run is shown as an order is: these fields carry the order's names.
_AS_ORDER = {
    "buy_sell": "buy_sell",
    "amount": "order_amount",
    "order_type": "order_type",
    "price_seen": "instrument_price",
}
_SAME_FOR_EVERY_ATTEMPT = (
    "purpose", "position_id", "signal_uuid", "action", "instrument_name", "instrument_symbol",
    "instrument_uic", "instrument_currency", "asset_type",
)


def _intent(request: dict):
    """What a request was for: the sale (or the stop) of a position, the buy of a signal, or itself alone."""
    purpose = request.get("purpose")
    if request.get("position_id") and purpose in ("close", "stop"):
        return purpose, "position", request["position_id"]
    if request.get("signal_uuid"):
        return purpose, "signal", request["signal_uuid"]
    return "alone", request.get("request_id")


def _in_time_order(requests: list[dict]) -> list[dict]:
    return sorted(
        (request for request in requests if request.get("requested_at") is not None),
        key=lambda request: (request["requested_at"], request.get("request_id") or ""),
    )


def _named_orders(request: dict) -> list[str]:
    """The orders a cancellation names (Saxo takes them separated by commas)."""
    return [part.strip() for part in str(request.get("order_id") or "").split(",") if part.strip()]


def refusal_runs(requests: list[dict]) -> list[dict]:
    """
    The orders Saxo did not take, grouped into runs, newest first.

    A run is the attempts made with one intent (selling a position, buying for
    a signal) that Saxo did not accept: it ends when Saxo accepts one, which
    is then the order that went through after it, or after ``RUN_GAP``
    without an attempt.
    """
    ordered = _in_time_order(requests)
    open_runs: dict = {}
    runs: list[dict] = []
    for request in ordered:
        if request.get("operation") != PLACE:
            continue
        intent = _intent(request)
        current = open_runs.get(intent)
        if request.get("outcome") == ACCEPTED:
            if current is not None:
                current["followed_by"] = {"order_id": request.get("order_id"), "requested_at": request["requested_at"]}
                del open_runs[intent]
            continue
        if current is not None and request["requested_at"] - current["attempts"][-1]["requested_at"] > RUN_GAP:
            current = None
        if current is None:
            current = {"attempts": [], "followed_by": None}
            open_runs[intent] = current
            runs.append(current)
        current["attempts"].append(request)

    cancels = [request for request in ordered if request.get("operation") == CANCEL]
    built = [_run(run["attempts"], run["followed_by"], cancels) for run in runs]
    return sorted(built, key=lambda run: run["first_at"], reverse=True)


def _run(attempts: list[dict], followed_by: dict | None, cancels: list[dict]) -> dict:
    first, last = attempts[0], attempts[-1]
    answers: dict = {}
    for attempt in attempts:
        key = tuple(attempt.get(field) for field in ("outcome", "http_status", "error_code", "error_message"))
        answer = answers.get(key)
        if answer is None:
            answer = answers[key] = {
                "outcome": attempt.get("outcome"),
                "http_status": attempt.get("http_status"),
                "error_code": attempt.get("error_code"),
                "error_message": attempt.get("error_message"),
                "count": 0,
                "first_at": attempt["requested_at"],
                "last_at": attempt["requested_at"],
            }
        answer["count"] += 1
        answer["last_at"] = attempt["requested_at"]

    outcomes = {attempt.get("outcome") for attempt in attempts}
    run = {
        # A run is named by its first attempt: that never changes as it grows.
        "run_id": first.get("request_id"),
        **{field: next((a[field] for a in reversed(attempts) if a.get(field) is not None), None)
           for field in _SAME_FOR_EVERY_ATTEMPT},
        **{name: last.get(field) for field, name in _AS_ORDER.items()},
        "reason": last.get("reason"),
        "first_at": first["requested_at"],
        "last_at": last["requested_at"],
        "answered_at": last.get("answered_at"),
        "count": len(attempts),
        "outcome": REFUSED if REFUSED in outcomes else NO_ANSWER if NO_ANSWER in outcomes else ERROR,
        # Saxo's latest answer, at the top: what the page's search and export read.
        "http_status": last.get("http_status"),
        "error_code": last.get("error_code"),
        "error_message": last.get("error_message"),
        "recorded_by": sorted({a["recorded_by"] for a in attempts if a.get("recorded_by")}),
        "answers": sorted(answers.values(), key=lambda answer: (-answer["count"], answer["first_at"])),
        "followed_by": followed_by,
    }
    shown = attempts[-MAX_RUN_ATTEMPTS:]
    if run["purpose"] == "close" and run["position_id"]:
        # Resting orders cancelled on the way to these sells are part of them.
        since = first["requested_at"] - CANCEL_BEFORE_SELL
        shown = shown + [
            cancel for cancel in cancels
            if cancel.get("position_id") == run["position_id"] and since <= cancel["requested_at"] <= last["requested_at"]
        ]
    run["requests"] = _in_time_order(shown)
    run["requests_total"] = len(attempts)
    return run


def requests_by_order(requests: list[dict], order_ids: list[str]) -> dict[str, list[dict]]:
    """
    The requests that belong to each order, oldest first: the one that placed
    it, the cancellations that name it, and the resting orders cancelled just
    before it on the same position (the same attempt to sell).
    """
    wanted = {str(order_id) for order_id in order_ids if order_id}
    ordered = _in_time_order(requests)
    placed = [
        request for request in ordered
        if request.get("operation") == PLACE and request.get("outcome") == ACCEPTED and request.get("order_id") in wanted
    ]
    found: dict[str, list[dict]] = {}
    for request in placed:
        found.setdefault(request["order_id"], []).append(request)
    for request in ordered:
        if request.get("operation") != CANCEL:
            continue
        named = [order_id for order_id in _named_orders(request) if order_id in wanted]
        if named:
            for order_id in named:
                found.setdefault(order_id, []).append(request)
            continue
        if request.get("purpose") == "close" and request.get("position_id"):
            sell = next(
                (
                    sale for sale in placed
                    if sale.get("purpose") == "close"
                    and sale.get("position_id") == request["position_id"]
                    and request["requested_at"] <= sale["requested_at"] <= request["requested_at"] + CANCEL_BEFORE_SELL
                ),
                None,
            )
            if sell is not None:
                found.setdefault(sell["order_id"], []).append(request)
    return {order_id: _in_time_order(found_requests) for order_id, found_requests in found.items()}
