"""The trading rules and the trading settings of trade.config: what is bought, when, how much, and when it is sold."""

from src.admin.config_catalog.model import SERVER, TRADING, UNUSED, Column, Param
from src.trade.macro_calendar import DEFAULT_EVENTS, MACRO_EVENTS
from src.trade.signal_sources import SOURCES

# Services by the ids of their health entries.
TRADER, MONITOR, SCHEDULER, WATCHLIST, RECORDER, ADMIN, TELEGRAM = (
    "trader", "position_monitor", "scheduler", "watchlist_manager", "price_recorder", "admin", "telegram",
)


def rule(rule_type: str, key: str) -> str:
    return f"trade.rules[{rule_type}].rule_config.{key}"


TIMEZONE = "trade.config.general.timezone"
MARKET_TIMEZONE = rule("market_hours", "timezone")
START_HOUR = rule("market_hours", "trading_start_hour")
END_HOUR = rule("market_hours", "trading_end_hour")
RISKY_HOUR = rule("market_hours", "risky_trading_start_hour")
RISKY_MINUTE = rule("market_hours", "risky_trading_start_minute")
CLOSE_TIME = rule("day_trading", "close_position_time")
DAY_TARGET = rule("day_trading", "percent_profit_wanted_per_days")
NO_ENTRY_ABOVE = rule("day_trading", "dont_enter_trade_if_day_profit_is_more_than")
NO_ENTRY_BELOW = rule("day_trading", "max_day_loss_percent")
STOPLOSS = "trade.config.position_management.performance_thresholds.stoploss_percent"
MAX_PROFIT = "trade.config.position_management.performance_thresholds.max_profit_percent"
TRAILING = "trade.config.position_management.performance_thresholds.trailing_stop"
PROTECTIVE = "trade.config.position_management.protective_stop"
COOLDOWN = rule("risk_management", "cooldown_after_loss_minutes")
SIZING = "trade.config.position_sizing"
BUYING_POWER = "trade.config.buying_power"
GENERAL = "trade.config.general"
POLLING = "trade.config.signal_polling"
WATCHLIST_SECTION = "trade.config.watchlist_manager"

UTC_DAY = "The day is counted on the server's clock, in UTC: the trading day of the US session lies within it."
DASHBOARD_AT_START = "The dashboard shows it as the admin read it at its start: restart the admin to see a change there."
PROTECTED = "It says where a service connects or listens: whoever could change it could point the trader at another server."
ON_THE_SERVER = "Edit /app/wata/etc/config.json on the server, then restart the services that read it."

RULE_NAME = Param(
    path="trade.rules[*].rule_name", group="hours", label="Name of a rule", kind="text",
    help="A name for the rule, shown in the validator's messages. The services find a rule by its type, never by its name.",
    readers=(), risk="none",
)

def required_rule(rule_type: str, group: str, label: str, help_text: str, readers: tuple[str, ...]) -> Param:
    """A rule the validator requires: no switch, only a header for its keys on the page."""
    return Param(path=f"trade.rules[{rule_type}]", group=group, label=label, kind="rule", help=help_text,
                 readers=readers, risk=TRADING)


