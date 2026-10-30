# 03 · Turbos, data and costs

*Measured on WATA's own records, October 2026. Measure again before relying on a number: `data_bars` on a turbo, `turbo_model`, `data_execution`.*

## Saxo's Nasdaq 100 turbos

- **Price per index point**: a **long** turbo is worth about (index − its zero level) × 0.0087 €; a **short** about (its zero level − index) × 0.0088 €. These euros per index point were measured on the recorded turbos (`turbo_model`, 13–16 Oct, r² 0.93 and 0.89).
- **Knock-out**: the knock-out level is in the turbo's name ("NASDAQ 100 TurboXL Long 29022.5799 Saxo"). The zero level lies about 130 points beyond it for a long and 105 for a short, measured against Dukascopy's index: a turbo still costs about 1 € at its knock-out.
- **When the index touches the knock-out level the turbo is gone.** A backtest sells a knocked-out turbo at 0; what Saxo pays back, if anything, is not modelled.
- **The ladder**: Saxo lists turbos at levels roughly 50–150 points apart, often a Turbo and a TurboXL at one level. The recorder keeps the ~20 long and ~20 short closest to the index each day (62 known on 30 Oct). The ladder moves as Saxo issues new ones.
- **What WATA buys**: the lowest bid between 4 and 15 € among them, so the closest to its knock-out within the band. In 13–20 Oct, longs were mostly bought at 4.2–5.2 € and shorts at 7.7–12.6 €.

### One index point, at the index near 29,500

Longs, at 0.0087 € a point, with the zero level 130 points beyond the knock-out:

| Turbo | 1 point | Stop −7 % | Day target +0.5 % | Trailing armed (+2 %) | Trailing gap (2.5 %) | To knock-out | Leverage |
|---|---|---|---|---|---|---|---|
| 4 € | 0.22 % | 32 pts | 2.3 pts | 9 pts | 11.5 pts | ≈ 330 pts (1.1 %) | ≈ 64× |
| 5 € | 0.17 % | 40 pts | 2.9 pts | 11.5 pts | 14 pts | ≈ 445 pts (1.5 %) | ≈ 51× |
| 8 € | 0.11 % | 64 pts | 4.6 pts | 18 pts | 23 pts | ≈ 790 pts (2.7 %) | ≈ 32× |
| 15 € | 0.058 % | 121 pts | 8.6 pts | 34 pts | 43 pts | ≈ 1,600 pts (5.4 %) | ≈ 17× |

So WATA's exits are tiny in index terms. On a 4–5 € long, the day target is 2–3 points away; the trailing stop arms at about 10 points and gives back 12–14; the stop is 32–40 points away. The index ranged over 730 points on 29 Oct 2026.

## Quotes, spread, fills

- **Saxo quotes a turbo about every 25–40 s**: 86–154 changes an hour on 29 Oct 2026; p95 40 s and max 80 s on 19 Oct.
  - The index moves continuously meanwhile, and Saxo fills an order at the live price, which the standing quote has not caught up with.
  - Exits act on quotes that old. Real stops at −7 % filled at −16.7 % and −30.2 %, and a "2.5 %" trailing gap sold 2.5 to 6.5 points under the best.
- **The spread is a fixed amount in euros at a moment, not a percentage.** It was the same 0.028 € on a 6 € and a 13 € long on the morning of 29 Oct.
  - **Usually** 0.009–0.010 € (≈ 1.1 index points).
  - **0.028–0.036 €** (≈ 3–4 points) for hours at a time: the morning of 29 Oct until about 15:00 Paris (10:00 New York); the hour before the FOMC statement on 28 Oct.
  - **≈ 0.11 €** for the half hour after the FOMC statement (28 Oct, 18:00 UTC), and at that day's US close.
  - On a 4–5 € turbo the usual spread is 0.2–0.25 % of the stake and the wide one 0.6–0.9 %: more than the +0.5 % day target.
