# 04 · Writing Pine for WATA

WATA runs Pine with **PineTS** in Node, not TradingView. Most of Pine v5 and v6 works; some does not, or works differently. This file says what a script must be to become a signal rule, and what to avoid.

## The contract

- **`//@version=6` (v5 accepted) and `indicator(...)`.** A `strategy()` runs, but **only alerts are signals**: its orders are ignored, and it is run whole at every bar (slow).
- **Signals are `alertcondition(condition, "Title", message)`**, with positional arguments. The rule, or the backtest, maps each title to `long`, `short`, `close-position`, `message` or `ignore`.
  - There is no close-long or close-short: `close-position` sells whatever is held.
  - An entry against an open position flips it: WATA sells, then buys.
- **`message` mapping** reads WATA's webhook JSON from the message, for example `{"action": "long", "indice": "us100", "confidence": {{plot("Confidence")}}}`.
  - TradingView's placeholders are filled first: `{{close}}`, `{{time}}`, `{{plot("Title")}}`, `{{plot_0}}`…
  - The engine stamps the times itself.
- **Confidence (0 to 2)** is optional. It comes from the message JSON, or from a plot named as the rule's (or the backtest source's) `confidence_plot`.
  - The trader refuses an entry under 0.3. It sizes 50 % under 0.5, 75 % under 0.8, and 100 % from 0.8.
  - No confidence counts as 1.0 (100 %).
  - A confidence not calibrated to these bands sizes or refuses entries by accident.
- **Inputs are `in_0`, `in_1`…** in declaration order: rules, backtests and sweeps set them by these ids. Add a new input **last**, or every rule and run that sets them shifts.
- **One bar, one trade**: when a bar raises an entry and a `close-position`, only the entry is sent. Never raise a long and a short on one bar.
- **A signal is a bar's alert once the bar has closed**: there is no intrabar signal, and `signal_timestamp` is the bar's close.
- **`alert()` calls have no title**: a rule takes all of them as messages, or none (`alert_calls`). Prefer `alertcondition`.

## What PineTS on WATA does differently

- **`syminfo.mintick` is na on WATA's assets** (seen 30 Oct 2026), and no other `syminfo.*` can be counted on either. A formula that uses it becomes na. Use an input or a constant: the tick is in `data_assets`.
- **`request.security` reads WATA's assets only**, and live only Capital.com's.
  - Use `CAPITAL:CAPITAL_US100`; range series as `CAPITAL_US100.R60`; `.ASK` for the ask. Dukascopy's is read in replays.
  - There is no Skilling, PurpleTrading, futures, VIX or other index.
  - A script that calls it is run whole at every bar.
- **Days are UTC days**, since a symbol has no exchange time zone. Build sessions with `hour(time, "Europe/Paris")`, `minute(time, "Europe/Paris")` or `timestamp("Europe/Paris", …)`. WATA's build caches the time-zone formatter, so these cost little.
- **Bars are built from 10-second bars.**
  - A range bar never shows what happened inside 10 s. There are no tick-built bars and no gaps between bars, so the "fast candle" and gap tests of TradingView's tick charts never fire.
  - A 45-s bar holds 40 or 50 s by turns.
- **History starts where the rule's bars start**: `history_days` of bars before each daily restart (21:15 UTC); the engine's default is 1, a rule's own 0 to 30.
  - A Monday's one day before Sunday's restart holds no bar.
  - An indicator with a long warm-up needs more days; one with none runs faster with 0.
- **Continued runs**: each new bar continues the run. A script is instead run whole over all its bars at every new bar if it reads any of:
  - `barstate.*` (except `isfirst`);
  - `last_bar_index`, `last_bar_time`, `session.islastbar`;
  - `timenow`, `math.random`;
  - `request.*`, or the visible range;
  - or if it is a `strategy()`.

  That is slow, and Claude's shadow rules are refused over 2 s of runner time per engine day.
- **Libraries**: `import user/Name/1 as alias` imports the WATA library that declares `library("Name")`, inlined before the run.
- **PineTS is lenient**: what runs here can fail in TradingView, and the other way round.

