# 01 · WATA, the system

*State on 30 Oct 2026: admin v0.62.0, trading services v0.59.1 (the same trading code). The live server says what is true now: `status`, `backtest_options`, `rule_list`.*

WATA is the operator's self-hosted automated trading assistant: async Python services in Docker, with PostgreSQL and RabbitMQ, on one server. It day-trades **Saxo Bank knock-out turbos on the Nasdaq 100** from **signals**, on a small real Saxo account. WATA calls an index an "indice"; `us100` is the only one.

## From a signal to a trade

1. **The signal** says `long`, `short` or `close-position` for `us100`. It carries its time, and optionally a **confidence** from 0 to 2.
2. **One source per indice** (`trade.config.signal_sources`): TradingView's webhooks, or the **TA engine**'s live rule. The operator stopped using TradingView on 29 Oct 2026: signals are now to come from WATA's own Pine rules.
3. **The web server** keeps the newest signal of each indice. **The trader** long-polls it and takes each signal once. It checks the signal against its rules (02) and journals what it did (`signal_journal`).
4. **An entry** first sells the position of the other direction, if there is one (a flip). It then chooses a turbo: the lowest bid between 4 and 15 € among the turbos of that direction closest to their knock-out, so the most leveraged of the band. It buys it with the whole account.
5. **The position monitor** follows Saxo's price stream. It sells on stop-loss, take-profit, trailing stop or the day's profit target. A `close-position` signal sells everything, and so does 15:55 New York (02).
6. **Records**: every signal and its outcome, every order and Saxo's answer, every position with its best and worst result. The research tools read them: `data_signals`, `data_trades`, `data_position`, `data_orders`, `data_execution`.

## The TA engine: WATA's own signals

- **A signal rule** names:
  - a Pine script version, and its libraries' versions, pinned;
  - an asset: `capital/US100`, Capital.com's live Nasdaq 100 feed;
  - a side (bid or ask) and bars: any length from 10 s to a day, or range bars `R<ticks>`;
  - input values (`in_0`, `in_1`…);
  - what each `alertcondition()` title becomes: `long`, `short`, `close-position`, `message` (WATA's webhook JSON read from the alert's message) or `ignore`.
- **How it runs**: the engine builds the rule's bars from the feed's 10-second bars. It runs the script with **PineTS**, an open-source Pine runtime in Node (not TradingView). At each daily restart (21:15 UTC) the script runs over `history_days` of bars before (1 by default); then it continues bar by bar as each bar closes.
- **Signals**: a closed bar's alerts become signals, stamped with the bar's close. A signal is dropped when its bar closed more than 60 s ago or when the feed is stale. **One bar, one trade**: a bar that raises an entry and a `close-position` sends only the entry.
- **Modes**: `shadow` journals the signals in `ta_signal` and never sends them; `live` sends them to the trader; `off`. A new rule starts in shadow.
- **Paper trading**: after each finished weekday, each shadow rule's signals are traded on paper on the recorded turbos. Each day is a one-day backtest, with fixed fills and slippage 0. The rule's **report**, one backtest since the rule began, is made again each night. A run made from the rule can be set as its **reference**: its paper days are compared with the reference's daily range (5–95 %).
- **Going live** is the operator's action alone, and audited. It needs the indice set to the engine and one live rule per indice. It also needs the rule's readiness, unless the operator chooses "Go live anyway":
  - a week of weekdays in shadow since the last change of what it runs;
  - a signal journaled;
  - no paper day outside its reference's range;
  - the engine running it without fault.

## State on 30 Oct 2026

- **No signal source is live**: TradingView is off and no rule is live. WATA takes no trade; its last real position was on 20 Oct 2026.
- **One rule, in shadow**: "Kernel + Avg multi VWAP Context light on US100" (`86fc6f91463e`). It runs the operator's script `0d4fd1117e2f` v10 on R60 bars of capital/US100 (bid). What it does and how it fares: 06.
- **The account is small**, about 22 € at the end of October 2026. Backtests start from 1,000 € unless told otherwise, and are judged in percent.

## What Claude can do here (the `wata` MCP server)

Research only, on the live server's own data. Nothing behind the tools trades, edits the configuration or takes a rule live.

| For | Tools |
|---|---|
| Looking | `status`, `data_assets`, `data_coverage`, `data_bars`, `data_signals`, `data_trades`, `data_position`, `data_orders`, `data_execution`, `turbo_model`, `rule_list`, `memory_list`, `memory_read` |
| The experiment journal | `exp_new`, `exp_get`, `exp_trials`, `exp_conclude`, `lockbox_set` |
| Pine scripts | `pine_list`, `pine_get`, `pine_check`, `pine_run`, `pine_signals`, `pine_save`, `pine_delete` |
| Backtests | `backtest_options`, `backtest_start`, `backtest_wait`, `backtest_list`, `backtest_result`, `backtest_trades`, `backtest_days`, `backtest_signals`, `backtest_compare`, `backtest_derive`, `backtest_remove`, `turbo_model_measure` |
| Claude's shadow rules | `rule_shadow_save`, `rule_shadow_remove`, `rule_set_reference` |
| Long studies | `job_wait` |
| TradingView comparisons | `pine_compare`, `rule_compare`: they set signals beside TradingView's, which only exist up to 20 Oct 2026 |

- **Claude's scripts are its own** (`made_by: claude`; name them "Claude · …"). The operator's are never changed: save a copy.
- **Claude's shadow rules**: 3 at most, each within 2 s of runner time per engine day, never live.
- **The lock box** keeps days out of every study and run until a candidate's final check. Claude can only make it larger; only the operator can open it.
- **Limits now**:
  - 3 runs waiting, 60 runs a day (UTC);
  - 10,000 simulated trading days per request: its trading days × its sweep's combinations, or × (1 + random repetitions);
  - 20 min of Pine studies an hour, 90 s and 384 MB per study;
  - 100 scripts.
- Studies run one at a time, and Claude's runs wait behind the operator's.

## Other Claudes around WATA

- **WATA's AI agent** (admin › AI agent) uses the same tools from the admin's pages. It makes proposals the operator applies, writes a daily debrief, and keeps a **memory** this project can read (`memory_list`, `memory_read`) but not write. A finding to share with it: the operator adds it there.
- **Claude Code in the WATA repository** (skill `wata-lab`) has the same server tools plus the Mac's disk: CSV exports, Python ports of a script, fast local screening. It also has the code itself. A change to WATA's rules is a code change there, mirrored in the backtester.

## The operator's pages (admin)

- **Technical Analysis**: Indicators (editor and test), Charts, Signal rules, Engine monitor.
- **Backtests**: runs, sweeps and comparisons; Backtests › Paper trading.
- **Data**: assets and availability.
- **Trading**: positions, orders, signals.
- **Configuration**: every setting, with its history.
- **System**: health and releases.
