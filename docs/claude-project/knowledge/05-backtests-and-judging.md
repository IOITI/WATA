# 05 · Backtests, and how to judge them

## What a backtest is in WATA

- **What it trades**: the turbos the price recorder kept, at their recorded bid and ask, under WATA's own rules. Those rules are the trader's entry checks in their order, its turbo choice and sizing, the monitor's exits, the day's close and the window around US releases. Parity tests hold the simulator to the live code. An entry with no recorded turbo at its moment is not traded (`unpriced`).
- **Signals** come from one of:
  - a Pine script, run as the TA engine runs a rule, engine day by engine day;
  - the signals WATA received (`journal`: TradingView's, up to 20 Oct);
  - the signals a rule already raised (`ta_signals`), with no script run again.
- **Fills**:
  - a signal reaches the trader 3 s after its bar closed (0.5 s for the journal);
  - an order is filled 1 s later, at the turbo's **next quote** (Saxo quotes again every ~30 s);
  - a stop crossed inside 10 s sells at the worst bid;
  - slippage and commissions are 0 unless set. **Set `"slippage": 0.005`** (03) and read the 0.01 stress.
  - `execution: "measured"` instead draws each order's delay and price from WATA's own orders.
- **Results** are percent counted as WATA counts a day: each trade the whole stake, compounded. Money comes from a 1,000 € account.
- **Random entries** (100 by default) trade the same days again. Each day gets as many entries as the strategy had, at random moments of its hours, long or short by a coin, on the same turbos under the same rules. "Beaten" is the share of those the run did better than. WATA's exits decide most of a trade: near 50 %, the entries add nothing.
- **The edge** is how the index moved 1 to 60 min after each entry signal, signed by its direction, against random moments of the same hours. Its t-statistic counts only signals whose windows do not overlap. **Read it first**: no edge means any result came from the exits and the days.
- **Regimes**: each day's kind (gap, range, volatility, trend or chop, the US open, macro news), and the trades by kind, hour, part of the session and minutes after the US open.
- **Risk**:
  - Sharpe, Sortino and Calmar, annualised only from 20 days;
  - time under water, the worst 5 % of days, payoff, how much of its best a winning trade kept;
  - stress: the same days drawn again, and the total without the best days.

## A request (`backtest_start`, after `exp_new`)

```json
{
  "from": "2026-10-16", "to": "2026-10-29", "indice": "us100",
  "source": {
    "kind": "pine",
    "script": {"id": "<script id>", "version": 1},
    "asset": {"provider": "capital", "key": "US100"},
    "side": "bid",
    "bars": {"type": "range", "ticks": 60},
    "inputs": [{"id": "in_0", "value": 20}, {"id": "in_1", "value": 80}],
    "alerts": [
      {"title": "Enter Long", "action": "long"},
      {"title": "Enter Short", "action": "short"},
      {"title": "Exit", "action": "close-position"}
    ],
    "confidence_plot": "Confidence"
  },
  "changes": {"slippage": 0.005},
  "baseline": {"repetitions": 100}
}
```

- **Bars**: `{"type": "time", "timeframe": "1"}` for minutes, `"10S"`/`"45S"` for seconds, `{"type": "range", "ticks": 60}` for range bars. The Pine study tools (`pine_run`, `pine_signals`) take a string instead: `"1"`, `"10S"`, `"R60"`.
- **Settings** go by name in `changes`. `backtest_options` lists them with their live values and bounds:
  - entries: hours, news window, cooldown, limits, breakers, minimum confidence;
  - day: target, no-entry level, day loss;
  - exits: stop, take-profit, trailing stop;
  - turbo: price band, spread limit;
  - sizing;
  - execution: capital, delays, slippage, fills;
  - engine: `history_days`.
- **A sweep**: `"sweep": {"axes": [{"kind": "input", "name": "in_1", "values": [40, 80, 120]}, {"kind": "setting", "name": "day_profit_target", "values": [0.5, 1, 2]}], "walk_forward": {"train_days": 5, "test_days": 2}}`.
  - Two axes, 64 combinations and 12 values an axis at most.
  - The last 30 % of days are held out (`held_out`, 0.1–0.5).
  - A sweep has no random entries.
- **A journal replay**: `"source": {"kind": "journal"}`, plus `"chart": {...}` naming the asset the replay is read on.
- **Modelled turbos**: `"turbos": "modelled"`, on Dukascopy's index for up to 731 days. The Pine asset is `{"provider": "dukascopy", "key": "us100"}`, with range bars a hundred times more ticks (R6000 for 6 points).
- **A run made from a rule**: a Pine source with the rule's script, asset, bars, inputs and alerts, plus `"from_rule": {"id": "<rule id>", "name": "<rule name>"}`. It takes the rule's own `history_days`, and it is the kind of run `rule_set_reference` makes a reference.
- **The signals a rule already raised**: `"source": {"kind": "ta_signals", "rule": {"id": "<rule id>"}}`.

Then:
- `backtest_wait` (45 s at a time);
- `backtest_result`, by part: `summary`, `edge`, `baseline`, `breakdowns` (`group`: hour, weekday, direction, exit, holding, turbo_price, regime…), `sweep`, `reconciliation` (journal replays), `model_check`, `settings`;
- `backtest_trades`, `backtest_days`, `backtest_signals` (each signal's fate: taken, refused and why, unpriced), and `backtest_compare` (up to four runs).

## The checks before keeping anything (`backtest_derive`)

- `neighbours` of a chosen value (a setting or `in_N`): a plateau, or a peak among worse values?
- `stress`: each order later and each fill dearer.
- `cell`: a sweep's combination made a run of its own, with random entries.
- `model_check`: a recorded run made again on modelled turbos, to see how far apart they are. Do it before trusting any modelled run.

## Do not believe

The same bar as WATA's pages and its AI agent:
- fewer than **30 trades** or **20 days**;
- random entries beaten less than **95 %** of the time;
- no edge: **t under 2.6** at the best horizon (the best of five horizons is picked, so the 5 % is shared among them);
- a best value whose **neighbours** do badly, or a **held-out** result far below the chosen-on one;
- a **probability of overfitting above 0.5**, or a **deflated Sharpe ratio under 95 %** (every trial of the family is counted);
- a result that fails **one stress step**;
- results on the **lock box**'s days, except for a candidate's final check;
- modelled turbos not checked on the recorded days, or measured on few days;
- a journal replay far from WATA's real trades: discount it by that gap.

Say on how many days and trades each conclusion rests.

## Traps seen in WATA's own research

- **The day target makes knife-edge results.** With the whole account and a +0.5 % target, most winning days end exactly at +0.5 %, and a quarter of a cent per fill turns some into −14 % days. The kernel/VWAP rule made +4.67 % at slippage 0, −23.5 % at 0.0025 and −37.5 % at 0.01. Always read the cost stress.
- **Nine days decide nothing.** A day-loss cap that halved the losses on 9 recorded days lost on 60 modelled ones. A +1 % target that beat 87 % of random entries on 9 days was negative on every held-out cell of 60.
- **Paper days are traded at slippage 0**, and overstate a rule.
- **Map exit titles with care.** A script that raises an entry and the opposite exit on a reversal bar, both mapped, would sell what it just bought. One bar, one trade now drops that close, but an exit title firing on its own at the wrong moment still sells.
- **Count the ticks.** R60 on Dukascopy is 0.06 points: hundreds of thousands of bars a day, killed for memory. Use R6000 there.
- **Compare runs of the same code.** Runs made by older backtest code are marked stale; compare runs with the same code digest (`status`).
- **Mind the budget.** A request may simulate 10,000 trading days: its days × its sweep's combinations, or × (1 + random repetitions). A 60-day modelled run with 100 repetitions is 6,060.
- **A sweep over the signal delay** gave every cell the same signals before v0.37.0. Today it shifts them, but runs of that axis made before are void.
