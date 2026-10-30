# 07 · Glossary

**Account guard**: pauses new entries below a balance or after a drawdown from the account's highest; only the operator resumes them. Off live.

**Alert mapping**: what each `alertcondition()` title becomes in a rule or a backtest: `long`, `short`, `close-position`, `message` (the action read from the message's JSON) or `ignore`.

**Bp (basis point)**: 0.01 %. One index point near 29,500 is about 0.34 bp.

**Close reasons**: why a position was sold.
- "Stoploss (−7%) hit at …", "Takeprofit …", "Trailing Stop: peak …";
- "Daily profit target (0.5%) potentially met (…)";
- "Explicit Close (All)": a `close-position` signal, a flip or the day's close;
- "End of day";
- "SaxoAPI": recorded by the reconciliation, a knock-out or a sale WATA did not label.

**Code digest**: a fingerprint of the backtest code a run was made with. A run of another digest is "stale": compare runs of one digest.

**Confidence**: 0 to 2, carried by a signal. Under 0.3 the entry is refused; it is sized 50 % under 0.5, 75 % under 0.8, 100 % from 0.8. None counts as 1.0.

**Continued run**: the TA engine runs a new bar on what the run before it left, instead of the whole history again. Scripts that read `barstate`, `request.*`, the clock or a `strategy()` run whole at every bar.

**Day target**: the monitor sells as soon as the day's compounded result, with the open position, would reach +0.5 %. With no entry above +0.5 %, that ends the day.

**Deflated Sharpe ratio (DSR)**: the probability that a sweep's best Sharpe ratio is real after counting every trial of its family. Under 95 %: do not believe.

**Edge (of the entries)**: how far the index moved 1 to 60 min after each entry signal, in its direction, against random moments of the same hours. "No edge" means t under 2.6 at the best horizon.

**Engine day**: the TA engine's day, from one daily restart (21:15 UTC) to the next, run over `history_days` of bars before.

**Experiment, family, trial**: an experiment is one hypothesis with its runs. A family is the idea it belongs to. Every run is 1 trial of its family and every sweep as many as its combinations; the overfitting figures are corrected by the family's trials.

**Flip**: an entry against an open position. WATA sells, then buys the other side.

**Indice**: WATA's word for an index. `us100`, the Nasdaq 100, is the only one.

**Journal**: `signal_journal`, every signal the trader received and what it did with it. `ta_signal` is the TA engine's own.

**Knock-out level (barrier)**: the index level at which a turbo dies. It is in the turbo's name.

**Lock box**: days kept out of every run and study until a candidate's final check. Claude can only make it larger; the operator alone opens it.

**Modelled turbos**: turbos made up from Dukascopy's index with a measure of the recorded ones, for long backtests. Measured on 4 days so far.

**Neighbours**: the same run with a chosen value moved a little each side. A plateau, or a lucky peak?

**Next quote (fill price)**: a backtest fills an order at the turbo's first quote after the order, since Saxo fills at a live price its standing quote has not caught up with.

**One bar, one trade**: a bar that raises an entry and a `close-position` sends only the entry.

**Paper day, report, reference**:
- a paper day is a one-day backtest of a shadow rule's signals, made each night (slippage 0);
- the report is one run since the rule began, made again each night;
- the reference is a run whose daily range (5–95 %) the paper days are compared with.

**PBO (probability of overfitting)**: how often the best combination of a sweep, chosen on some days, ranks below the median on the others. Above 0.5: do not believe.

**PineTS**: the open-source Pine runtime (Node) WATA runs scripts with. It is not TradingView.

**R60, R6000**: range bars of 60 or 6,000 ticks of the asset. Both are 6 points: 60 on Capital.com (tick 0.1), 6,000 on Dukascopy (tick 0.001).

**Random entries (baseline)**: the same days traded again with entries at random moments and random directions, on the same turbos and rules. "Beaten" is the share of those the run did better than; 95 % is needed.

**Recorded turbos**: Saxo turbos whose bid and ask the price recorder kept every 10 s, since 13 Oct 2026. What backtests trade.

**Risky minutes**: from 15:54 New York, no new entry.

**Shadow, live, off**: a rule's modes. Shadow journals signals and trades them on paper; live sends them to the trader. Going live is the operator's.

**Signal rule**: a pinned script version, an asset, a side, bars, inputs and an alert mapping, run by the TA engine.

**Slippage (backtest)**: euros per turbo taken from each fill: 0.005 normal, 0.01 stress.

**Stress**: the same run with each order later and each fill dearer, step by step.

**Sweep, held-out days, walk-forward**:
- a sweep is a grid of up to two settings or inputs;
- the held-out days are the last 30 %, not used to choose;
- the walk-forward chooses the best again and again on past days and keeps it on the next ones.

**Trailing stop**: armed once a position reaches +2 %; it sells when the position falls 2.5 points below its best.

**Turbo (knock-out warrant)**: a leveraged certificate on the index, long or short, worth about (index − zero level) × 0.0087 € a point for a long. Saxo's are named "NASDAQ 100 Turbo(XL) Long/Short <knock-out level> Saxo".

**`updates`**: the number of quote changes in a 10-s bar. It stands for volume.

**`wait_for_fresh_quote`**: a position just bought is not judged until Saxo sends a new quote after the purchase.
