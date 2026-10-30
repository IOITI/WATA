# 06 · Research memory

*What was tried, what it showed, what is open. Last updated: 30 Oct 2026.*

This is the project's memory. At the end of a research session ("update the research memory"), Claude writes the entries to add, in the template at the end, and the operator uploads the file again. The full records stay on the server: the experiment journal (`exp_get`) and WATA's AI agent memory (`memory_list`, `memory_read`).

## Where things stand

- **No live signal source.** TradingView has been off since 29 Oct; no rule is live; the last real trade was on 20 Oct.
- **One rule in shadow**: the operator's kernel/VWAP rule `86fc6f91463e` (script `0d4fd1117e2f` v10, R60 on capital/US100, bid). No reference run is set.
- **Its paper days**, at slippage 0, so optimistic:

  | Day | Result | Trades |
  |---|---|---|
  | 21 Oct | 0 % | 0 |
  | 22 Oct | +0.51 % | 2 |
  | 23 Oct | −11.30 % | 28 |
  | 26 Oct | +0.52 % | 1 |
  | 27 Oct | +0.50 % | 1 |
  | 28 Oct | +0.50 % | 1 |
  | 29 Oct | +0.52 % | 2 |
- **Its report**, 21–29 Oct (7 days): −9.01 % in 35 trades, 40 % won, worst drawdown −11.3 %, sample "too few". The newest report is `rule_list` › `paper.report.run_id`.
- **No strategy has shown an entry edge on WATA's data yet.**
- **15 experiments concluded**: 2 kept (both about execution realism), 3 inconclusive, 10 rejected.
- **Trials by family**: kernel-avg-vwap 61, wata-day-design 28, signal-latency 9, wata-day-guards 8, execution-realism 2, execution-model 1. Another 35 variants of the kernel/VWAP script were screened on the Mac on 21 Oct and never recorded with `exp_trials`: that family's true count is about 96.

## What we know (durable lessons)

1. **Costs decide everything at WATA's scale.**
   - Backtest slippage: 0.005 € per turbo as normal, 0.01 as the stress (real fills against recorded quotes, 21 positions, 13–20 Oct). Not 0.036: that figure is measured against WATA's sometimes stale price.
   - A round trip costs about 0.02 € at the usual spread and 0.045 € at the wide one: 0.4–1 % of a 4–5 € turbo.
2. **The +0.5 % day target hides losing days.** Most winning days end at exactly +0.5 %, so results are knife-edge: the kernel/VWAP rule went from +4.67 % at slippage 0 to −23.5 % at 0.0025 (16–28 Oct). Its break-even cost is under 0.0025 € a fill.
3. **Losses come from chop.** Many flips around the VWAP on back-and-forth days: 31 entries on 23 Oct for v9; 11–15 real trades on 9, 14 and 19 Oct. Fewer, cleaner entries are the first thing a strategy needs here.
4. **Nine days decide nothing.**
   - A −5 % day-loss cap beat −25 % in 8 of 8 cells on 9 recorded days, then lost on 60 modelled days.
   - A +1 % target beat 87 % of random entries on 9 days, and was negative on every held-out cell of 60.
   - Check day settings over a long period (modelled turbos) before proposing them.
5. **Delays and stale quotes matter more than seconds of latency.** A journal replay at 0.5 s with fixed fills beat WATA's real trades by about +0.67 points a trade; 15–30 s added matched them. Saxo quotes a turbo every ~30 s, so exits act on old prices, and stops fill far beyond their level.
6. **News.** A long bought 3 min before the CPI of 14 Oct went from +5.2 % to −29.4 % before the −7 % stop acted: −30.2 %. WATA's window around releases (5 min before, 15 after) would have refused it. It is still off: turning it on is the operator's switch.
7. **WATA cannot reproduce TradingView's kernel/VWAP signals.**
   - WATA has no Skilling or PurpleTrading feed.
   - Its range bars are built from 10-s bars, so the candle-risk tests never fire.
   - The copy paired 28 of 55 TradingView entries and 1 of 122 closes (16, 19, 20 Oct).
8. **Modelled turbos are weak evidence for now.** The model is measured on 4 days, and a model check of 13–16 Oct gave a per-trade correlation of only 0.22. Measure it again and check it before trusting long runs.
9. **Spread regimes.** Usually 0.009–0.010 €; 0.028–0.036 € for hours on some days (the morning of 29 Oct; before the FOMC of 28 Oct); ~0.11 € at the FOMC and the US close of 28 Oct.

## Experiments (newest first)