## Nothing that looks ahead

- Nothing from bars not yet closed: no `lookahead` in `request.security`.
- A value known later (a pivot, `right` bars after it) is only known then: signal it then.
- Backtests act on a signal 3 s after its bar closed, at the turbo's next quote.

## Habits that pay

- **Keep a state machine** (a `var` state) so that a signal is raised once, not at every bar while a condition holds. An alert on a condition that stays true fires at every bar: thousands of alerts a day, and answers too big to read.
- **Filter the session in the script as well as in WATA's hours**: entries from 08:00 to 21:50 Paris, and an exit before WATA's 21:55 close if the script wants to be flat on its own terms. The spread is wide some mornings (03).
- **Prefer fewer, cleaner signals**: every round trip costs ~0.4–1 % of a 4–5 € turbo.
- **Plot what you want to read back**, and a `Confidence` plot when sizing matters. `pine_run` summarises each plot: na share, min, max, mean, and a thinned line.
- **Check, then look**:
  1. `pine_check`: it compiles, and lists the inputs `in_N`, the plots and the alerts on made-up bars;
  2. `pine_run` on two or three days: does it plot what you think, and how many alerts?
  3. `pine_signals`: what a rule would send, day by day, with confidence;
  4. `pine_save`.

## A skeleton

Checked with `pine_check` and `pine_signals` on 28 Oct 2026: 78 signals, confidences from 0.30 to 0.94. It is not a strategy (an EMA cross on R60 flips all day): it shows the shape WATA wants.

```pine
//@version=6
indicator("Claude · Skeleton (EMA cross)", overlay = true)

// Inputs: rules and backtests set them as in_0, in_1, ... in this order (add new ones last)
int fastLen  = input.int(20, "Fast EMA", minval = 1)                                // in_0
int slowLen  = input.int(80, "Slow EMA", minval = 2)                                // in_1
int startMin = input.int(8 * 60, "Session start (Paris, minutes)", minval = 0)      // in_2
int endMin   = input.int(21 * 60 + 50, "Session end (Paris, minutes)", minval = 0)  // in_3

// The session in Paris time (PineTS's own days are UTC days)
int parisMin = hour(time, "Europe/Paris") * 60 + minute(time, "Europe/Paris")
bool inSession = parisMin >= startMin and parisMin < endMin

float fast = ta.ema(close, fastLen)
float slow = ta.ema(close, slowLen)

// One position at a time: 1 long, -1 short, 0 flat
var int state = 0
bool goLong  = inSession and ta.crossover(fast, slow) and state != 1
bool goShort = inSession and ta.crossunder(fast, slow) and state != -1
bool exitNow = not inSession and state != 0

if goLong
    state := 1
else if goShort
    state := -1
else if exitNow
    state := 0

// Confidence 0.3 to 2: WATA refuses under 0.3, sizes 50 % under 0.5, 75 % under 0.8, 100 % above
float atr = ta.atr(14)
float confidence = 0.3 + 1.7 * (atr > 0 ? math.min(1.0, math.abs(fast - slow) / atr) : 0.0)
plot(confidence, "Confidence", display = display.status_line)
plot(fast, "Fast EMA", color.teal)
plot(slow, "Slow EMA", color.orange)

alertcondition(goLong, "Enter Long", '{"action": "long", "indice": "us100", "confidence": {{plot("Confidence")}}}')
alertcondition(goShort, "Enter Short", '{"action": "short", "indice": "us100", "confidence": {{plot("Confidence")}}}')
alertcondition(exitNow, "Exit", '{"action": "close-position", "indice": "us100"}')
```

Map it either way:
- `{"Enter Long": "long", "Enter Short": "short", "Exit": "close-position"}`: every entry at 100 %, unless the source names `confidence_plot: "Confidence"`;
- `{"Enter Long": "message", "Enter Short": "message", "Exit": "close-position"}`: the action and the confidence are read from the messages.