DAY = (
    required_rule("day_trading", "day", "Day trading",
                  "The day's target and limits, and the close of the day.", (TRADER, MONITOR, SCHEDULER, ADMIN)),
    Param(
        path=DAY_TARGET, group="day", label="Day's profit target", kind="number", unit="%", minimum=0, maximum=100,
        help="The day's result at which every open position is sold.",
        readers=(MONITOR, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        effects=(
            "The position monitor sells every open position once the day's result, counting what is open, reaches it: "
            "(1 + day %) × (1 + position %) − 1 ≥ target.",
            "The dashboard shows the day's progress against it.",
        ),
        related=(NO_ENTRY_ABOVE, NO_ENTRY_BELOW, "trade.config.position_management.wait_for_fresh_quote"),
        caveats=(UTC_DAY, "A position just bought is not judged on a quote older than its purchase, so the target is "
                          "not met on a bid from before it (wait_for_fresh_quote).", DASHBOARD_AT_START),
    ),
    Param(
        path=NO_ENTRY_ABOVE, group="day", label="No entry above this day's result", kind="number", unit="%",
        minimum=0, maximum=100,
        help="Once the day's result is at or above it, no new position is taken.",
        readers=(TRADER, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        effects=("The trader refuses long and short signals; closing signals still go through.",),
        related=(DAY_TARGET, NO_ENTRY_BELOW),
        caveats=(UTC_DAY,),
    ),
    Param(
        path=NO_ENTRY_BELOW, group="day", label="No entry below this day's loss", kind="number", unit="%",
        minimum=-100, maximum=0,
        help="Once the day's result is at or below it, no new position is taken.",
        readers=(TRADER, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        effects=("The trader refuses long and short signals.",
                 "Nothing is sold for it: each position's own stop does that."),
        related=(DAY_TARGET, NO_ENTRY_ABOVE, STOPLOSS),
        caveats=(UTC_DAY,),
    ),
    Param(
        path="trade.rules[risk_management]", group="day", label="Risk limits", kind="rule", optional=True,
        help="A pause after a loss, a limit on the trades of a day, the day's breakers and the account guard. "
             "Absent, none applies.",
        readers=(TRADER, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        rule_default={"cooldown_after_loss_minutes": 0, "max_trades_per_day": 0},
        effects=("The trader refuses entries during the pause, once the day's trades are made, after the losses or "
                 "the fall the breakers allow, and pauses them when the account guard trips.",),
    ),
    Param(
        path=rule("risk_management", "max_consecutive_losses"), group="day", label="Stop the day after losses in a row",
        kind="int", minimum=0, optional=True, default=0,
        help="Once this many of the day's trades lost one after the other, no new position until tomorrow. 0: off.",
        readers=(TRADER, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        effects=("The trader reads the day's closed positions in the order they closed and refuses entries once "
                 "the last ones lost that many times in a row.",),
        related=(rule("risk_management", "max_drawdown_from_day_best_percent"), NO_ENTRY_BELOW),
        caveats=(UTC_DAY, "A trade at exactly 0 % breaks the row, as a win does."),
    ),
    Param(
        path=rule("risk_management", "max_drawdown_from_day_best_percent"), group="day",
        label="Stop the day this far under its best", kind="number", unit="points", minimum=0, maximum=1000,
        optional=True, default=0,
        help="Once the day's result is this many points under the best it reached at a close (0 before the first), "
             "no new position until tomorrow. 0: off.",
        readers=(TRADER, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        effects=("The trader compounds the day's closed results as the day's result is counted, and refuses "
                 "entries once it is that far under its best.",),
        related=(NO_ENTRY_BELOW, rule("risk_management", "max_consecutive_losses")),
        caveats=(UTC_DAY, "From the start of the day it works as a day loss limit: the day's best is 0 until a "
                          "trade wins."),
    ),
    Param(
        path=rule("risk_management", "pause_below_balance_eur"), group="day", label="Pause entries under",
        kind="number", unit="€", minimum=0, optional=True, default=0,
        help="Once the account is worth less than this, new entries are paused until you resume them. 0: off.",
        readers=(TRADER, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        effects=("The trader reads the account's value with its balance (every 30 s and before an entry) and, under "
                 "the floor, pauses new entries as the dashboard's pause does, said on Telegram.",),
        related=(rule("risk_management", "pause_after_drawdown_percent"),),
        caveats=("The pause stays until you resume entries from the dashboard: with the account still under the "
                 "floor, the guard pauses them again at once.",),
    ),
    Param(
        path=rule("risk_management", "pause_after_drawdown_percent"), group="day",
        label="Pause entries this far under the account's highest", kind="number", unit="%", minimum=0, maximum=100,
        optional=True, default=0,
        help="Once the account is worth this much less than its highest since entries were last resumed, new entries "
             "are paused until you resume them. 0: off.",
        readers=(TRADER, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        effects=("The highest is the larger of the daily values the report records since the last resume and the "
                 "values the trader read since its start.",),
        related=(rule("risk_management", "pause_below_balance_eur"),),
        caveats=("A deposit raises the highest; a withdrawal reads as a fall.",
                 "Resuming entries starts the highest afresh."),
    ),
    Param(
        path=COOLDOWN, group="day", label="Pause after a loss", kind="int", unit="min", minimum=0, optional=True,
        default=0,
        help="After a sale at the stop-loss, no new position for this long. 0: no pause.",
        readers=(TRADER, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        effects=("The position monitor writes down each sale at the stop-loss; the trader refuses entries until the "
                 "pause after the last one is over.",),
        related=(STOPLOSS,),
        caveats=("Only a sale at the stop-loss starts it: not a trailing stop, the day's target or a close by signal.",),
    ),
    Param(
        path=rule("risk_management", "max_trades_per_day"), group="day", label="Trades a day at most", kind="int",
        minimum=0, optional=True, default=0,
        help="Once this many positions were opened today, no new one. 0: no limit.",
        readers=(TRADER, TELEGRAM, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        effects=("The trader counts the positions opened today and refuses entries past the limit.",),
        caveats=(UTC_DAY,),
    ),
)

HOURS = (
    Param(
        path=TIMEZONE, group="hours", label="Trading timezone", kind="timezone", optional=True, default="Europe/Paris",
        help="The timezone of the days WATA counts and of the hours it keeps, unless the market hours name another.",
        readers=(TRADER, MONITOR, SCHEDULER, RECORDER, "capital_feed", TELEGRAM, ADMIN),
        consumers=("dashboard", "backtests", "paper", "research_export"), risk=TRADING,
        effects=(
            "The market hours below and the close time are read in it, unless they name a timezone of their own.",
            "The scheduler's daily report (22:00) and the calendar's morning check (09:00) are at its hours.",
            "The days of the recorder's and the feed's files, of the research export and of paper days are its days.",
            "The time-of-day sizing periods are read in it.",
            "The admin shows times in it, and the bot's commands too.",
        ),
        related=(MARKET_TIMEZONE, f"{SIZING}.time_of_day_scaling.periods"),
    ),
    required_rule("market_hours", "hours", "Market hours",
                  "When new positions are taken, and in which timezone the hours are written.",
                  (TRADER, MONITOR, SCHEDULER, RECORDER, ADMIN)),
    Param(
        path=START_HOUR, group="hours", label="Trading starts at", kind="int", unit="h", minimum=0, maximum=23,
        help="The hour from which new positions are taken.",
        readers=(TRADER, MONITOR, SCHEDULER, RECORDER, ADMIN),
        consumers=("dashboard", "backtests", "downloads", "paper", "research_export"), risk=TRADING,
        effects=(
            "The trader takes entries from this hour.",
            "After the day's close, the position monitor keeps selling what is held until this hour of the next day.",
            "The price recorder expects quotes from then: silence before it is not an incident.",
            "Dukascopy's keeper, paper days and the research export only run outside these hours.",
        ),
        related=(END_HOUR, MARKET_TIMEZONE, RISKY_HOUR),
        caveats=("Read in the market's timezone (below), the trading timezone unless it names one.",),
    ),
    Param(
        path=END_HOUR, group="hours", label="Trading ends at", kind="int", unit="h", minimum=0, maximum=23,
        help="From this hour on, no new position. It must be after the start.",
        readers=(TRADER, MONITOR, SCHEDULER, RECORDER, ADMIN),
        consumers=("dashboard", "backtests", "downloads", "paper", "research_export"), risk=TRADING,
        effects=(
            "The trader takes no entry from this hour to the end of the day.",
            "On an early close of the US calendar, sales come as long before the early close as the close time is "
            "before this hour.",
        ),
        related=(START_HOUR, CLOSE_TIME, MARKET_TIMEZONE),
        caveats=("Positions are not sold at this hour: they are at the close time.",),
    ),
    Param(
        path=RISKY_HOUR, group="hours", label="Last minutes from (hour)", kind="int", unit="h", minimum=0, maximum=23,
        help="With the minute below: from then to the end of the day, no new position.",
        readers=(TRADER, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        effects=("The trader refuses entries from this hour and minute on: a position bought then would barely be held.",
                 "On an early close, entries stop as many minutes before it."),
        related=(RISKY_MINUTE, END_HOUR, CLOSE_TIME),
        caveats=("It must be within the trading hours.",),
    ),
    Param(
        path=RISKY_MINUTE, group="hours", label="Last minutes from (minute)", kind="int", unit="min", minimum=0,
        maximum=59,
        help="The minute of the hour above.",
        readers=(TRADER, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        related=(RISKY_HOUR,),
    ),
    Param(
        path=MARKET_TIMEZONE, group="hours", label="Market's timezone", kind="timezone", optional=True, default=None,
        help="The timezone the trading hours and the close time are written in. Empty: the trading timezone.",
        readers=(TRADER, MONITOR, SCHEDULER, RECORDER, ADMIN),
        consumers=("dashboard", "backtests", "downloads", "paper", "research_export"), risk=TRADING,
        effects=("With America/New_York, the US session keeps its hours in the weeks Europe and the US change clocks "
                 "apart.",),
        related=(TIMEZONE, START_HOUR, END_HOUR, CLOSE_TIME),
        caveats=("Backtests honour America/New_York and Europe/Paris only: another falls back to the trading timezone.",
                 "In the weeks the clocks differ, 02:00 in New York is 07:00 in Paris, an hour before Saxo quotes the "
                 "turbos: an entry then finds none."),
    ),
    Param(
        path=CLOSE_TIME, group="hours", label="Close of the day", kind="time",
        help="When every position still open is sold.",
        readers=(SCHEDULER, MONITOR, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        effects=(
            "The scheduler sends a close of every position at this time, in the market's timezone.",
            "From one minute after it to the next day's start, the position monitor sells whatever is still held.",
            "On an early close of the US calendar, both come earlier, as long before the early close as this time is "
            "before the trading end.",
        ),
        related=(END_HOUR, MARKET_TIMEZONE, RISKY_HOUR),
        caveats=("Two digits for the hour (09:30): the scheduler reads HH:MM.",
                 "A scheduler restarted within a minute of it misses that day's close; the monitor still sells "
                 "a minute later."),
    ),
    required_rule("market_closed_dates", "hours", "Closed dates", "Days without trading.", (TRADER, RECORDER, ADMIN)),
    Param(
        path=rule("market_closed_dates", "market_closed_dates"), group="hours", label="Market closed on", kind="dates",
        help="Days without trading, on top of the US market calendar kept with WATA.",
        readers=(TRADER, RECORDER, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        effects=("The trader takes no entry on these days (in the market's timezone).",
                 "The price recorder takes no silence on them for an incident."),
        caveats=("The NYSE's holidays and early closes are already in src/trade/data/us_market_calendar.csv: list "
                 "here only what it lacks.", "Written DD/MM/YYYY."),
    ),
    Param(
        path="trade.rules[macro_news]", group="hours", label="No entry around US releases", kind="rule", optional=True,
        help="No new position from shortly before to shortly after each release of the calendar kept with WATA.",
        readers=(TRADER, MONITOR, SCHEDULER, TELEGRAM, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        rule_default={"events": ["FOMC", "CPI", "NFP", "PCE"], "minutes_before": 5, "minutes_after": 15,
                      "minutes_after_fomc": 45, "flatten_minutes_before": 0},
        effects=("The trader refuses entries in each window (New York's hours, whatever the clocks).",
                 "The scheduler says each morning when the calendar runs out within 30 days."),
        caveats=("Past the calendar's last day nothing is known, and entries go on.",
                 "PPI, RETAIL, GDP and JOLTS are kept from late 2026 on: a backtest of earlier days that names them "
                 "says so. ISM's manufacturing index is not kept: its dated calendar is for ISM's members."),
    ),
    Param(
        path=rule("macro_news", "events"), group="hours", label="Releases", kind="strings",
        choices=MACRO_EVENTS, optional=True, default=list(DEFAULT_EVENTS),
        help="Which releases have a window: FOMC statements, CPI, the jobs report (NFP) and PCE unless it names others. "
             "Producer prices (PPI), retail sales (RETAIL), the GDP's advance estimate (GDP) and job openings (JOLTS) "
             "apply once named.",
        readers=(TRADER, MONITOR, SCHEDULER, TELEGRAM, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
    ),
    Param(
        path=rule("macro_news", "minutes_before"), group="hours", label="From before a release", kind="int",
        unit="min", minimum=0, maximum=120, optional=True, default=5,
        help="No entry from this long before a release.",
        readers=(TRADER, TELEGRAM, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
    ),
    Param(
        path=rule("macro_news", "minutes_after"), group="hours", label="Until after it", kind="int", unit="min",
        minimum=0, maximum=120, optional=True, default=15,
        help="No entry until this long after a release.",
        readers=(TRADER, MONITOR, TELEGRAM, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
    ),
    Param(
        path=rule("macro_news", "minutes_after_fomc"), group="hours", label="Until after an FOMC statement",
        kind="int", unit="min", minimum=0, maximum=180, optional=True, default=45,
        help="The same after an FOMC statement, whose press conference starts 30 minutes later.",
        readers=(TRADER, MONITOR, TELEGRAM, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
    ),
    Param(
        path=rule("macro_news", "flatten_minutes_before"), group="hours", label="Sell what is held, from before",
        kind="int", unit="min", minimum=0, maximum=120, optional=True, default=0,
        help="From this long before a release until its window ends, the position monitor sells what is held. "
             "0 keeps open positions.",
        readers=(TRADER, MONITOR, TELEGRAM, ADMIN), consumers=("dashboard", "backtests"), risk=TRADING,
        effects=("Entries are refused from the earlier of the two starts: a position bought then would be sold at once.",),
        caveats=("Around a release Saxo can stop quoting a turbo for minutes, when no stop can be sold: a long held "
                 "through the CPI of 14 October 2026, unquoted for 250 s, was sold at −30 %.",),
    ),
    RULE_NAME,
)

EXITS = (
    Param(
        path=STOPLOSS, group="exits", label="Stop-loss", kind="number", unit="%",
        help="A position whose result falls to it is sold.",
        readers=(MONITOR,), consumers=("backtests",), risk=TRADING,
        effects=("The position monitor sells it at once.",
                 "A sale at the stop starts the pause after a loss, if the risk limits have one."),
        related=(MAX_PROFIT, COOLDOWN, f"{PROTECTIVE}.percent"),
        caveats=("It must be below the take-profit.", "Judged on Saxo's quote of the turbo, which comes about every "
                 "30 s: the sale fills at the live price, often further."),
    ),
    Param(
        path=MAX_PROFIT, group="exits", label="Take-profit", kind="number", unit="%",
        help="A position whose result reaches it is sold.",
        readers=(MONITOR,), consumers=("backtests",), risk=TRADING,
        effects=("The position monitor sells it at once.",),
        related=(STOPLOSS, f"{TRAILING}.activation_percent"),
    ),
    Param(
        path=f"{TRAILING}.enabled", group="exits", label="Trailing stop", kind="bool", optional=True, default=False,
        help="Once a position has gained enough, it is sold when it gives back part of its best result.",
        readers=(MONITOR,), consumers=("backtests",), risk=TRADING,
        related=(f"{TRAILING}.activation_percent", f"{TRAILING}.drawdown_percent"),
    ),
    Param(
        path=f"{TRAILING}.activation_percent", group="exits", label="Trailing stop arms at", kind="number", unit="%",
        optional=True, default=5,
        help="The best result from which the trailing stop watches the position.",
        readers=(MONITOR,), consumers=("backtests",), risk=TRADING,
        related=(f"{TRAILING}.drawdown_percent", MAX_PROFIT),
        caveats=("At or above the take-profit, it never acts: the position is sold before.",),
    ),
    Param(
        path=f"{TRAILING}.drawdown_percent", group="exits", label="Trailing stop sells after giving back",
        kind="number", unit="points", optional=True, default=8,
        help="Sold when the best result less the result now reaches it, in percentage points.",
        readers=(MONITOR,), consumers=("backtests",), risk=TRADING,
        related=(f"{TRAILING}.activation_percent",),
    ),
    Param(
        path=f"{PROTECTIVE}.enabled", group="exits", label="Stop placed at Saxo", kind="bool", optional=True,
        default=False,
        help="A stop order placed at Saxo with each new position, which acts while WATA cannot sell.",
        readers=(TRADER,), risk=TRADING,
        effects=("The trader places it right after each purchase; every sale cancels it first.",),
        related=(f"{PROTECTIVE}.percent", STOPLOSS),
        caveats=("Not yet tried against Saxo.",),
    ),
    Param(
        path=f"{PROTECTIVE}.percent", group="exits", label="Stop at Saxo, at", kind="number", unit="%",
        optional=True,
        help="Its price: the opening price less this. Above −100 % and below the stop-loss, so that it only acts "
             "when the position monitor did not.",
        readers=(TRADER,), risk=TRADING,
        related=(STOPLOSS,),
        caveats=("Required while the stop at Saxo is on.",),
    ),
    Param(
        path=f"{PROTECTIVE}.order_type", group="exits", label="Order type of the stop at Saxo", kind="text",
        optional=True, default="StopIfTraded",
        help="Saxo's name of the order type; it must be one the turbo supports.",
        readers=(TRADER,), risk=TRADING,
    ),
    Param(
        path="trade.config.position_management.wait_for_fresh_quote", group="exits",
        label="Judge a new position on Saxo's next quote", kind="bool", optional=True, default=True,
        help="A position seen within two minutes of its opening is not judged until its quote changes.",
        readers=(MONITOR,), consumers=("backtests",), risk=TRADING,
        effects=("No stop, take-profit, trailing stop or day's target on a bid older than the purchase; the day's "
                 "close and a sale before a release are not held back.",),
        caveats=("On 19 October 2026 a stop was 'hit at −8.47 %' on a bid 17 to 27 s older than the purchase; the "
                 "sale filled at +0.51 %.",),
    ),
)

SIZE = (
    Param(
        path=f"{BUYING_POWER}.max_account_funds_to_use_percentage", group="sizing", label="Share of the account used",
        kind="number", unit="%", minimum=1, maximum=100,
        help="The share of Saxo's spending power a new position may use.",
        readers=(TRADER,), consumers=("backtests",), risk=TRADING,
        related=(f"{BUYING_POWER}.reserve_cash_percent", f"{SIZING}.time_of_day_scaling.enabled"),
    ),
    Param(
        path=f"{BUYING_POWER}.reserve_cash_percent", group="sizing", label="Cash kept aside", kind="number", unit="%",
        minimum=0, optional=True, default=0,
        help="Taken from the share above: what is used is the share less this.",
        readers=(TRADER,), consumers=("backtests",), risk=TRADING,
        effects=("Above 0 the trader buys the new position first and sells the other side after, instead of selling "
                 "first and buying on the proceeds.",),
    ),
    Param(
        path=f"{BUYING_POWER}.balance_cache_max_age_seconds", group="sizing", label="Spending power reused for",
        kind="number", unit="s", minimum=0, optional=True, default=0,
        help="How long the trader reuses Saxo's spending power before asking again. 0: asked each time.",
        readers=(TRADER,), risk=TRADING,
    ),
    Param(
        path=f"{BUYING_POWER}.safety_margins.bid_calculation", group="sizing", label="Turbos left out of each order",
        kind="int", minimum=0,
        help="Amount = floor(available ÷ ask − this); nothing is bought unless available ≥ ask × (1 + this).",
        readers=(TRADER,), consumers=("backtests",), risk=TRADING,
    ),
    Param(
        path=f"{SIZING}.time_of_day_scaling.enabled", group="sizing", label="Size by time of day", kind="bool",
        optional=True, default=False,
        help="A position's size scaled by the period of the day it is taken in.",
        readers=(TRADER,), consumers=("backtests",), risk=TRADING,
        related=(f"{SIZING}.time_of_day_scaling.periods",),
    ),
    Param(
        path=f"{SIZING}.time_of_day_scaling.periods", group="sizing", label="Periods of the day", kind="rows",
        optional=True, default=[],
        help="The first period holding the moment of an entry gives its size; outside them, 100 %.",
        readers=(TRADER,), consumers=("backtests",), risk=TRADING,
        columns=(
            Column("label", "Name", "text"),
            Column("start_hour", "From (hour)", "int", minimum=0, maximum=23),
            Column("start_minute", "From (min)", "int", minimum=0, maximum=59),
            Column("end_hour", "To (hour)", "int", minimum=0, maximum=24),
            Column("end_minute", "To (min)", "int", minimum=0, maximum=59),
            Column("scale_percent", "Size", "number", unit="%", minimum=0),
        ),
        related=(TIMEZONE,),
        caveats=("Read in the trading timezone, not the market's.",),
    ),
    Param(
        path=f"{SIZING}.confidence_scaling.enabled", group="sizing", label="Size by the signal's confidence",
        kind="bool", optional=True, default=False,
        help="A position's size scaled by the confidence its signal carries; signals below the threshold refused.",
        readers=(TRADER,), consumers=("backtests",), risk=TRADING,
        related=(f"{SIZING}.confidence_scaling.scaling_rules", f"{SIZING}.confidence_scaling.min_confidence_threshold"),
    ),
    Param(
        path=f"{SIZING}.confidence_scaling.default_confidence", group="sizing", label="Confidence of a signal without one",
        kind="number", minimum=0, optional=True, default=1.0,
        help="Taken for a signal that carries no confidence.",
        readers=(TRADER,), consumers=("backtests",), risk=TRADING,
    ),
    Param(
        path=f"{SIZING}.confidence_scaling.min_confidence_threshold", group="sizing", label="Lowest confidence taken",
        kind="number", minimum=0, optional=True, default=0.0,
        help="A signal of lower confidence is refused, while sizing by confidence is on.",
        readers=(TRADER,), consumers=("backtests",), risk=TRADING,
        caveats=("A webhook's confidence is between 0 and 2.",),
    ),
    Param(
        path=f"{SIZING}.confidence_scaling.scaling_rules", group="sizing", label="Bands of confidence", kind="rows",
        optional=True, default=[],
        help="A signal whose confidence is in a band (from, below) gets its size; outside them, 100 %.",
        readers=(TRADER,), consumers=("backtests",), risk=TRADING,
        columns=(
            Column("label", "Name", "text"),
            Column("min_confidence", "Confidence from", "number", minimum=0),
            Column("max_confidence", "Confidence below", "number", minimum=0),
            Column("scale_percent", "Size", "number", unit="%", minimum=0),
        ),
    ),
    Param(
        path="trade.config.turbo_preference.exchange_id", group="sizing", label="Exchange of the turbos", kind="text",
        help="The exchange whose turbos are searched.",
        readers=(TRADER, WATCHLIST, RECORDER), risk=TRADING,
        effects=("The trader, the watchlist manager and the price recorder look for turbos there only.",),
    ),
    Param(
        path="trade.config.turbo_preference.price_range.min", group="sizing", label="Turbo's price from", kind="number",
        unit="€", minimum=0,
        help="Only turbos whose ask is within the range are bought.",
        readers=(TRADER, WATCHLIST), consumers=("backtests",), risk=TRADING,
        related=("trade.config.turbo_preference.price_range.max",),
        caveats=("It must be below the upper bound.",),
    ),
    Param(
        path="trade.config.turbo_preference.max_spread_percent", group="sizing", label="Widest spread bought",
        kind="number", unit="% of the ask", minimum=0, maximum=100, optional=True, default=None,
        help="No turbo whose spread is wider than this share of its ask is bought. Empty or 0: no limit.",
        readers=(TRADER, WATCHLIST), consumers=("backtests",), risk=TRADING,
        effects=("Of the turbos in the price range, those with a wider spread are left out before the cheapest is "
                 "chosen; with none left, the entry fails and says the narrowest spread seen.",),
        related=("trade.config.turbo_preference.price_range.min", "trade.config.turbo_preference.price_range.max"),
        caveats=("The cheapest turbo of the range, the one chosen, is also the one whose spread weighs most.",),
    ),
    Param(
        path="trade.config.turbo_preference.price_range.max", group="sizing", label="Turbo's price up to", kind="number",
        unit="€", minimum=0,
        help="Only turbos whose ask is within the range are bought.",
        readers=(TRADER, WATCHLIST), consumers=("backtests",), risk=TRADING,
        related=("trade.config.turbo_preference.price_range.min",),
    ),
    Param(
        path="trade.config.turbo_cache.enabled", group="sizing", label="Reuse the turbo search", kind="bool",
        optional=True, default=False,
        help="The trader keeps the result of its own search of Saxo for a while.",
        readers=(TRADER,), risk=TRADING,
        related=("trade.config.turbo_cache.ttl_seconds", f"{WATCHLIST_SECTION}.enabled"),
    ),
    Param(
        path="trade.config.turbo_cache.ttl_seconds", group="sizing", label="Turbo search kept for", kind="number",
        unit="s", minimum=0, optional=True, default=30,
        help="How long a search result is reused.",
        readers=(TRADER,), risk=TRADING,
    ),
)

SIGNALS = (
    required_rule("allowed_indices", "signals", "Indices", "The indices WATA trades.",
                  (TRADER, WATCHLIST, RECORDER, ADMIN)),
    Param(
        path=rule("allowed_indices", "indice_ids"), group="signals", label="Indices traded", kind="map",
        key_label="Name in the signal", value_label="Saxo's Uic of the underlying",
        help="Each indice a signal may name, with Saxo's Uic of its underlying.",
        readers=(TRADER, WATCHLIST, RECORDER, ADMIN), consumers=("backtests",), risk=TRADING,
        effects=(
            "The trader refuses a signal for any other indice.",
            "The watchlist manager keeps a turbo ready for each indice and direction.",
            "The price recorder records the turbos of each underlying.",
            "A recorded or Capital.com instrument whose label is an indice's name is recorded as that index.",
        ),
        caveats=("The Uic is a number, kept as text.",),
    ),
    Param(
        path="trade.config.signal_sources", group="signals", label="Where each index's signals come from",
        kind="map", key_label="Indice", value_label="Source", choices=SOURCES, optional=True, default=None,
        help="tradingview: TradingView's alerts, through the webhook (an indice absent takes them too). engine: the "
             "TA engine's live signal rule, alone.",
        readers=("web_server", "ta_engine"), risk=TRADING,
        effects=(
            "The web server answers a webhook for an indice set to the engine without storing it, and counts it for "
            "the Signals page.",
            "The TA engine sends a live rule's signals only for an indice set to the engine.",
            "A signal rule goes live only on an indice the web server runs with set to the engine.",
        ),
        related=(rule("allowed_indices", "indice_ids"),),
        caveats=(
            "Both feed one store, which keeps the newest signal of each indice: TradingView's alerts and a live rule "
            "on the same indice would close each other's positions.",
            "Applies once the web server and the TA engine start again; set it while no rule is live and the market "
            "is closed.",
            "The scheduler's close of the day and the signals sent from the admin are taken whatever the source.",
        ),
    ),
    required_rule("signal_validation", "signals", "Signal validation", "How old a signal may be.", (TRADER,)),
    Param(
        path=rule("signal_validation", "max_signal_age_seconds"), group="signals", label="Oldest signal taken",
        kind="int", unit="s", minimum=1,
        help="The trader refuses a signal older than this.",
        readers=(TRADER,), risk=TRADING,
        related=("trade.config.ta_engine.max_signal_age_seconds", f"{POLLING}.startup_replay_max_age_seconds"),
        caveats=("Apart from the TA engine's own limit, and from what the trader replays at its start.",),
    ),
    Param(
        path="reporting.signal_silence_minutes", group="signals", label="Say when no signal came for", kind="int",
        unit="min", minimum=1, optional=True, default=None,
        help="Said on Telegram when no signal came for this long in trading hours. Empty: never.",
        readers=(TRADER,), risk="service",
    ),
    Param(
        path="trade.config.trading_mode", group="signals", label="Trading mode", kind="text", optional=True,
        default=None,
        help="Only makes the validator require the day trading rule, which the trader and the scheduler read anyway.",
        readers=(ADMIN,), consumers=("dashboard",), risk="none",
    ),
    Param(
        path=f"{POLLING}.service_url", group="signals", label="Web server's address", kind="text", access=SERVER,
        reason=PROTECTED, how=ON_THE_SERVER,
        help="Where the trader, the scheduler, the admin and the TA engine reach the web server.",
        readers=(TRADER, SCHEDULER, "ta_engine", ADMIN), optional=True, default="http://web_server1:80",
    ),
    Param(
        path=f"{POLLING}.interval_ms", group="signals", label="Polling interval", kind="int", unit="ms", minimum=0,
        optional=True, default=500,
        help="How often the trader asks the web server for signals when it does not wait for them (long polling off).",
        readers=(TRADER,), risk=TRADING,
    ),
    Param(
        path=f"{POLLING}.long_poll_wait_ms", group="signals", label="Wait for signals up to", kind="int", unit="ms",
        minimum=0, optional=True, default=1000,
        help="How long the web server holds the trader's request until a signal comes. 0: polling every interval.",
        readers=(TRADER,), risk=TRADING,
    ),
    Param(
        path=f"{POLLING}.startup_replay_max_age_seconds", group="signals", label="Replayed at the trader's start, if newer than",
        kind="int", unit="s", minimum=0, optional=True, default=10,
        help="At its start the trader takes a signal received at most this long before; older ones are journaled as "
             "ignored.",
        readers=(TRADER,), risk=TRADING,
        caveats=("A signal arriving while the trader restarts is lost unless it is newer than this when the trader "
                 "polls again.",),
    ),
    Param(
        path=f"{POLLING}.request_timeout_seconds", group="signals", label="Web server answers within", kind="number",
        unit="s", minimum=0, optional=True, default=2,
        help="How long the trader and the scheduler wait for the web server.",
        readers=(TRADER, SCHEDULER), risk="service",
    ),
)

WATCHLIST_PARAMS = (
    Param(
        path=f"{WATCHLIST_SECTION}.enabled", group="watchlist", label="Use the watchlist manager", kind="bool",
        optional=True, default=True,
        help="The trader asks the watchlist manager for a turbo before searching Saxo itself.",
        readers=(TRADER, ADMIN), risk=TRADING,
        effects=("Off, every entry searches Saxo, which costs seconds.",),
    ),
    Param(
        path=f"{WATCHLIST_SECTION}.request_timeout_seconds", group="watchlist", label="Watchlist answers within",
        kind="number", unit="s", minimum=0, optional=True, default=1,
        help="How long the trader waits for the watchlist manager before searching Saxo itself.",
        readers=(TRADER,), risk=TRADING,
    ),
    Param(
        path=f"{WATCHLIST_SECTION}.refresh_interval_seconds", group="watchlist", label="Turbos looked for every",
        kind="int", unit="s", minimum=1, optional=True, default=300,
        help="How often the watchlist manager searches Saxo for the turbos it keeps.",
        readers=(WATCHLIST,), risk="service",
    ),
    Param(
        path=f"{WATCHLIST_SECTION}.stale_after_seconds", group="watchlist", label="A turbo kept is old after",
        kind="int", unit="s", minimum=1, optional=True, default=330,
        help="Past this, a turbo the manager kept is old: the trader refuses it unless old ones are accepted.",
        readers=(WATCHLIST,), risk=TRADING,
        related=(f"{WATCHLIST_SECTION}.accept_stale", f"{WATCHLIST_SECTION}.refresh_interval_seconds"),
    ),
    Param(
        path=f"{WATCHLIST_SECTION}.startup_refresh", group="watchlist", label="Look for turbos at start", kind="bool",
        optional=True, default=True,
        help="The manager searches Saxo as soon as it starts, rather than at its first interval.",
        readers=(WATCHLIST,), risk="service",
    ),
    Param(
        path=f"{WATCHLIST_SECTION}.accept_stale", group="watchlist", label="Accept an old turbo", kind="bool",
        optional=True, default=False,
        help="The trader takes a turbo the manager kept past its age.",
        readers=(TRADER,), risk=TRADING,
    ),
    Param(
        path=f"{WATCHLIST_SECTION}.directions", group="watchlist", label="Directions kept", kind="strings",
        choices=("long", "short"), optional=True, default=["long", "short"],
        help="For which directions the manager keeps a turbo ready.",
        readers=(WATCHLIST, ADMIN), risk="service",
    ),
    Param(
        path=f"{WATCHLIST_SECTION}.service_url", group="watchlist", label="Watchlist manager's address", kind="text",
        access=SERVER, reason=PROTECTED, how=ON_THE_SERVER, optional=True, default="http://watchlist_manager1:8081",
        help="Where the trader and the admin reach the watchlist manager.", readers=(TRADER, ADMIN),
    ),
    Param(
        path=f"{WATCHLIST_SECTION}.api_host", group="watchlist", label="Watchlist manager listens on", kind="text",
        access=SERVER, reason=PROTECTED, how=ON_THE_SERVER, optional=True, default="0.0.0.0",
        help="The address the watchlist manager listens on.", readers=(WATCHLIST,),
    ),
    Param(
        path=f"{WATCHLIST_SECTION}.api_port", group="watchlist", label="Watchlist manager's port", kind="int",
        access=SERVER, reason=PROTECTED, how=ON_THE_SERVER, optional=True, default=8081, minimum=1, maximum=65535,
        help="The port the watchlist manager listens on.", readers=(WATCHLIST,),
    ),
)

SAXO = (
    Param(
        path="trade.config.saxo_session.full_trading_level", group="saxo", label="Real-time prices", kind="bool",
        optional=True, default=False,
        help="The trader asks Saxo for the full level of WATA's session (FullTradingAndChat), at which Saxo gives "
             "real-time prices.",
        readers=(TRADER,), risk=TRADING,
        effects=("Checked every minute and asked again when another session took it.",
                 "The other services share the trader's login, so they get the level too."),
        caveats=("One session of an account holds that level: while WATA holds it, SaxoTraderGO loses it and may be "
                 "logged off.",),
    ),
    Param(
        path="saxo_auth.env", group="saxo", label="Saxo's environment", kind="enum", choices=("live", "simulation"),
        access=SERVER, optional=True, default="live",
        reason="Simulated positions would be written beside the live ones, and the app and its login are set apart "
               "for each environment: decided on the server.",
        how=ON_THE_SERVER,
        help="Whether the services trade the live account or Saxo's simulation.",
        readers=(TRADER, MONITOR, WATCHLIST, RECORDER, TELEGRAM), risk=TRADING,
    ),
    Param(
        path=f"{GENERAL}.streaming.refresh_rate_ms", group="saxo", label="Stream's refresh rate", kind="int", unit="ms",
        minimum=0, optional=True, default=1000,
        help="How often Saxo's stream sends what changed of the positions and the watched turbos.",
        readers=(MONITOR, WATCHLIST), risk=TRADING,
        caveats=("The price recorder has its own rate.",),
    ),
    Param(
        path=f"{GENERAL}.streaming.reconnect_delay_seconds", group="saxo", label="Stream reconnects after",
        kind="number", unit="s", minimum=0, optional=True, default=1.0,
        help="The first wait before reconnecting a stream Saxo closed; it doubles at each failure.",
        readers=(MONITOR, WATCHLIST, RECORDER), risk="service",
        related=(f"{GENERAL}.streaming.max_reconnect_delay_seconds",),
    ),
    Param(
        path=f"{GENERAL}.streaming.max_reconnect_delay_seconds", group="saxo", label="Longest wait to reconnect",
        kind="number", unit="s", minimum=0, optional=True, default=30.0,
        help="The doubling wait stops growing there.",
        readers=(MONITOR, WATCHLIST, RECORDER), risk="service",
    ),
    Param(
        path=f"{GENERAL}.streaming.reauth_interval_seconds", group="saxo", label="Stream re-authorised every",
        kind="number", unit="s", minimum=1, optional=True, default=900,
        help="How often a stream is given the latest access token.",
        readers=(MONITOR, WATCHLIST, RECORDER), risk="service",
        caveats=("Saxo's access tokens last 20 minutes: Saxo closes a stream whose token expired, and it reconnects.",),
    ),
    Param(
        path=f"{GENERAL}.streaming.reconcile_interval_seconds", group="saxo", label="Positions reconciled every",
        kind="number", unit="s", minimum=1, optional=True, default=30,
        help="How often the position monitor compares WATA's rows with what Saxo lists, besides each position that "
             "leaves the stream.",
        readers=(MONITOR,), risk=TRADING,
    ),
    Param(
        path=f"{GENERAL}.streaming.live_value_interval_seconds", group="saxo", label="Live values written every",
        kind="number", unit="s", minimum=0, optional=True, default=5,
        help="How often the position monitor writes each open position's value for the admin. 0: never.",
        readers=(MONITOR,), risk="none",
    ),
    Param(
        path=f"{GENERAL}.keep_warm_interval_seconds", group="saxo", label="Saxo called between signals every",
        kind="number", unit="s", minimum=0, optional=True, default=30,
        help="The trader calls Saxo on a timer so that a trade does not pay for a new connection; it also checks its "
             "own health then. 0: never.",
        readers=(TRADER,), risk=TRADING,
    ),
    Param(
        path=f"{GENERAL}.api_limits.top_instruments", group="saxo", label="Turbos asked for at most", kind="int",
        minimum=1,
        help="How many turbos a search of Saxo asks for.",
        readers=(TRADER, WATCHLIST, RECORDER), risk=TRADING,
        caveats=("Capped at 60 in the code: more has no effect.",),
    ),
    Param(
        path=f"{GENERAL}.api_limits.top_closed_positions", group="saxo", label="Closed positions asked for at most",
        kind="int", minimum=1,
        help="How many closed positions are asked of Saxo at once.",
        readers=(TRADER, MONITOR), risk="service",
    ),
    Param(
        path=f"{GENERAL}.retry_config.max_retries", group="saxo", label="Retries of a lookup", kind="int", minimum=0,
        help="How often a price or a position just bought is asked of Saxo again.",
        readers=(TRADER, MONITOR, WATCHLIST, RECORDER), risk=TRADING,
        caveats=("Capped at 3 for prices; a position's lookup waits the first this many of 0.3 … 3.0 s, about 11 s "
                 "at most.",),
    ),
    Param(
        path=f"{GENERAL}.api_limits.top_positions", group="saxo", label="Open positions asked for at most", kind="int",
        access=UNUSED, optional=True, reason="Nothing reads it.", help="Left from an earlier WATA.",
    ),
    Param(
        path=f"{GENERAL}.retry_config.retry_sleep_seconds", group="saxo", label="Wait between retries", kind="int",
        access=UNUSED, optional=True, reason="Stored and never used: the waits are fixed in the code.",
        help="Left from an earlier WATA.",
    ),
    Param(
        path=f"{GENERAL}.position_check.check_interval_seconds", group="saxo", label="Positions checked every",
        kind="int", access=UNUSED, optional=True,
        reason="The 7-second polling job it timed was replaced by the position monitor's stream.",
        help="Left from an earlier WATA.",
    ),
    Param(
        path=f"{GENERAL}.position_check.timeout_seconds", group="saxo", label="Position check timeout", kind="int",
        access=UNUSED, optional=True,
        reason="The 7-second polling job it timed was replaced by the position monitor's stream.",
        help="Left from an earlier WATA.",
    ),
    Param(
        path=f"{GENERAL}.websocket.refresh_rate_ms", group="saxo", label="WebSocket refresh rate", kind="int",
        access=UNUSED, optional=True, reason="Nothing reads it: the streams use the streaming section's rate.",
        help="Left from an earlier WATA.",
    ),
)

PARAMS = DAY + HOURS + EXITS + SIZE + SIGNALS + WATCHLIST_PARAMS + SAXO
