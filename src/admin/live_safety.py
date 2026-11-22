"""
A signal rule's protections, as the rule page's "Protections" and the go-live dialog list them: what keeps WATA's
account safe while the rule trades live, each with where it is set. The route (src/admin/routers/live_safety.py) reads
the facts; this module only says what they mean. Nothing here changes a setting, and nothing holds a rule back from
going live: the readiness does that (src/admin/ta_readiness.py).

Each protection is a check of ioiti-ui's ``CheckList``: ``pass`` (on, and able to act), ``open`` (off: the operator's
choice, said with what it would do, or not known), ``fail`` (something to see to before trusting it). Its ``link``
leads where it is set: a key of config.json (``config``, its catalog group and path, which the page turns into the
Configuration page's address) or a page (``href``).

What the account can buy (``funds_what_if``): the entries of the rule's backtest, or of its days on paper, sized again
at the account's value under the live settings by the backtests' own sizing (``position_scale``, ``units_for`` of
src/backtest/rules.py, which tests/test_backtest_parity.py holds to the trader's ``_calculate_bid_amount``). The
trader buys the cheapest turbo in range whatever the account holds (``select_best_price_candidate``), so an entry the
account cannot pay is refused for funds: a top-up, or fewer safety turbos, is what changes that.
"""

import math
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from src.admin.config_catalog import BLOCKS, lookup
from src.backtest.clock import TradingClock
from src.backtest.rules import available_funds, position_scale, units_for
from src.backtest.settings import BacktestSettings
from src.trade.account_guard import AccountGuard
from src.trade.day_breakers import DayBreakers
from src.trade.macro_calendar import MACRO_EVENTS, NewsRule
from src.trade.signal_sources import ENGINE, source_of

PASS, OPEN, FAIL = "pass", "open", "fail"
# The calendar of US releases is said to need keeping ahead this many days before its end, as health says.
CALENDAR_AHEAD_DAYS = 30
# The share of the entries the "most entries" figure of what the account needs covers, in percent.
MOST_ENTRIES = 90
# A rule's days on paper read for its entries when no backtest is linked to it.
PAPER_DAYS = 30
# The full level of a Saxo session (src/trade/trader_watch.py FULL_TRADE_LEVEL), below which its prices may be delayed.
FULL_LEVEL = "FullTradingAndChat"
FUNDS_REFUSALS = ("insufficient_funds", "funds_refused")
SOURCE_NAMES = {"tradingview": "TradingView's alerts", ENGINE: "WATA's engine"}

DAY_TRADING, RISK, MACRO = "day_trading", "risk_management", "macro_news"
MAX_DAY_LOSS = f"trade.rules[{DAY_TRADING}].rule_config.max_day_loss_percent"
LOSSES_IN_A_ROW = f"trade.rules[{RISK}].rule_config.max_consecutive_losses"
FLOOR = f"trade.rules[{RISK}].rule_config.pause_below_balance_eur"
EDGE_GUARD = f"trade.rules[{RISK}].rule_config.pause_when_edge_broken"
RELEASES = f"trade.rules[{MACRO}].rule_config.minutes_before"
SOURCES = "trade.config.signal_sources"
FULL_SESSION = "trade.config.saxo_session.full_trading_level"
SAFETY = "trade.config.buying_power.safety_margins.bid_calculation"


def config_link(path: str, *, whole_block: bool = False) -> dict | None:
    """
    Where a key of config.json is set: its catalog group and path (the page makes the address). ``whole_block``: its
    block's heading instead, which the page shows even while the rule the key belongs to is not in config.json.
    """
    param = lookup(path)
    if param is None:
        return None
    if whole_block:
        block = next((block for block in BLOCKS if block.group == param.group and any(
            path == start or path.startswith((f"{start}.", f"{start}[")) for start in block.paths)), None)
        if block is not None:
            return {"config": {"group": param.group, "path": param.path, "block": block.id}}
    return {"config": {"group": param.group, "path": param.path}}


def _check(check_id: str, label: str, state: str, detail: str, link: dict | None = None) -> dict:
    return {"id": check_id, "label": label, "state": state, "detail": detail, "link": link}


def not_read(check_id: str, label: str, error: str) -> dict:
    """A protection whose facts could not be read: said, never guessed."""
    return _check(check_id, label, OPEN, f"Not read: {error}")


