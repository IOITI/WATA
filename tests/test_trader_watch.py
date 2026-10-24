"""
What the trader says of its own health between signals (src/trade/trader_watch.py):
the web server it cannot poll, signals that stopped, Saxo refusing to renew its
login, Saxo's session below its full level; and no turbo bought on a quote Saxo
says is delayed.
"""

import asyncio
import sys
import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.trade.async_services import build_selected_turbo_from_candidate_snapshots  # noqa: E402
from src.trade.exceptions import NoMarketAvailableException  # noqa: E402
from src.trade.trader_watch import (  # noqa: E402
    FULL_SESSION_CHECK_EVERY_SECONDS,
    LOGIN_ALERT_EVERY_SECONDS,
    POLL_FAILING_ALERT_SECONDS,
    SESSION_ALERT_EVERY_SECONDS,
    SESSION_CHECK_EVERY_SECONDS,
    SESSION_CONFIRM_SECONDS,
    TraderWatch,
)

PARIS = pytz.timezone("Europe/Paris")


def paris(*moment) -> float:
    return PARIS.localize(datetime(*moment)).timestamp()


def watch_for(clock, **settings):
    send = AsyncMock()
    watch = TraderWatch(send, now=lambda: clock["now"], **settings)
    return watch, send


def said(send) -> list[str]:
    return [call.args[0] for call in send.await_args_list]


def test_a_web_server_that_cannot_be_polled_is_said_after_a_minute_and_when_it_is_back():
    clock = {"now": paris(2026, 10, 26, 10, 0)}
    watch, send = watch_for(clock)

    async def scenario():
        await watch.polled(False, ConnectionError("refused"))
        clock["now"] += POLL_FAILING_ALERT_SECONDS - 1
        await watch.polled(False, ConnectionError("refused"))
        clock["now"] += 1
        await watch.polled(False, ConnectionError("refused"))
        clock["now"] += 30
        await watch.polled(False, ConnectionError("refused"))
        await watch.polled(True)

    asyncio.run(scenario())

    assert said(send) == [
        "The trader cannot reach the web server since 10:00: no signal can arrive. Last: ConnectionError: refused",
        "The trader reaches the web server again: signals arrive since 10:01.",
    ]
    assert watch.figures()["poll_failing_since"] is None


def test_signals_that_stopped_are_said_once_in_trading_hours():
    clock = {"now": paris(2026, 10, 26, 10, 0)}
    in_hours = {"value": False}
    watch, send = watch_for(clock, signal_silence_minutes=30, in_trading_hours=lambda: in_hours["value"])

    async def scenario():
        clock["now"] += 31 * 60
        await watch.check_silence()  # outside the trading hours: nothing said
        in_hours["value"] = True
        await watch.check_silence()
        await watch.check_silence()  # said once
        watch.signal_received()
        clock["now"] += 31 * 60
        await watch.check_silence()

    asyncio.run(scenario())

    assert said(send) == [
        "No signal for 31 min (the last at 10:00): TradingView's alerts may have stopped.",
        "No signal for 31 min (the last at 10:31): TradingView's alerts may have stopped.",
    ]


def test_a_setting_that_is_not_a_number_of_minutes_leaves_it_off():
    for value in ("30", True, -5, 0, None):
        watch, _ = watch_for({"now": 0.0}, signal_silence_minutes=value)
        assert watch.signal_silence_seconds is None, value


def test_without_the_setting_silence_is_never_said():
    clock = {"now": paris(2026, 10, 26, 10, 0)}
    watch, send = watch_for(clock)
    clock["now"] += 24 * 3600

    asyncio.run(watch.check_silence())

    send.assert_not_awaited()


def _auth(error=None, lapses_at="2026-10-23T18:25:00+00:00"):
    auth = MagicMock()
    auth.last_refresh_error = error
    auth.get_token_status.return_value = {"refresh_token_expires_at": lapses_at}
    return auth


def test_a_login_saxo_refuses_to_renew_is_said_and_no_new_position_is_taken():
    clock = {"now": paris(2026, 10, 23, 20, 5)}
    auth = _auth("Saxo answered 401: invalid_grant")
    watch, send = watch_for(clock, saxo_auth=auth)

    async def scenario():
        await watch.check_login()
        clock["now"] += 60
        await watch.check_login()  # not said again before LOGIN_ALERT_EVERY_SECONDS
        refusal = watch.login_refusal()
        auth.last_refresh_error = None
        await watch.check_login()
        return refusal

    refusal = asyncio.run(scenario())

    messages = said(send)
    assert messages[0] == (
        "Saxo refuses to renew WATA's login since 20:05: Saxo answered 401: invalid_grant. No new position is taken "
        "meanwhile. It lapses at 20:25, in 20 min: then nothing can be bought or sold until someone logs in again "
        "(watasaxoauth, or the admin's Saxo page)."
    )
    assert messages[1] == "Saxo renews WATA's login again."
    assert refusal == ("Breaking trading rule: Saxo refuses to renew WATA's login since 20:05: no new position until "
                       "it is renewed (Saxo answered 401: invalid_grant).")
    assert watch.login_refusal() is None