| Date | Id | Family | Question | Verdict | What it rests on |
|---|---|---|---|---|---|
| 29 Oct | 32b100 | wata-day-design | Day-loss cap −5 % and +1 % target over ~60 days (modelled) | rejected | Live cells: −18.33 % in 173 trades; (−25, 1) +4.35 %; (−5, 1) +5.31 %; every cell negative on the held-out days from 30 Sep; walk-forward −31.19 % over 40 days; DSR 60 % |
| 29 Oct | 255ad9 | wata-day-design | Let the winner run (target T) and cap the day's loss | inconclusive | 9 recorded days: cap −5 beat −25 in 8/8; T 1 made +5.15 % in 11 trades (beat 87 %); PBO 0.886 |
| 29 Oct | 557d6b | kernel-avg-vwap | v9 and the dead zone at slippage 0.005; break-even cost | rejected | 21–28 Oct: dead zone −25.61 % in 32 trades (beat 45 %), v9 −61.05 % in 122 (beat 31 %); break-even under 0.0025 €/fill |
| 29 Oct | 771d9c | execution-model | Real fills against the recorded quotes | kept | 21 paired positions, 13–20 Oct: real − replay median −0.003 €, mean +0.005 €; totals −27.57 % real, −29.44 % replayed |
| 29 Oct | 198fcd | wata-day-guards | Stop the day after N losses in a row | inconclusive | 9 days at slippage 0.036 (overstated): every cell loses; N 1 cuts losses most (v9 −29 % vs −78 %); walk-forward −22.7 % over 4 days; PBO 0.2; not proposed |
| 29 Oct | f9b0d6 | kernel-avg-vwap | A dead zone around the average VWAP against v9's whipsaw | rejected | 21–28 Oct at slippage 0: +3.09 % in 7 trades vs −20.43 % for v9; neighbours jagged; beat 74.5 %; no edge. Applied anyway as v10 (in_28 0.75) for its shadow |
| 24 Oct | 439d40 | signal-latency | What a slower signal costs (journal + 0.5/15/30/60 s) | inconclusive | 9 days: 0.5 s +4.67 %, 15 s −40 %, 30 s −44 %, 60 s −36 %; the gap is two days decided by the day target and a bad quote; no horizon reaches t 2.6 |
| 21 Oct | f59bc8 | kernel-avg-vwap | E7: WATA's copy over 89 days of Dukascopy, modelled turbos | rejected | No edge (t ≤ 0.46); −99.75 % with every random run too; model check r 0.22 |
| 21 Oct | 5fd63f | kernel-avg-vwap | E6: TradingView's live signals on the recorded turbos | rejected | 13–20 Oct −73.9 % at slippage 0.036 (real −49.7 %), beat 10 %; 1-min edge +1.66 bp, t 2.13: under the bar, and gone by 5 min |
| 21 Oct | 5034a1 | kernel-avg-vwap | E5: is the copy faithful (live inputs, CandleRiskActivity v3)? | rejected | 28/55 entries, 1/122 closes paired |
| 21 Oct | 6a5787 | kernel-avg-vwap | E4: 35 variants, and WATA's damage limits | rejected | Mac port, 90 Dukascopy days, net of 9.4 points a round trip: no variant positive on both halves, best t 2.46. Best by the worse half: min kernel slope 0.3 × ATR(14), −4.55 / −4.03 points a trade net (+4.85 / +5.37 gross, 8.1 and 5.0 trades a day). On the server, 3 days: −29.53 %, and −8.74 % with damage limits (5 trades a day, −5 % day, 15 min after a loss), against −42.28 % |
| 21 Oct | 1d07b6 | kernel-avg-vwap | E3: v1's signals over 90 days of Dukascopy | rejected | Server runs died (range too small for Dukascopy's tick). Mac port: no edge at 1–60 min (best t 0.46); +1.98 points a trade gross, −7.42 net on the tuning days; −0.52 gross, −9.92 net held out |
| 21 Oct | 3fc111 | kernel-avg-vwap | E2: v1 on the recorded turbos beside TradingView's signals | rejected | 3 days, slippage 0.036: v1 R60 −42.28 % in 34 trades (beat 46 %, best t 0.4); v1 10S −36.08 %; TradingView's own signals −32.61 % (beat 92 %, 1-min edge t 2.13). Found the mapping trap (05) |
| 21 Oct | 2fe435 | kernel-avg-vwap | E1: WATA's replay of v1 against TradingView's signals | rejected | 29 of 55 TradingView entries paired (53 %) over 3 days; stand-ins for the missing providers changed nothing; TradingView's 40–45 daily closes come from a risk test that never fires on WATA's bars |
| 21 Oct | 5f0d64 | execution-realism | Do replays match real trades? | kept | Replay vs real −67.4 vs −66.4 %, r 0.91 at slippage 0.036. Since refined by 771d9c: 0.005 is the right slippage |

The experiments of 21 Oct charged 0.036 € a fill, about 9.4 index points a round trip. That is two to four times the cost measured since (03): their "net" figures overstate the losses, though their edge figures (index moves) do not depend on it.

