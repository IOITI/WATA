"""
What the MCP server tells Claude about itself: its instructions (read at every
connection; Claude Code keeps 4,096 characters of them) and the ``research``
prompt (``/wata:research``), the loop of .claude/skills/wata-lab/SKILL.md
written for the server.
"""

# What WATA and this server are.
ABOUT = """\
WATA trades Saxo knock-out turbos on the Nasdaq 100 from signals. This server is its research side, on the live \
server: Pine scripts (run by PineTS, as WATA's TA engine runs them), the prices WATA recorded, and backtests that \
trade the recorded turbos under WATA's own rules."""

# What the endpoint may do and how its calls end (Claude Code's 60 s).
LIMITS = """\
Limits: nothing here trades, edits the configuration or takes a rule live; going live stays the operator's. \
Claude's runs wait behind the operator's; Pine studies run one at a time; large data comes as CSV, which Claude \
Code saves to a file on the Mac, to analyse there (data_bars: a week of 10 s bars a call). Every tool answers \
within 50 s: a study not done by 45 s comes back as a job, which job_wait answers."""

# The research loop, tool by tool.
LOOP = """\
Start with `status`, then `memory_list`: what WATA's AI agent and the operator kept between conversations (read \
what bears on the work with `memory_read`; only they change it). The loop:
1. `exp_new`: a hypothesis the data can refute, and its family. Every run and sweep counts as trials of the family; \
configurations tried elsewhere (a port on the Mac) too, once written down with `exp_trials`.
2. The strategy: `pine_check`, `pine_run` (plots and alerts on recorded bars), `pine_signals` (what a rule would \
raise), `pine_compare` (against TradingView's signals), then `pine_save`.
3. `backtest_start` (the Backtests form's body; `backtest_options` lists the settings; a run starts with \
1,000 € unless changes.capital says another), `backtest_wait`, then `backtest_result`: the edge of the entries \
first, then random entries, risk, regimes, trades.
4. Sweeps with walk-forward: read the held-out days before the chosen-on ones, then the probability of \
overfitting and the deflated Sharpe ratio.
5. Before keeping anything: neighbours and stress (`backtest_derive`); before trusting made-up turbos, a model \
check of a recorded run (`backtest_derive` kind model_check).
6. `exp_conclude`: kept, rejected or inconclusive, with what it rests on.
7. What survives may become one of Claude's shadow rules, once the operator switched them on: `rule_shadow_save` \
(the engine journals its signals, never sends them), a run made from it (`source.from_rule`) set as its \
reference (`rule_set_reference`), then its paper days in `rule_list`, and its report since it was made \
(`paper.report.run_id`, read as any run: made again after each finished day, no random entries)."""

# The bar a result must clear, mirrored by admin_ui/src/features/backtests/verdict.ts: a change to one is made in
# the other. The admin's AI agent (src/admin/agent/prompts.py) says it too.
DO_NOT_BELIEVE = """\
Do not believe: fewer than 30 trades or 20 days; random entries beaten less than 95 % of the time; no edge \
(t under 2.6 at the best horizon); a best value whose neighbours do badly, or a held-out result far below the \
chosen-on one; a probability of overfitting above 0.5 or a deflated Sharpe ratio under 95 %; a result that fails \
one stress step; results on the lock box's days, except for a candidate's final check. Recorded turbos start on \
2026-10-13: say on how few days each conclusion rests."""

# How a Pine script must be written for WATA's engine: the AI agent says it too.
PINE_FOR_WATA = """\
Pine for WATA: //@version=6 and indicator(); inputs are in_0, in_1… in declaration order; signals are \
alertcondition(condition, "Title", message), each title mapped to long, short, close-position, message or \
ignore. No barstate.islast, nothing that repaints; request.security reads WATA's assets only. A strategy()'s \
orders are not signals: only alerts are. A bar that raises an entry and a close-position sends the entry alone \
(it sells the other side first). R<ticks> counts the asset's own tick (data_assets): R60 is 6 points on \
capital/US100, 0.06 on dukascopy/us100, where 6 points are R6000. An engine day starts history_days before the \
daily restart: a rule may have its own (0 to 30), fewer for a script that needs little warm-up. A rule's bars \
are of any length from 10 s to a day, or range bars."""

INSTRUCTIONS = "\n\n".join((ABOUT, LIMITS, LOOP, DO_NOT_BELIEVE, PINE_FOR_WATA)) + "\n"

RESEARCH_PROMPT = """\
Work on WATA's strategies through the `wata` tools, on the live server's data, as follows.

1. `status`: what data there is (recorded days per source), the queue, the lock box and the open experiments. \
Then `memory_list`, and `memory_read` of what bears on the work: what WATA's AI agent and the operator kept.
2. `exp_new` before running anything: a title, a hypothesis the data can refute, and a family (the idea it \
belongs to). Every run counts 1 trial of the family, every sweep its combinations; the overfitting figures are \
corrected with that count. Configurations screened outside the server's runs (a port on the Mac, by hand) are \
written down with `exp_trials`, with what they were.
3. The strategy, a Pine v6 indicator: `pine_check` (errors with their lines), `pine_run` on a few recorded days \
(do the plots and alerts look right? alert_titles lists only some), `pine_signals` (what a signal rule would \
raise, day by day), `pine_compare` (against the signals TradingView sent), then `pine_save`. A study that takes \
longer than 45 s comes back as a job: `job_wait` it.
4. `backtest_start` with the experiment: the asset and bars the script reads, its inputs, what each alert \
becomes. `backtest_wait`, then `backtest_result` from the summary down: the edge of the entries first (no edge \
means the result came from WATA's exits and the days), random entries, risk, regimes, then `backtest_trades` \
and `backtest_days`.
5. Try values with a sweep (walk-forward on): the held-out column before the chosen-on one, then the \
walk-forward, the probability of overfitting and the deflated Sharpe ratio.
6. Before keeping anything: `backtest_derive` neighbours (a plateau, or a peak among worse values?) and stress \
(each order later, each fill dearer). Before believing a run on made-up turbos, a model check of a run on the \
recorded ones (`backtest_derive` kind model_check, then `backtest_result` part model_check).
7. `exp_conclude` with the verdict and what it rests on, then tell the operator: what was tried, what survived, \
on how many days. Recorded turbos only start on 2026-10-13.
8. A strategy that survives may become a shadow rule (`rule_shadow_save`, when the operator has switched those \
on): it journals its signals without sending them, and its paper days are set beside the backtest's range. \
Going live is the operator's alone.
"""
