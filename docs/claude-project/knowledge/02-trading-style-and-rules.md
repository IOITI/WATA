# 02 · Trading style and WATA's live rules

*Values read from the live configuration on 30 Oct 2026 (`backtest_options`). Read them again before relying on one.*

## The style

From the operator's own baseline (April 2026), still the spirit of WATA:
- **Systematic, rules-based, automation-first.** No discretionary chart reading: a clear signal, executed with discipline by WATA.
- **Intraday only.** Flat by the close; never held overnight or over a weekend.
- **One market**: the Nasdaq 100 (`us100`), through Saxo turbos, long and short.
- **Execution counts almost as much as the signal.** Latency and reliability matter: a strategy must be deployable, not only good in a backtest.
- **What to optimise**: expectancy, drawdown control and real deployability, not the number of signals.
- **Risk controls are hard constraints.** The signal layer and the execution and risk layer are separate on purpose: say which one a change touches.
- **Confidence** (0–2) was meant to size positions (50/75/100 %). It is worth keeping only if it predicts outcomes.
- **Improvement is measured**, before and after: win rate, expectancy per trade, drawdown, trades per day, confidence buckets, delay from signal to execution.

What changed since that baseline:
- TradingView is no longer used (29 Oct 2026). The strategy must run as a WATA signal rule on WATA's one live feed, Capital.com's US100.
- The TradingView chart's multi-provider VWAP (Skilling, PurpleTrading) cannot be reproduced: WATA has no such feeds.
- The exits and the day logic were tightened (below).

## Hours

Trading hours are read in New York time (`market_hours.timezone`). The trading day is the Paris day.

| | New York | Paris, usual weeks | Paris, weeks the clocks change apart |
|---|---|---|---|
| Entries from | 02:00 | 08:00 | 07:00 |
| No entry from (risky minutes) | 15:54 | 21:54 | 20:54 |
| Everything sold at | 15:55 | 21:55 | 20:55 |
| Hours end | 16:00 | 22:00 | 21:00 |
| US cash session | 09:30 – 16:00 | 15:30 – 22:00 | 14:30 – 21:00 |
| Saxo quotes the turbos | | 08:00 – 22:00 | |

- **Weeks the clocks change apart**: late March, and the last week of October. Entries then open an hour before Saxo quotes the turbos, and an entry finds none.
- **US holidays and early closes** come from a calendar file. No entry on a closed day. On an early close, the day's sale and the last entry move earlier.

## The trader's checks on an entry, in order (live values)

1. **Signal age** ≤ 7,200 s. The TA engine itself drops a bar that closed more than 60 s ago.
2. **Market**: not a closed date or a US holiday; within entry hours; before the risky minutes; before an early close's end.
3. **Window around US releases** (`macro_news`): **off**. When on, there is no entry from 5 min before a release to 15 min after (45 min after an FOMC statement), for FOMC, CPI, NFP and PCE. It can also sell what is held N minutes before (`flatten_minutes_before`, 0).
4. **Indice** allowed (`us100`).
5. **Account guard**, which pauses entries below a balance or after a drawdown from the highest: **off**.
6. **No position already open** in the same direction.
7. **Day result**: no new entry once the day is at or above **+0.5 %**, nor at or below **−25 %**.
8. **Cooldown after a losing trade**: **0 min** (off).
9. **Entries per day**: at most **40**.
10. **Day breakers** (losses in a row, drawdown from the day's best): **off**.
11. **Confidence**: an entry under **0.3** is refused. A signal without confidence counts as 1.0.

A refused entry closes nothing.

## Size and turbo

- **Size**: the whole account (`funds_percent` 100 %, no cash reserve). It is scaled by the time of day (three periods, all at 100 % now) and by confidence: 50 % under 0.5, 75 % under 0.8, 100 % from 0.8. The order is in whole turbos, with one left out (`safety_units` 1).
  - So an order needs twice the turbo's ask in cash. At the live ~22 € no turbo above ~11 € can be bought, and often only half to three quarters of the account is used.
  - At a backtest's 1,000 €, ~99 % is used.
- **The turbo**: the lowest bid between **4 and 15 €** among the turbos of that direction closest to their knock-out, so the most leveraged of the band.
  - In 13–20 Oct 2026, longs were mostly bought at 4.2–5.2 € and shorts at 7.7–12.6 € (no short was cheaper then).
  - No spread limit (`max_spread_percent` 0).
- **A flip**: an entry sells the opposite position first. That is two orders, about 1 s apart.
- **The shadow rule sends no confidence**: its entries would all be sized 100 %.

## Exits

The position monitor judges the turbo's bid against the price paid, at every price Saxo sends:
1. **Stop-loss −7 %**.
2. **Take-profit +60 %**.
3. **Trailing stop**: armed once the position has reached +2 %; it sells when the position falls 2.5 points below its best.
4. **Day target**: it sells when the day's compounded result, with this position, would reach **+0.5 %** ("Daily profit target (0.5%) potentially met").
5. **News flatten**: off.

Also:
- A `close-position` signal sells everything ("Explicit Close (All)").
- 15:55 New York sells everything.
- **`wait_for_fresh_quote`** (on): a position just bought is not judged until Saxo's first new quote after the purchase. The quote standing at the purchase is often older than it.

## What this design does (read before proposing anything)

- **A day ends at its first +0.5 %.** The target sells, and no entry is taken above +0.5 %. With the whole account in one turbo, most days are a small win (+0.5 %) or a string of losses. The result lives in the rare bad days: −11.3 % in 28 trades on paper on 23 Oct; −30 % on one real trade before the CPI of 14 Oct.
- **Small costs decide the sign.** +0.5 % of a 4–5 € turbo is 0.02–0.025 €, about one round trip's cost, and less than the spread on some mornings (03).
- **Stops fill far below their level.** Saxo quotes a turbo every ~30 s, and the index can move tens of points between two quotes (hundreds around a release). Two of the three real stops at −7 % filled at −16.7 % and −30.2 %. The third was triggered by a stale quote and sold at +0.5 %.
- **Signal closes lose; the monitor's exits win.** Of 118 real trades from 2 Sep to 20 Oct, summed by close reason:
  - signal, flip and end-of-day closes: −139.8 % (55 trades);
  - day target: +51.7 % (22);
  - trailing stop: +31.1 % (22).
- **Losses come on busy days.** On 9, 14 and 19 Oct there were 11–15 trades each: flips back and forth around the VWAP.

## The live record (real money, TradingView's signals)

From 1 Aug to 20 Oct 2026: 172 positions, 75 won (44 %), results summed −50.45 %. By close reason:
- 72 signal, flip or end of day;
- 34 trailing stop;
- 33 day target;
- 30 "SaxoAPI": recorded by the reconciliation, mostly WATA's own sales labelled so before v0.34.0, or knock-outs;
- 3 stop-loss.

There has been no trade since 20 Oct.

## The April 2026 baseline, for history

| Setting | April 2026 | Now |
|---|---|---|
| Hours | Paris, 08:00–21:55 | New York (above) |
| Stop-loss | −15 % | −7 % |
| Take-profit | +60 % | +60 % |
| Trailing stop (arm / gap) | 5 % / 8 % | 2 % / 2.5 % |
| Day target | +1.7 % | +0.5 % |
| No entry above | +1.25 % | +0.5 % |
| Trades a day | 8 | 40 |
| Cooldown after a loss | 10 min | off |
| Time-of-day scales | 100/50/75 % | 100 % |
| Oldest signal taken | 5 min | 2 h |