## The operator's kernel/VWAP rule (what it is)

"Kernel + Avg multi VWAP Context light", script `0d4fd1117e2f`: v1–v5 on 21 Oct, v6–v9 on 25 Oct, v10 on 29 Oct. It runs on R60 bars of capital/US100, bid, with `history_days` 1.
- **The "average VWAP"**: a session VWAP anchored at 08:00 Paris, averaged with two daily VWAPs of the same feed. The other providers of the TradingView era are gone, so this is no longer a consensus of providers.
- **The kernel**: a Nadaraya-Watson kernel regression of the close (Wave kernel, bandwidth 8), from the `KernelFunctions` library (`868c3d621d68`).
- **Entries**: Enter Long when the kernel crosses above the average VWAP plus a dead zone (in_28 = 0.75 × the VWAP band's width); Enter Short when it crosses below it minus the dead zone. Only from 08:00 to 21:55 Paris, and only once the VWAP band has been 20 points wide in the session.
- **Exit ("Global Exit" → close-position)**: a reverse cross out of the dead zone; the session's end; or a Supertrend turning (ATR period in_18 = 1, multiplier in_19 = 6) while beyond the average VWAP.
- **Confidence**: computed (kernel slope, VWAP penetration, provider agreement, kernel direction) but not sent, so every entry is sized 100 %.
- **Edge**: none at any horizon (t ≈ 0 on 109 entry signals, to 23 Oct).

Claude's scripts around it:
- `90dc8ae01bab`: the dead-zone copy of v9;
- `3384236bf5ac`: a copy of the TradingView indicator;
- `b28e1aae5c2a`: the CandleRiskActivity v3 library;
- `868c3d621d68`: the KernelFunctions library.

## The operator's decisions that frame the research

- Study **WATA's own scripts and rules**, not TradingView's signals (29 Oct). Journal replays of days up to 20 Oct replay TradingView's signals: use them to measure execution, never to judge a strategy the operator runs now.
- **Shadow first.** Settings, the news window and going live are the operator's decisions; Claude proposes, with evidence.
- **Runs start from 1,000 €.** Paper reports are made every night without random entries.

## Open questions and ideas not yet tried

Each idea is tagged with the layer it changes.

**Pine signal** (Claude can test these now):
- **Test again, at the corrected cost, the "min kernel slope 0.3 × ATR(14)" variant of E4.** It made +4.85 / +5.37 points a trade gross on the two halves of 90 Dukascopy days, at 5–8 trades a day. Net of 9.4 points it lost; at the measured 2.3–5 points a round trip it may not. It had no edge at t 2.6, so it is a lead, not a finding: run it on the recorded turbos at slippage 0.005, edge first. The operator's script declares "Min Kernel Slope" (in_11) but never uses it, so a Claude copy must implement the filter as the port did: entries only while the kernel's slope is at least 0.3 × ATR(14).
- **A regime filter against chop**: trend strength, VWAP band width, range-bar speed, a choppiness index. Trade only when the market trends.
- **Avoid the wide-spread hours**: no entry on mornings when the spread is ~0.03 €, or only US-session entries. Compare the costs in the trades' breakdowns.
- **Calibrate confidence**: send it, then check that the breakdown by confidence shows the high band doing better.
- **Long/short asymmetry**: shorts are bought at 8–12 € and longs at 4–5 €, so their leverage differs. Breakdowns by direction.
- **The first half hour after the US open**: regimes show `minutes_after_open`.
- **Let WATA's exits work alone**: map the rule's exit title to `ignore` and compare with the signal exit. Signal closes lost −139.8 % in the live record.

**WATA risk rules** (the operator's settings):
- the news window on;
- a spread limit at entry (`max_spread_percent`);
- a larger day target with the trailing stop, tested over 60+ days once the turbo model is measured again.

**Execution and infrastructure** (code, in Claude Code):
- exits read from Capital.com's index between Saxo's ~30-s turbo quotes;
- tick-built range bars, which need another feed;
- partial exits.

**Data**:
- measure the turbo model again over every recorded day (`turbo_model_measure`) and check it (`model_check`) before any long-history work;
- record the 35 Mac-screened kernel/VWAP variants of 21 Oct with `exp_trials` if that family is studied again.

## Template for new entries

```
### YYYY-MM-DD · <title> (exp <id>, family <family>)
- Question: <the hypothesis, as one sentence the data can refute>
- Data: <asset, bars, period (n days, recorded or modelled turbos)>, costs <slippage, delays>
- Runs: <run ids>
- Result: <total %, trades, random entries beaten %, edge t at the best horizon; PBO and DSR for a sweep; stress step>
- Verdict: kept | rejected | inconclusive, because <...>
- Next: <what it leads to, or nothing>
```

When a lesson above proves wrong, correct it in place and say so in the new entry.