- **Real fills sit at the recorded quotes.** Real entry minus the backtest's, on 21 paired positions of 13–20 Oct: median −0.003 €, mean +0.005 €. Hence a backtest slippage of **0.005 € per turbo** as normal, and **0.01** as the stress.
  - `data_execution`'s "slippage" (median 0.036 €) is measured against the price WATA saw when the order left, which is sometimes stale: it overstates the cost.
- **A round trip costs** the spread plus two slippages:
  - ≈ 0.02 € (≈ 2.3 index points, 0.8 bp of the index) at the usual spread;
  - ≈ 0.045 € (≈ 5 points) at the wide one;
  - on a 4–5 € turbo, 0.4–1 % of the stake per trade.

  An entry's edge must beat that, after the exits, or nothing is left.
- **Delays**:
  - from a TA engine bar's close to its signal: seconds (backtests assume 3 s);
  - from receipt to the trader's decision: ~0.02 s;
  - from an order to Saxo's acceptance: median 0.26 s. Until v0.48.0 one order in ten waited about 60 s for a rate limit; those fills carry most of the gap between real fills and replays.
- **Refusals**: Saxo refused 14 % of order requests from July to October, most of them between 08:00 and 09:00 Paris (a sale already working, or unknown).

## The data

| Asset ref | What | From | Tick | Hours | Use |
|---|---|---|---|---|---|
| `capital/US100` | Capital.com's US Tech 100, a CFD: the live feed | 15 Oct 2026, 21:20 UTC (first whole day 16 Oct) | 0.1 | nearly round the clock on weekdays (~8,500 bars of 10 s a day), from Sunday evening, nothing on Saturday | What signal rules run on: Pine studies, backtests of a rule |
| `dukascopy/us100` | Dukascopy's US100 history, a CFD | files for 221 days, 15 Feb – 29 Oct 2026 | 0.001 | about the same | Long backtests on modelled turbos (up to 731 days) |
| `saxo/<id>` | Saxo's turbos, recorded every 10 s: bid, ask, number of quote changes | 13 Oct 2026 | 0.001–0.01 | 08:00–22:00 Paris on weekdays | What every backtest trades |

- **Days that can be backtested on recorded turbos**: weekdays from 13 Oct 2026, 13 whole days by 29 Oct; 23 Oct ends at 20:07 Paris. A Pine rule on `capital/US100` has signals only from 16 Oct, 10 whole days by 29 Oct. `data_coverage` says what each day has.
- **Saxo's own Nasdaq index** only quotes in US cash hours, and turbo volumes are tiny: neither is any use for signals.
- **Everything is built from 10-second bars**: open, high, low and close of the bid and the ask, and `updates`, the number of quote changes, which stands for volume. Bars of any length (10 s to a day) and range bars are built from them. A range bar's volume is the updates of the 10-s bars it spans; one made inside 10 s can have 0.
- **Range bars count the asset's own tick**: R60 is 6 points on capital/US100 (tick 0.1) and 0.06 points on dukascopy/us100 (tick 0.001), where 6 points are R6000. An engine day of more than 40,000 bars is refused. R60 on Capital.com makes about 4,000–6,500 bars a day.
- **Capital.com's spread** is about 1.8 index points. Its prices, like Dukascopy's, follow the index futures, not exactly the cash index the turbos are written on.
- **Times**: data in UTC; the trading day is the Paris day; trading hours are New York's; PineTS's own days are UTC days.

## Turbos made up from the index (long backtests)

`turbos: "modelled"` trades a ladder of made-up turbos priced from Dukascopy's index. The measure behind them: euros a point, zero level beyond the knock-out, ladder spacing, spread by hour, quoting hours.
- **The measure rests on 4 days** (13–16 Oct, `few_days`). Measure it again over every recorded day (`turbo_model_measure`) before trusting a long run.
- Then check it: `backtest_derive` kind `model_check` of a recorded run.
- The euro's rate is held at what it was when measured, and new issues during a day are not made up.
