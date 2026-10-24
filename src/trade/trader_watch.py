"""
What the trader knows of its own health between two signals: whether it can
reach the web server, how long no signal came, whether Saxo still renews its
login, and at what level Saxo serves its session. Said on Telegram once it
matters, written in its heartbeat (``service_heartbeat``) for the admin's
health and the dashboard's "Next entry" tile.

With ``trade.config.saxo_session.full_trading_level`` the trader also asks
Saxo for the full level of WATA's session (FullTradingAndChat): below it, Saxo
serves an app delayed market data. One session of an account holds it: the
others (SaxoTraderGO's) lose it, which may log them off, and the last to ask
gets it, so it is read every minute and asked for again when another took it.
The services share one login, so one session: what the trader gets, the
position monitor's and the recorder's prices get too.

None of this was said before: on 2026-10-23 Saxo's login lapsed at 20:05
Paris, and TradingView's alerts can stop without a word. Checked from the
keep-warm loop; never raises.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import pytz

import src.saxo_openapi.endpoints.rootservices as rs
from src.heartbeat import moment

logger = logging.getLogger(__name__)

# A web server that cannot be polled for this long is said: no signal can arrive.
POLL_FAILING_ALERT_SECONDS = 60
# A login whose renewal is refused is said again at most this often.
LOGIN_ALERT_EVERY_SECONDS = 900
# A refresh token left this long before it lapses, while renewals fail, is said.
LOGIN_LAPSING_ALERT_SECONDS = 1200
# Saxo's session level is read again this often (an app logged in elsewhere may take the full level), every
# minute while WATA asks for the full level, and soon after asking for it (Saxo applies it shortly after).
SESSION_CHECK_EVERY_SECONDS = 300
FULL_SESSION_CHECK_EVERY_SECONDS = 60
SESSION_CONFIRM_SECONDS = 10
# A request for the full level Saxo refuses is said again at most this often.
SESSION_ALERT_EVERY_SECONDS = 900
FULL_TRADE_LEVEL = "FullTradingAndChat"


def _clock(epoch: float, timezone_name: str) -> str:
    return datetime.fromtimestamp(epoch, pytz.timezone(timezone_name)).strftime("%H:%M")


def _epoch(iso: str | None) -> float | None:
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else None
    except (TypeError, ValueError):
        return None


class TraderWatch:
    def __init__(self, send, *, saxo_auth=None, timezone_name="Europe/Paris", signal_silence_minutes=None,
                 in_trading_hours=None, full_trading_level=False, now=time.time):
        self.send = send
        self.saxo_auth = saxo_auth
        self.timezone = timezone_name
        # Anything but a positive number of minutes (reporting.signal_silence_minutes) leaves it off.
        valid = isinstance(signal_silence_minutes, (int, float)) and not isinstance(signal_silence_minutes, bool)
        self.signal_silence_seconds = signal_silence_minutes * 60 if valid and signal_silence_minutes > 0 else None
        # Whether the trader would take an entry now by its hours and closed dates (TradingRule.check_market_hours).
        self.in_trading_hours = in_trading_hours or (lambda: True)
        self.now = now
        started = now()
        self.last_poll_at: float | None = None
        self.poll_failing_since: float | None = None
        self.poll_error: str | None = None
        self._poll_failure_said = False
        # Counted from the start: a trader started is not yet a trader that heard nothing.
        self.last_signal_at: float = started
        self._silence_said = False
        self.login_failing_since: float | None = None
        self.login_error: str | None = None
        self.login_lapses_at: float | None = None
        self._login_said_at: float | None = None
        self.session: dict | None = None
        self._session_next_check: float | None = None
        # Ask Saxo for the full level of the session (trade.config.saxo_session.full_trading_level).
        self.full_trading_level = full_trading_level is True
        self.session_requested_at: float | None = None
        self.session_request_error: str | None = None
        self._session_request_said_at: float | None = None

    # ── what the polling loop tells it ──

    async def polled(self, ok: bool, error: BaseException | None = None) -> None:
        now = self.now()
        if ok:
            self.last_poll_at = now
            if self._poll_failure_said:
                await self._say(f"The trader reaches the web server again: signals arrive since {_clock(now, self.timezone)}.")
            self.poll_failing_since, self.poll_error, self._poll_failure_said = None, None, False
            return
        if self.poll_failing_since is None:
            self.poll_failing_since = now
        self.poll_error = f"{type(error).__name__}: {error}" if error is not None else "unknown"
        if not self._poll_failure_said and now - self.poll_failing_since >= POLL_FAILING_ALERT_SECONDS:
            self._poll_failure_said = True
            await self._say(
                f"The trader cannot reach the web server since {_clock(self.poll_failing_since, self.timezone)}: "
                f"no signal can arrive. Last: {self.poll_error}"
            )

    def signal_received(self) -> None:
        self.last_signal_at = self.now()
        self._silence_said = False

    # ── what the keep-warm loop checks ──

    async def check(self, api_client=None) -> None:
        for check in (self.check_silence(), self.check_login(), self.check_session(api_client)):
            try:
                await check
            except Exception as e:
                logger.warning("Trader watch check failed: %r", e)

    async def check_silence(self) -> None:
        if self.signal_silence_seconds is None or self._silence_said:
            return
        now = self.now()
        if now - self.last_signal_at < self.signal_silence_seconds or not self.in_trading_hours():
            return
        self._silence_said = True
        await self._say(
            f"No signal for {int((now - self.last_signal_at) // 60)} min (the last at "
            f"{_clock(self.last_signal_at, self.timezone)}): TradingView's alerts may have stopped."
        )

    async def check_login(self) -> None:
        """Saxo's refusals to renew the login, and a login lapsing while they last."""
        if self.saxo_auth is None:
            return
        now = self.now()
        status = await asyncio.to_thread(self.saxo_auth.get_token_status)
        self.login_lapses_at = _epoch(status.get("refresh_token_expires_at"))
        error = getattr(self.saxo_auth, "last_refresh_error", None)
        if error is None:
            if self.login_failing_since is not None:
                await self._say("Saxo renews WATA's login again.")
            self.login_failing_since, self.login_error, self._login_said_at = None, None, None
            return
        if self.login_failing_since is None:
            self.login_failing_since = now
        self.login_error = error
        if self._login_said_at is not None and now - self._login_said_at < LOGIN_ALERT_EVERY_SECONDS:
            return
        left = None if self.login_lapses_at is None else self.login_lapses_at - now
        lapse = "" if left is None else (
            f" It lapses at {_clock(self.login_lapses_at, self.timezone)}"
            + (f", in {int(left // 60)} min" if 0 < left <= LOGIN_LAPSING_ALERT_SECONDS else "")
            + ": then nothing can be bought or sold until someone logs in again (watasaxoauth, or the admin's Saxo page)."
        )
        self._login_said_at = now
        await self._say(
            f"Saxo refuses to renew WATA's login since {_clock(self.login_failing_since, self.timezone)}: {error}. "
            f"No new position is taken meanwhile.{lapse}"
        )

    async def check_session(self, api_client) -> None:
        """
        The level Saxo serves WATA's session at, said when it is not the full one
        and when it changes; asked for when the configuration wants the full one.
        """
        if api_client is None:
            return
        now = self.now()
        if self._session_next_check is not None and now < self._session_next_check:
            return
        self._session_next_check = now + (FULL_SESSION_CHECK_EVERY_SECONDS if self.full_trading_level else SESSION_CHECK_EVERY_SECONDS)
        answer = await api_client.request(rs.sessions.GetSessionCapabilities()) or {}
        session = {"trade_level": answer.get("TradeLevel"), "data_level": answer.get("DataLevel"), "checked_at": moment(now)}
        before = self.session
        self.session = session
        if self.full_trading_level and session["trade_level"] != FULL_TRADE_LEVEL:
            await self._ask_full_level(api_client, now, before)
            return
        if before is not None and before.get("trade_level") == session["trade_level"]:
            return
        if session["trade_level"] == FULL_TRADE_LEVEL:
            if self.full_trading_level:
                await self._say(f"Saxo serves WATA's session at its full level ({FULL_TRADE_LEVEL}), as asked: real-time "
                                "prices. The account's other sessions (SaxoTraderGO) lose it while WATA holds it.")
            elif before is not None:
                await self._say(f"Saxo serves WATA's session at its full level again ({FULL_TRADE_LEVEL}).")
            return
        await self._say(
            f"Saxo serves WATA's session at {session['trade_level']} (data {session['data_level']}), not "
            f"{FULL_TRADE_LEVEL}: Saxo gives a session below the full level delayed prices, and an app logged in "
            "elsewhere (SaxoTraderGO) takes the full level. WATA buys no turbo on a quote Saxo says is delayed."
        )

    async def _ask_full_level(self, api_client, now: float, before: dict | None) -> None:
        """Ask Saxo for the full level; said when another session had taken it, or when Saxo refuses."""
        level = self.session["trade_level"]
        try:
            await api_client.request(rs.sessions.PatchSessionCapabilities({"TradeLevel": FULL_TRADE_LEVEL}))
        except Exception as e:
            self.session_request_error = f"{type(e).__name__}: {e}"
            logger.warning("Saxo refused the full session level: %s", self.session_request_error)
            if self._session_request_said_at is None or now - self._session_request_said_at >= SESSION_ALERT_EVERY_SECONDS:
                self._session_request_said_at = now
                await self._say(
                    f"WATA asked Saxo for the full session level ({FULL_TRADE_LEVEL}) and Saxo refused: "
                    f"{self.session_request_error}. The session stays at {level}: delayed prices."
                )
            return
        self.session_requested_at = now
        self.session_request_error = None
        self._session_request_said_at = None
        # Saxo applies it shortly after: read it back soon.
        self._session_next_check = now + SESSION_CONFIRM_SECONDS
        logger.info("Asked Saxo for the full session level (the session was at %s).", level)
        if before is not None and before.get("trade_level") == FULL_TRADE_LEVEL:
            await self._say(
                f"Another session of the account took the full level (SaxoTraderGO?): WATA's session was at {level}, "
                f"with delayed prices, at {_clock(now, self.timezone)}. WATA asked for it again."
            )

    # ── what the trader decides with it ──

    def login_refusal(self) -> str | None:
        """Why no entry is taken while Saxo refuses to renew the login; None otherwise."""
        if self.login_failing_since is None:
            return None
        return (f"Breaking trading rule: Saxo refuses to renew WATA's login since "
                f"{_clock(self.login_failing_since, self.timezone)}: no new position until it is renewed "
                f"({self.login_error}).")

    def figures(self) -> dict:
        return {
            "last_poll_at": moment(self.last_poll_at),
            "poll_failing_since": moment(self.poll_failing_since),
            "poll_error": self.poll_error,
            "last_signal_at": moment(self.last_signal_at),
            "signal_silence_minutes": None if self.signal_silence_seconds is None else self.signal_silence_seconds // 60,
            # Said on Telegram: no signal for longer than the setting, in trading hours.
            "signals_silent": self._silence_said,
            "saxo_login": {
                "renewal_failing_since": moment(self.login_failing_since),
                "error": self.login_error,
                "lapses_at": moment(self.login_lapses_at),
            },
            "saxo_session": None if self.session is None and not self.full_trading_level else {
                **(self.session or {}),
                "wanted": FULL_TRADE_LEVEL if self.full_trading_level else None,
                "requested_at": moment(self.session_requested_at),
                "request_error": self.session_request_error,
            },
        }

    async def _say(self, message: str) -> None:
        try:
            await self.send(message)
        except Exception as e:
            logger.warning("Could not say %r: %r", message, e)