def test_a_refusal_that_lasts_is_said_again_every_quarter_of_an_hour():
    clock = {"now": paris(2026, 10, 23, 19, 0)}
    watch, send = watch_for(clock, saxo_auth=_auth("Saxo answered 503", lapses_at="2026-10-23T19:30:00+00:00"))

    async def scenario():
        await watch.check_login()
        clock["now"] += LOGIN_ALERT_EVERY_SECONDS
        await watch.check_login()

    asyncio.run(scenario())

    assert len(said(send)) == 2


def test_a_session_below_the_full_level_is_said_and_its_return():
    clock = {"now": paris(2026, 10, 26, 9, 0)}
    watch, send = watch_for(clock)
    api_client = MagicMock()
    answers = [{"TradeLevel": "FullTradingAndChat", "DataLevel": "Standard"},
               {"TradeLevel": "OrdersOnly", "DataLevel": "Standard"},
               {"TradeLevel": "OrdersOnly", "DataLevel": "Standard"},
               {"TradeLevel": "FullTradingAndChat", "DataLevel": "Standard"}]
    api_client.request = AsyncMock(side_effect=answers)

    async def scenario():
        for _ in range(4):
            await watch.check_session(api_client)
            await watch.check_session(api_client)  # too soon: not asked again
            clock["now"] += SESSION_CHECK_EVERY_SECONDS

    asyncio.run(scenario())

    assert api_client.request.await_count == 4
    messages = said(send)
    assert len(messages) == 2
    assert messages[0].startswith("Saxo serves WATA's session at OrdersOnly (data Standard), not FullTradingAndChat")
    assert messages[1] == "Saxo serves WATA's session at its full level again (FullTradingAndChat)."
    assert watch.figures()["saxo_session"]["trade_level"] == "FullTradingAndChat"


def test_a_check_that_fails_never_stops_the_others():
    clock = {"now": paris(2026, 10, 26, 9, 0)}
    auth = _auth("Saxo answered 401")
    watch, send = watch_for(clock, saxo_auth=auth)
    api_client = MagicMock()
    api_client.request = AsyncMock(side_effect=ConnectionError("down"))

    asyncio.run(watch.check(api_client))

    assert said(send)[0].startswith("Saxo refuses to renew WATA's login")


# ── no turbo bought on a delayed quote ──


def _candidate(uic: int, bid: float, delayed: int = 0) -> dict:
    return {"Uic": uic, "Identifier": uic, "AssetType": "WarrantOpenEndKnockOut",
            "Quote": {"Bid": bid, "Ask": bid + 0.01, "DelayedByMinutes": delayed, "MarketState": "Open",
                      "PriceTypeBid": "Tradable", "PriceTypeAsk": "Tradable"},
            "DisplayAndFormat": {"Description": f"NASDAQ 100 Turbo Long {uic} Saxo", "Symbol": "T", "Currency": "EUR"}}


def _select(candidates):
    return build_selected_turbo_from_candidate_snapshots(
        exchange_id="CATS_SAXO", underlying_uics="1909050", keywords="long", candidate_snapshots=candidates,
        selected_source_lookup={}, min_price=4, max_price=15,
    )


def test_no_turbo_is_bought_on_quotes_saxo_says_are_delayed():
    with pytest.raises(NoMarketAvailableException, match="delayed by 15 min: none is bought on them"):
        _select([_candidate(1, 5.0, delayed=15), _candidate(2, 6.0, delayed=15)])


def test_a_quote_on_time_is_chosen_beside_a_delayed_one():
    selected = _select([_candidate(1, 5.0, delayed=15), _candidate(2, 6.0)])

    assert selected["selected_instrument"]["uic"] == 2


# ── the full level asked for ──