def rule_config(config: dict, rule_type: str) -> dict | None:
    """A trading rule's settings in config.json; None when the rule is not there."""
    for rule in (config.get("trade") or {}).get("rules") or []:
        if isinstance(rule, dict) and rule.get("rule_type") == rule_type:
            return rule.get("rule_config") or {}
    return None


def _eur(value: float) -> str:
    return f"{value:,.2f} €"


def _number(value: float) -> str:
    return f"{value:g}"


# ── each protection ──


def signal_source(rule: dict, webhook: dict | None) -> dict:
    """Whether the rule's indice takes the engine's signals as the web server runs: without it, going live is refused."""
    label = "Signal source"
    link = config_link(SOURCES)
    indice = rule.get("indice") or "?"
    if webhook is None:
        return _check("signal_source", label, OPEN,
                      "The web server did not say where it takes each index's signals from (not reached, or older than "
                      "v0.48.0).", link)
    source = source_of(webhook.get("sources") or {}, indice)
    if source == ENGINE:
        return _check("signal_source", label, PASS,
                      f"The web server takes {indice}'s signals from WATA's engine and leaves TradingView's webhook "
                      f"aside.", link)
    return _check("signal_source", label, FAIL,
                  f"The web server takes {indice}'s signals from {SOURCE_NAMES.get(source, source)}: going live is "
                  f"refused until {indice} is set to the engine and the web server restarted.", link)


def saxo(status: dict | None, session: dict | None, wanted_full: bool) -> dict:
    """Saxo's login (the admin's ``saxo`` status) and the level it serves the services' session at (the trader's)."""
    label = "Saxo login and session"
    if status is None:
        return _check("saxo", label, OPEN, "Saxo's login was not read.", {"href": "/system"})
    if status.get("where") != "none" and (not status.get("has_token") or status.get("refresh_token_expired")):
        return _check("saxo", label, FAIL, "Saxo's login needs authorising again: nothing is traded until it is.",
                      {"href": "/system"})
    level = (session or {}).get("trade_level")
    if level and level != FULL_LEVEL:
        if wanted_full or (session or {}).get("wanted") == FULL_LEVEL:
            refused = f" ({session['request_error']})" if (session or {}).get("request_error") else ""
            return _check("saxo", label, FAIL,
                          f"Saxo serves WATA's session at {level}, though the trader asks for the full level{refused}: "
                          f"another session (SaxoTraderGO) may hold it.", config_link(FULL_SESSION))
        return _check("saxo", label, OPEN,
                      f"Authorised; Saxo serves WATA's session at {level}, below the full level, at which its prices "
                      f"come without delay: “Real-time prices” asks for it.", config_link(FULL_SESSION))
    if level:
        return _check("saxo", label, PASS, "Authorised; WATA's session is at Saxo's full level.", {"href": "/system"})
    return _check("saxo", label, PASS, "Authorised; the trader has not said its session's level yet.", {"href": "/system"})


