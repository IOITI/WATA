---
name: wata-lab
description: WATA's research lab on this Mac (`python -m lab`) — backtests of Pine strategies and of the signals WATA received, on the turbos the price recorder kept, sweeps, the experiment journal, and SQL over the recorded data. Use it whenever the work is iterating on a strategy or its settings, reading backtest results, or analysing WATA's recorded prices, signals or trades.
---

# WATA research lab

The lab runs WATA's own backtest code on this Mac, over a copy of what the
server recorded. The server writes each finished day as files (the admin's
research export, opt-in); `lab/sync.sh` pulls them each morning (the user's
scheduled job, or the user by hand); the lab loads them into a PostgreSQL of
its own; the lab's admin (the real admin app) runs the backtests four at a
time and shows them in its Backtests pages.

Never open SSH to the server, and never run `lab/sync.sh` yourself: pulling
data is the user's. If the data is stale, say so and give the command.

## Or on the server itself (the `wata` MCP server)

When `/mcp` in Claude Code lists `wata` as connected, the same loop runs on
the live server, on its freshest data, with no copy: start with its `status`
tool, and `/wata:research` gives the loop. Its experiment journal and lock box
are the server's own, apart from this lab's. Its tools, by step of the loop:

| Step | The lab | The server's tool |
|---|---|---|
| Look | `lab status` | `status`, `data_assets`, `data_coverage` |
| Experiment | `lab exp new`, `exp conclude` | `exp_new`, `exp_get`, `exp_conclude`, `lockbox_set` |
| Strategy | `lab script check` | `pine_check`, `pine_run`, `pine_signals`, `pine_compare`, `pine_save` |
| Run | `lab run`, `lab sweep` | `backtest_start`, `backtest_wait` |
| Read | `lab show`, `edge`, `regimes`, `trades`, `days`, `compare` | `backtest_result` (parts), `backtest_trades`, `backtest_days`, `backtest_signals`, `backtest_compare` |
| Test | `lab neighbours`, `lab stress` | `backtest_derive` (neighbours, stress, a sweep's cell) |
| Shadow | the user's steps | `rule_shadow_save`, `rule_set_reference`, `rule_list` (once the user switched them on) |

The server's runs wait behind the user's and are counted (3 waiting, 60 a
day); its studies run one at a time. Heavy analysis stays here: ask for CSV
(`data_bars`, `backtest_trades` with `format` csv), which Claude Code saves to
a file, and read it with pandas or DuckDB in `.venv-lab`. Nothing behind the
tools trades, edits the configuration or takes a rule live.

## First, look

```bash
.venv-lab/bin/python -m lab status
```

- No `.venv-lab`: `lab/setup.sh` makes it (requirements.txt + requirements-lab.txt).
- Days not loaded: `.venv-lab/bin/python -m lab db load`.
- Runs need the lab's admin: start `.venv-lab/bin/python -m lab serve` **in the background**
  (Bash `run_in_background`), wait for `http://127.0.0.1:8083/api/session` to answer 401.
- At the end of the session: `.venv-lab/bin/python -m lab stop`, then check that
  `pgrep -fl "lab serve"` and `pgrep -fl "var/lib/lab/pg"` find nothing.
- The Pine runner must be built (`npm run build:runner` in admin_ui/, Node 24 through nvm),
  and the admin's pages (`npm run build`) for `lab serve`.

## The loop

1. **Open an experiment** before running anything:
   `lab exp new --title "…" --hypothesis "<a statement the data can refute>" --family <family of ideas>`.
   A family groups the experiments of one idea; its trial count is what the overfitting
   figures are corrected with.
2. **Write or change the strategy**: a Pine v6 indicator in `lab/strategies/`, checked with
   `lab script check lab/strategies/<file>.pine` (compiles with PineTS and lists the alerts
   it raised on made-up bars).
3. **The spec**: a copy of `lab/specs/ema_cross.json` (the Backtests form's request, with
   `script_file`): the asset and bars the script reads, its inputs, what each alert becomes.
4. **Run**: `lab run <spec> --exp <id> [--set name=value] [--input in_N=value] [--from D --to D]`.
   Each setting changed (`--set`) is one of the form's fields: `lab show` lists them with the
   live values.
5. **Read**, from the summary down:
   - `lab show <run>`: figures, risk, money, random entries, refusals, edge, sample;
   - `lab edge <run> [--by hour|direction|fate|volatility|day_type|gap|us_open|news]`: the index's
     move after each entry signal against random moments of the same hours. **Read it first**: no
     edge here means the result came from WATA's exits and the days;
   - `lab regimes <run>`: each day's kind and the trades by volatility, gap, kind of day, US open,
     macro news and place against the US open — where the result lives;
   - `lab trades <run> --worst 10`, `lab trades <run> --by hour|weekday|direction|exit|holding|turbo_price`;
   - `lab days <run>`, `lab signals <run> --fate refused|unpriced|failed`;
   - `lab compare <run> <run>…`.
6. **Try values**: `lab sweep <spec> --exp <id> --axis setting:<name>=a,b,c [--axis input:in_N=a,b] --walk-forward 10/5`.
   Read the held-out column before the chosen-on one, then the walk-forward, the probability of
   overfitting and the deflated Sharpe ratio (the family's earlier trials are counted).
7. **Test what is kept**, before concluding:
   - `lab neighbours <run> --param <setting or in_N>`: a plateau, or a peak among worse values;
   - `lab stress <run>`: the same run with each order later and each fill dearer;
   - for long histories: runs on made-up turbos (`"turbos": "modelled"` in the spec, 731 days),
     after `lab model show` (measured? on how many days?) and `lab model validate <spec>` (how far
     made-up turbos are from recorded ones on the same signals);
   - `lab calibrate execution`: what WATA's own orders took and cost; `--set` those values.
8. **Conclude**: `lab exp conclude <id> --verdict kept|rejected|inconclusive --notes "…"`, then
   `lab exp report <id>` writes a page to publish for the user as an Artifact (load the
   artifact-design skill before publishing).
9. **Promote what survives** — the user's steps, never yours: import the `.pine` file into the
   server's Indicators page, make a signal rule in **shadow**, run it from the rule and "Set as the
   rule's reference", watch its paper days against that range (`lab paper <rule>`, the Signal
   rules page), and only then switch it live.

## What not to believe

- Fewer than 30 trades or 20 days: the figures describe those days and nothing else.
- Beating random entries below 95 %: the entries have shown nothing WATA's exits do not explain.
- No edge of the entries: under 95 % of the random draws, or a t under 2.6 at the best horizon (the strongest of five is picked: the 5 % of one test is shared among them).
- A best value whose neighbours do badly, or a held-out result far below the chosen-on one: fitted.
- A probability of overfitting above one half, or a deflated Sharpe ratio under 95 %.
- A result that does not survive one step of later and dearer fills.
- Made-up turbos not validated on the recorded days, or measured on few days (`few_days`).
- A replay of the journal far from WATA's real trades (`lab reconcile <run>`): discount every
  backtest by that gap.
- Results on the lock box's days, unless it is the final check of one candidate
  (`lab lockbox set <day>`; `--lockbox` to use those days, which the journal notes).
- Recorded turbos only begin on 2026-10-13: for a while every run rests on a few days.
  Say so in every conclusion.

## Writing Pine for WATA

- `//@version=6`, `indicator(...)`; inputs with `input.*()` (their ids are `in_0`, `in_1`… in
  declaration order); signals with `alertcondition(condition, "Title", message)`. The spec maps
  each title to `long`, `short`, `close-position`, `message` (the action is read in the alert's
  message, WATA's webhook JSON) or `ignore`: there is no `close-long` or `close-short`.
- One bar, one trade: a bar that raises an entry and a `close-position` sends the entry alone (it
  sells the other side first; after it, the close would sell the new position). The close is
  listed as `not_a_signal`, with why.
- The engine runs a day at a time from its restart (21:15 UTC) with `history_days` of bars before:
  an indicator that needs a long warm-up needs that setting raised (`--set history_days=3`), one
  that needs none runs faster with 0. A signal rule may have its own (0 to 30), which a run made
  from it takes.
- `R<ticks>` counts the asset's own tick: R60 is 6 points on Capital.com's US100 (0.1), 0.06 on
  Dukascopy's (0.001), where 6 points are R6000. A day of more than 40,000 bars is refused.
- Only the alerts count: a `strategy()`'s orders are not signals.
- No `barstate.islast`, nothing that repaints; `request.security` reads WATA's assets only.
- `lab script check` is PineTS, which is lenient: open the script in the lab admin's editor
  (TradingView's rules) when in doubt.

## Analysing the data

- `lab calendar check`: the macro calendar (FOMC, CPI, NFP, PCE) must be ahead of the days run;
  each row names its source. Never add a date that was not read from that source.
- `lab sql "<query>"` runs DuckDB over the research files. Each table of the server is a view of
  the same name over every day: `price_bar_10s`, `price_instrument` (the newest copy),
  `price_recorder_coverage`, `capital_bar_10s`, `capital_instrument`, `signal_journal`,
  `ta_signal`, `turbo_data_position`, `turbo_data_order`, `order_execution`, `order_request`
  (without the bodies), `account_balance_history`; Dukascopy's days are `dukascopy_bars` with
  their `label`. Times are UTC; the trading day is Europe/Paris.
- In Python: `from lab.sql import connect` gives the same DuckDB connection.
- A run's files are under `var/lib/lab/backtests/runs/<id>/` (`summary.json`, `trades.json`,
  `signals.json`, `days.json` with each day's `regime`, `edge.json`, `sweep.json`,
  `reconciliation.json` for a replay of the journal).

## Keep in mind

- The backtest repeats WATA's live rules (`src/backtest/`, held to the live code by
  `tests/test_backtest_parity.py`): a change to the trader or the monitor must be made there too.
- Nothing in the lab acts on the server. The lab's admin has operator actions switched off.