class FakeSaxo:
    """Saxo's session endpoints: the levels it reads in turn, and what it was asked."""

    def __init__(self, levels, refuse=None):
        self.levels = list(levels)
        self.refuse = refuse
        self.asked = []

    async def request(self, endpoint):
        from src.saxo_openapi.endpoints.rootservices.sessions import GetSessionCapabilities, PatchSessionCapabilities

        if isinstance(endpoint, PatchSessionCapabilities):
            self.asked.append((endpoint.method, endpoint.data))
            if self.refuse is not None:
                raise self.refuse
            return None
        assert isinstance(endpoint, GetSessionCapabilities)
        return {"TradeLevel": self.levels.pop(0), "DataLevel": "Standard"}


def _checked(watch, saxo, clock, steps):
    """The checks at each (seconds later) step."""

    async def scenario():
        for seconds in steps:
            clock["now"] += seconds
            await watch.check_session(saxo)

    asyncio.run(scenario())


def test_asked_for_the_full_level_the_trader_gets_it_and_says_so_once():
    clock = {"now": paris(2026, 10, 26, 9, 0)}
    watch, send = watch_for(clock, full_trading_level=True)
    saxo = FakeSaxo(["OrdersOnly", "FullTradingAndChat", "FullTradingAndChat"])

    # At start; read back 10 s after asking; then every minute.
    _checked(watch, saxo, clock, [0, 5, SESSION_CONFIRM_SECONDS - 5, 30, FULL_SESSION_CHECK_EVERY_SECONDS - 30])

    assert saxo.asked == [("PATCH", {"TradeLevel": "FullTradingAndChat"})]
    assert saxo.levels == []
    assert said(send) == [
        "Saxo serves WATA's session at its full level (FullTradingAndChat), as asked: real-time prices. "
        "The account's other sessions (SaxoTraderGO) lose it while WATA holds it.",
    ]
    session = watch.figures()["saxo_session"]
    assert (session["trade_level"], session["wanted"], session["request_error"]) == ("FullTradingAndChat", "FullTradingAndChat", None)
    assert session["requested_at"] is not None


def test_a_full_level_another_session_took_is_asked_for_again_and_said():
    clock = {"now": paris(2026, 10, 26, 9, 0)}
    watch, send = watch_for(clock, full_trading_level=True)
    saxo = FakeSaxo(["FullTradingAndChat", "OrdersOnly", "FullTradingAndChat"])

    _checked(watch, saxo, clock, [0, FULL_SESSION_CHECK_EVERY_SECONDS, SESSION_CONFIRM_SECONDS])

    assert saxo.asked == [("PATCH", {"TradeLevel": "FullTradingAndChat"})]
    messages = said(send)
    assert messages[1] == ("Another session of the account took the full level (SaxoTraderGO?): WATA's session was at "
                           "OrdersOnly, with delayed prices, at 09:01. WATA asked for it again.")
    assert messages[2].startswith("Saxo serves WATA's session at its full level (FullTradingAndChat), as asked")


def test_a_full_level_saxo_refuses_is_said_once_a_quarter_of_an_hour_and_warned_of_in_health():
    from src.admin.health import WARNING, trader_health

    clock = {"now": paris(2026, 10, 26, 9, 0)}
    watch, send = watch_for(clock, full_trading_level=True)
    saxo = FakeSaxo(["OrdersOnly"] * 3, refuse=PermissionError("403 Forbidden"))

    _checked(watch, saxo, clock, [0, FULL_SESSION_CHECK_EVERY_SECONDS, SESSION_ALERT_EVERY_SECONDS])

    assert len(saxo.asked) == 3
    assert said(send) == [
        "WATA asked Saxo for the full session level (FullTradingAndChat) and Saxo refused: PermissionError: 403 Forbidden. "
        "The session stays at OrdersOnly: delayed prices.",
    ] * 2
    beat = {"beat_at": datetime.fromtimestamp(clock["now"], timezone.utc), "figures": watch.figures()}
    status, detail = trader_health(beat, datetime.fromtimestamp(clock["now"], timezone.utc), "Europe/Paris")
    assert (status, detail) == (WARNING, "Saxo serves the session at OrdersOnly, delayed prices, though WATA asks for the "
                                         "full level: PermissionError: 403 Forbidden")


def test_without_the_setting_nothing_is_asked_of_saxo():
    clock = {"now": paris(2026, 10, 26, 9, 0)}
    watch, send = watch_for(clock)
    saxo = FakeSaxo(["OrdersOnly", "OrdersOnly"])

    _checked(watch, saxo, clock, [0, FULL_SESSION_CHECK_EVERY_SECONDS, SESSION_CHECK_EVERY_SECONDS])

    assert saxo.asked == []
    assert saxo.levels == []
    assert watch.figures()["saxo_session"]["wanted"] is None