def paused(since, by_account_guard, by_edge_guard, zone) -> dict:
    """Whether new entries are paused now, and by whom."""
    label = "New entries"
    if since is None:
        return _check("paused", label, PASS, "Not paused: each of the rule's entry signals reaches the trader's rules.",
                      {"href": "/"})
    moment = since if isinstance(since, datetime) else datetime.fromisoformat(str(since).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    when = moment.astimezone(zone).strftime("%d %b %H:%M")
    who = "the account guard" if by_account_guard else "the edge guard" if by_edge_guard else "you"
    return _check("paused", label, FAIL,
                  f"Paused by {who} since {when}: the rule's entry signals are refused until you resume entries from "
                  f"the dashboard.", {"href": "/"})


def day_limits(config: dict) -> dict:
    """The day's own limits: the loss after which no entry is taken, and the trades a day at most."""
    label = "The day's limits"
    day_trading = rule_config(config, DAY_TRADING) or {}
    risk = rule_config(config, RISK) or {}
    loss = day_trading.get("max_day_loss_percent")
    trades = int(risk.get("max_trades_per_day") or 0)
    cooldown = int(risk.get("cooldown_after_loss_minutes") or 0)
    others = [f"{trades} trades a day at most" if trades > 0 else "no limit on the trades of a day",
              f"{cooldown} min without an entry after a stop-loss" if cooldown > 0 else "no pause after a stop-loss"]
    if not isinstance(loss, (int, float)) or loss <= -100:
        return _check("day_limits", label, OPEN,
                      f"No loss caps the day: entries go on whatever the day lost; {'; '.join(others)}.",
                      config_link(MAX_DAY_LOSS))
    return _check("day_limits", label, PASS,
                  f"No new entry once the day is at {_number(loss)} %; {'; '.join(others)}.", config_link(MAX_DAY_LOSS))


def breakers(config: dict) -> dict:
    """The day's breakers (src/trade/day_breakers.py)."""
    label = "The day's breakers"
    risk = rule_config(config, RISK)
    link = config_link(LOSSES_IN_A_ROW, whole_block=risk is None)
    found = DayBreakers.from_config(risk)
    if not found.on:
        return _check("breakers", label, OPEN,
                      "Off: a run of losing trades, or a fall from the day's best, does not end the day's entries.", link)
    said = []
    if found.max_losses > 0:
        said.append(f"after {found.max_losses} losing trades in a row")
    if found.max_drawdown > 0:
        said.append(f"once the day is {_number(found.max_drawdown)} points under its best")
    return _check("breakers", label, PASS, f"No new entry for the rest of the day {' or '.join(said)}.", link)


def account_guard(config: dict, value: float | None) -> dict:
    """The account guard (src/trade/account_guard.py): a floor, and a fall from the highest since the last resume."""
    label = "Account guard"
    risk = rule_config(config, RISK)
    link = config_link(FLOOR, whole_block=risk is None)
    guard = AccountGuard.from_config(risk)
    if not guard.on:
        return _check("account_guard", label, OPEN, "Off: nothing pauses new entries as the account falls.", link)
    if guard.below_eur > 0 and value is not None and value < guard.below_eur:
        return _check("account_guard", label, FAIL,
                      f"Its floor ({_eur(guard.below_eur)}) is above the account's value ({_eur(value)}): it pauses new "
                      f"entries at the trader's next look.", link)
    said = []
    if guard.below_eur > 0:
        said.append(f"under {_eur(guard.below_eur)}")
    if guard.drawdown_percent > 0:
        said.append(f"{_number(guard.drawdown_percent)} % under its highest since entries were last resumed")
    return _check("account_guard", label, PASS, f"Pauses new entries once the account is {' or '.join(said)}.", link)


def _edge_unable(entry: dict, track: dict | None) -> str | None:
    """Why the edge guard could not pause entries on this rule's judgement, whatever its verdict; None when it could."""
    if track is None:
        return None
    verdict = track.get("verdict")
    if verdict == "no_model":
        reasons = track.get("reasons") or []
        return f"its reference cannot be drawn from ({reasons[0] if reasons else 'too few days or trades'})"
    if verdict == "no_edge_over_random":
        return "its reference does no better than random entries: there is no edge to watch"
    sample = ((entry.get("model") or {}).get("reference") or {}).get("sample") or track.get("sample")
    if sample == "too_few":
        return "its reference rests on too few trades or days (30 trades and 20 days at least)"
    if track.get("settings_differ"):
        return f"the live settings are not its reference's ({', '.join(track['settings_differ'][:4])})"
    return None


def edge_guard(rule: dict, *, enabled: bool, guard_on: bool, entry: dict | None, live_reference: bool,
               least_trades: int = 20, risk_rule: bool = True) -> dict:
    """
    The edge guard on this rule (src/admin/edge_watch_service.py): whether Edge Watch can judge its real trades against
    a reference of its own, and whether the guard would pause entries on a broken verdict. ``risk_rule``: whether
    config.json holds the risk_management rule its switch belongs to.
    """
    label = "Edge guard"
    switch = config_link(EDGE_GUARD, whole_block=not risk_rule)
    if not enabled:
        return _check("edge_guard", label, OPEN, "Edge Watch is not running: it needs the backtests switched on.", switch)
    rule_id = rule.get("id")
    if entry is None:
        meanwhile = (" Meanwhile its real trades are judged with WATA's other real trades against the live reference, "
                     "a replay of the signals WATA received: maybe another strategy's.") if live_reference else ""
        return _check("edge_guard", label, FAIL if guard_on else OPEN,
                      f"No reference run of the rule: its real trades cannot be judged against its own backtest. Make a "
                      f"run from the rule, then set it as its reference on the run's page.{meanwhile}",
                      {"href": f"/backtests/new?rule={rule_id}"})
    tracks = entry.get("tracks") or {}
    track = tracks.get("real") or tracks.get("paper")
    unable = _edge_unable(entry, track)
    reference = (entry.get("reference") or {}).get("name") or "its reference"
    live = rule.get("mode") == "live"
    so_far = ""
    if track is not None and live and "real" in tracks:
        so_far = f" {track.get('tested', track.get('n', 0))} of the {least_trades} real trades it waits for so far."
    if not guard_on:
        why = f" (and could not act on this rule yet: {unable})" if unable else ""
        return _check("edge_guard", label, OPEN,
                      f"Off: Edge Watch judges the rule's {'real' if live else 'paper'} trades against {reference} and "
                      f"says a broken verdict on Telegram, never acted on{why}.", switch)
    if unable:
        return _check("edge_guard", label, FAIL, f"On, but it cannot act on this rule: {unable}.",
                      {"href": f"/backtests/{(entry.get('reference') or {}).get('run_id')}"}
                      if (entry.get("reference") or {}).get("run_id") else switch)
    when = "its real trades" if live else "its real trades once it is live (paper trades meanwhile)"
    return _check("edge_guard", label, PASS,
                  f"On: a broken verdict on {when} against {reference}, with {least_trades} real trades or more, pauses "
                  f"new entries.{so_far}", switch)


def release_window(config: dict, known_until: date | None, readable: bool, today: date) -> dict:
    """The window around US releases (the macro_news rule) and its calendar's last known day."""
    label = "US releases"
    found = rule_config(config, MACRO)
    # Its rule absent, the page shows its block switched off, and no row of its keys.
    link = config_link(RELEASES, whole_block=found is None)
    if found is None:
        return _check("release_window", label, OPEN,
                      "Off: entries are taken through FOMC, CPI, NFP and PCE releases, and what is held is kept through "
                      "them.", link)
    try:
        news = NewsRule.from_config(found)
    except ValueError as e:
        return _check("release_window", label, FAIL, f"Its settings cannot be read: {e}.", link)
    if not readable:
        return _check("release_window", label, FAIL, "Its calendar of releases cannot be read: entries go on.", link)
    events = ", ".join(event for event in MACRO_EVENTS if event in news.events)
    held = (f"what is held is sold {news.flatten_minutes_before} min before" if news.flatten_minutes_before > 0
            else "what is held is kept through them")
    said = (f"No entry from {news.minutes_before} min before {events} to {news.minutes_after} min after "
            f"({news.minutes_after_fomc} after an FOMC statement); {held}")
    if known_until is None or known_until < today + timedelta(days=CALENDAR_AHEAD_DAYS):
        until = known_until.isoformat() if known_until else "no day"
        return _check("release_window", label, FAIL,
                      f"{said}. Its calendar is known until {until}: past it, entries go on through releases.", link)
    return _check("release_window", label, PASS, f"{said}. Its calendar is known until {known_until.isoformat()}.", link)


def telegram(bot_set: bool | None) -> dict:
    """Whether WATA can say what its guards do: their pauses, refused sales, Edge Watch's verdicts."""
    label = "Telegram"
    link = {"href": "/config/credentials"}
    if bot_set is None:
        return _check("telegram", label, OPEN, "Whether a Telegram bot is set could not be told.", link)
    if not bot_set:
        return _check("telegram", label, FAIL,
                      "No Telegram bot: the guards' pauses, refused sales and Edge Watch's verdicts reach nobody.", link)
    return _check("telegram", label, PASS,
                  "Set: the guards' pauses, refused sales and Edge Watch's verdicts are said there.", link)


# ── what the account can buy ──


def _epoch(value) -> float | None:
    if not value:
        return None
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def entries_of(trades: list[dict], signals: list[dict]) -> list[dict]:
    """
    A run's entries as the trader chose their turbo: each trade's ask it was sized on, and each signal refused for
    funds with the ask of the turbo chosen, the moment and the confidence; oldest first.
    """
    found = []
    for trade in trades:
        entry = trade.get("entry") or {}
        ask = entry.get("selected_ask", entry.get("ask"))
        at = _epoch(entry.get("selected_at") or entry.get("decided_at") or entry.get("filled_at"))
        if isinstance(ask, (int, float)) and ask > 0 and at is not None:
            found.append({"at": at, "ask": float(ask), "confidence": trade.get("confidence")})
    for signal in signals:
        if signal.get("rule") not in FUNDS_REFUSALS:
            continue
        chosen = next((candidate for candidate in signal.get("candidates") or [] if candidate.get("chosen")), None)
        ask = (chosen or {}).get("ask")
        at = _epoch(signal.get("decided_at"))
        if isinstance(ask, (int, float)) and ask > 0 and at is not None:
            found.append({"at": at, "ask": float(ask), "confidence": signal.get("confidence")})
    return sorted(found, key=lambda entry: entry["at"])


def _cents_up(value: float) -> float:
    return math.ceil(value * 100 - 1e-9) / 100


def funds_what_if(settings: BacktestSettings, value: float | None, entries: list[dict], source: dict | None) -> dict:
    """
    What the account's value buys under the live settings: the dearest ask it pays at full scale, and of ``entries``
    (``entries_of``) how many it buys now, how many without the safety turbos, and the value that buys all of them and
    ``MOST_ENTRIES`` % of them.
    """
    share = max(0.0, settings.funds_percent - settings.reserve_cash_percent) / 100
    safety = settings.safety_units
    clock = TradingClock(settings.timezone)
    without_safety = replace(settings, safety_units=0)
    needs, affordable, affordable_without = [], 0, 0
    for entry in entries:
        scale = position_scale(settings, clock.minute_of_day(entry["at"]), entry.get("confidence"))
        needs.append(math.inf if share * scale <= 0 else entry["ask"] * (1 + safety) / (share * scale))
        if value is not None:
            affordable += units_for(settings, value, entry["ask"], scale) > 0
            affordable_without += units_for(without_safety, value, entry["ask"], scale) > 0
    finite = sorted(need for need in needs if math.isfinite(need))
    most = finite[max(0, math.ceil(len(finite) * MOST_ENTRIES / 100) - 1)] if finite else None
    return {
        "value": value,
        "share_percent": round(share * 100, 2),
        "safety_units": safety,
        # The dearest ask an order at full scale pays (the trader's ``ask × (1 + safety) <= available``).
        "dearest_ask": None if value is None else round(available_funds(settings, value, 1.0) / (1 + safety), 2),
        "entries": len(entries),
        "affordable": affordable if value is not None else None,
        "affordable_without_safety": affordable_without if value is not None else None,
        "needed_for_all": _cents_up(finite[-1]) if finite else None,
        "needed_for_most": _cents_up(most) if most is not None else None,
        "most_percent": MOST_ENTRIES,
        "dearest_entry_ask": max((entry["ask"] for entry in entries), default=None),
        "source": source,
    }


def funds(what_if: dict | None) -> dict:
    """The funds check: whether the account buys the entries its backtest (or its paper days) took."""
    label = "What the account buys"
    link = config_link(SAFETY)
    if what_if is None:
        return _check("funds", label, OPEN, "Not read.", link)
    value = what_if.get("value")
    if value is None:
        return _check("funds", label, OPEN, "The account's value is not known yet: no daily report recorded it.", link)
    dearest = what_if.get("dearest_ask")
    head = f"At {_eur(value)}, an order at full scale buys a turbo of {_eur(dearest)} at most"
    source = what_if.get("source") or {}
    total = what_if.get("entries") or 0
    if total == 0:
        return _check("funds", label, OPEN,
                      f"{head}. No entry to set it against: link a backtest of the rule (Live, paper, backtest), or "
                      f"wait for its days on paper.", link)
    named = source.get("name") or ("its days on paper" if source.get("kind") == "paper" else "its backtest")
    affordable = what_if.get("affordable") or 0
    if affordable >= total:
        return _check("funds", label, PASS, f"{head}: every one of the {total} entries of {named}.", link)
    without = what_if.get("affordable_without_safety") or 0
    safety = what_if.get("safety_units") or 0
    without_said = (f"; with no safety turbo, {without}" if safety > 0 else "")
    others = "the other" if total - affordable == 1 else "the others"
    needed, most = what_if.get("needed_for_all"), what_if.get("needed_for_most")
    most_said = (f", {what_if['most_percent']} % of them from {_eur(most)}" if most is not None and needed is not None
                 and most < needed else "")
    needed_said = f" An account of {_eur(needed)} buys them all{most_said}." if needed is not None else ""
    return _check("funds", label, FAIL,
                  f"{head}: {affordable} of the {total} entries of {named}, {others} refused for funds{without_said}."
                  f"{needed_said}", link)


# ── all of them ──


def protections(checks: dict[str, dict]) -> list[dict]:
    """The checks in the order the page lists them: what keeps entries out now first, then the guards, then alerts."""
    order = ("signal_source", "saxo", "paused", "funds", "day_limits", "breakers", "account_guard", "edge_guard",
             "release_window", "telegram")
    return [checks[check_id] for check_id in order if check_id in checks]
