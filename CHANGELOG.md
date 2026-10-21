# Changelog

## v0.26.3 - PineTS 0.12.0, Vela 0.8.5 and Vela-pinets 0.2.17

**Release date:** 2026-10-21, with v0.26.1 and v0.26.2

The three LuxAlgo packages of the Technical Analysis pages and the TA engine, released on 2026-10-19, pinned exactly as before (`admin_ui/package.json`). Nothing else changes in the lock file; their licences and Vela's NOTICE are the same.

- **PineTS 0.12.0** (from 0.11.1) mostly rebuilds how a `strategy()` fills its orders (TradingView's path inside a bar, margin, risk rules), which WATA does not use: only alerts make signals. Beside it come fixes that bring scripts closer to TradingView: `ta.cross*` inside a lazy `and` or `or`, `ta.*` inside local blocks and on sources that start with `na`, `request.security` in a function called from several places, `(expr)[n]` inside `and`, `or` and `?:`, `x := switch`, `math.round_to_mintick`, `varip`. They can change what another script raises; they did not change what the Kernel VWAP scripts raise (below).
- **Vela-pinets 0.2.17** (from 0.2.16) carries PineTS 0.12.0 in its worker, the one the Charts page runs scripts in, and now moves a `plot()` by its `offset`, which it drew unshifted.
- **Vela 0.8.5** (from 0.8.4): panes keep the height they were dragged to when an indicator is added, an overlay can be brought in front of the candles from its legend, built-in studies wait for the whole history before they compute instead of sliding while it loads, and a plot shifted forward draws past the last candle. Linear Regression becomes two studies, Least Squares Moving Average and Linear Regression Slope; a saved chart opens with the one it had.
- **WATA's three changes to the packages at build time still find their code**, unchanged: seconds on Vela's chart (`vite/velaSeconds.ts`), a formatter per time zone in each copy of PineTS (`vite/pinetsZoneFormat.ts`), Vela-pinets' worker as a file of its own (`vite/velaPineWorker.ts`). The runner's hello says `0.12.0+zone-format`.

**On the live path**: the TA engine's runner is built with PineTS 0.12.0 at the deploy, so every signal rule, live or shadow, runs on it from then on: on the scripts compared below its alerts are the same as 0.11.1's, and it is about 5 % slower. The trader, the web server and the position monitor are untouched, and TradingView's alerts never go through PineTS. Backtests made before are not marked stale (the code digest covers the Python code; each run names its PineTS in `engine.runner`), and the runner's day cache starts empty after the deploy: its keys hold the runner's build.

**Verification status:**
- The interface's 843 tests pass, the live strategy's among them (it reads `wata-tradingview-scripts`); lint, type checks, the build, `build:runner` and `build:checker` are clean, and `python -m src.ta_engine.runner` answers with PineTS 0.12.0. The Python suite passes with the new runner and checker built (2,285 tests; those that need a PostgreSQL, a RabbitMQ or `pgserver` skipped, as without them before).
- The same signals: `python -m src.ta_engine.replay` run with the runner built from 0.11.1 and from 0.12.0, on the three Dukascopy days of this Mac (2026-09-15, 09-17 and 09-22), for the lean and the live copies of the Kernel VWAP script (the live one's providers on the chart's own series) and `kernel-vwap.pine`, on R6000, 1-minute and 30-second bars: 21,157 alerts, every one and every signal the same. The same replay took 8.3 s with 0.12.0 and 7.9 s with 0.11.1.
- In a browser, against the fake admin built with them: Capital.com's US100 live on 10-second bars with the seconds on the crosshair, a Pine script and its alerts drawn through the new worker file, the editor's test, a built-in study of Vela drawn at once.

**Not seen:** the server; Capital.com's real prices and the recorded turbos with PineTS 0.12.0; Vela's new pane and layer controls.

## v0.26.2 - Long Pine backtests no longer run out of memory, and research gets more of the server

**Release date:** 2026-10-21, with v0.26.3

### The Pine runner

- **The Pine runner compiles a rule's script once while its source and inputs stay the same** (`load` in `admin_ui/src/features/ta/engine/runnerCore.ts`), where it compiled it at every load. Each PineTS compile leaves native memory behind that the process never gets back, about 60 MB for the Kernel VWAP script. That memory lies outside V8's heap, so `--max-old-space-size` does not bound it: with a heap of 512 MB instead of 1,024 the runner grew the same way, to 1.3 GB. A backtest loads its script again for each engine day. On the server the runner of the 90-day Kernel VWAP run was killed by the kernel every 10 to 20 engine days in the admin's 1,536 MB, and the run finished only on its tenth attempt, each one reading the days already done from the day cache. Each day also ran slower than the one before, 2.8 s on the first and 9.7 s on the thirtieth of the same size.
- Now, over 90 engine days of that script, the runner holds 405 to 450 MB and runs a day in about 1 s.
- **The TA engine's runner gains too**: the engine loads each rule again at its daily restart, with the same script and inputs.

### Memory limits for a server of 8 GB

WATA used about 1.25 GB of the server's 8 GB at rest, and the two limits that were reached were the admin's and PostgreSQL's (`deploy/docker-compose.yml`):

- **The admin container gets 3 GB** (1.5 GB before). A backtest, its Pine runner and one of Claude's Pine studies fit side by side. It keeps its one processor, so the services that trade keep the rest.
- **PostgreSQL gets 1.5 GB** (512 MB before) and settings of its own, where it ran on the image's defaults: `shared_buffers` 512 MB (128 MB), `effective_cache_size` 1 GB, `work_mem` 16 MB (4 MB), and a `/dev/shm` of 256 MB (64 MB) for the memory parallel queries share. More of the price tables that backtests and the Data pages read stays in memory.
- **A Pine study gets a heap of 768 MB** (`admin.mcp.study_memory_mb`, 384 before, still 128 to 1,024).
- **The free memory a study waits for leaves out the file cache the kernel takes back** (`inactive_file` of the container's `memory.stat`, `cgroup_memory` in `src/admin/studies.py`). The price files and the runner's day cache the admin reads filled its memory with cache, and a study could be refused with nothing really short. A run that dies says the admin's memory then on the same measure.
- The other containers keep their limits; the trader has none.

**On the live path**: the TA engine's runner keeps a rule's compiled script when the rule is loaded again unchanged; a rule with another version or other inputs is compiled again. The deploy recreates the PostgreSQL container with its new settings, so the database restarts: deploy while the market is closed. The trader, the web server and the position monitor are untouched. Backtests give the same signals and are not marked stale (the code digest covers the Python code), but the runner's day cache starts empty after the deploy: its keys hold the runner's build.

**Verification status:**
- A test of the runner core (`runnerCore.test.ts`): a rule loaded twice with the same script and inputs makes one indicator and answers what a fresh compile answers (alerts, plots, inputs); other inputs, or another rule, are compiled for themselves. It fails without the change. 843 tests pass in the interface's suite; lint, type checks, the build, `build:runner` and `build:checker` are clean.
- Measured on this Mac with the built runner and the Kernel VWAP scripts (the lean copy and the full one with `request.security`) on 30 to 90 made-up Capital.com days of 5,300 R60 bars: the same alerts on every day before and after the change, memory flat, a day in 0.9 to 1.7 s. Before it: 1.2 GB and 9.7 s by the thirtieth day.
- `tests/test_deploy_compose.py` holds both containers' limits and PostgreSQL's settings; a test reads the container's memory with and without its inactive file cache.

**Not seen:** the server. After the deploy:
- the limits: 3 GiB for `admin1` and 1.5 GiB for `postgres` (`docker stats`), `SHOW shared_buffers` 512MB in the database;
- a Pine backtest of more than three weeks finishing at its first attempt, the runner's peak (`runner_peak_mb` in its progress) under about 500 MB;
- `admin.mcp.study_memory_mb` in the server's `config.json`: where it says 384 (the example's value when the deploy of v0.23.0 added `admin.mcp`), it stays 384 until it is changed to 768 or removed. The MCP `status` tool shows the value in use (`limits`).

## v0.26.1 - certifi no longer trusts the compromised emSign root

**Release date:** 2026-10-21, deployed with v0.26.3

- **certifi 2026.10.19** (from 2024.7.4), for GitHub's critical advisory GHSA-6q48-6c7w-9gw2 (Dependabot alert 46 on `main`). The bundle `requests` and `httpx` verify every HTTPS connection with trusted "emSign ECC Root CA - G3", eight of whose intermediate certificates had their private keys compromised: whoever holds one, and sits on the network path, could pass for any site. WATA's connections that go through it are Saxo's REST API and login (the trader's balances and orders), Capital.com's API, Telegram and Dukascopy. The new bundle drops that root and keeps the three emSign roots not affected (120 certificates where there were 147).
- Nothing else changes: certifi needs no other package, and `requirements.txt` stays the whole environment (`tests/requirements_lock.py`).

**On the live path**: the image's library layer is rebuilt at the next deploy; no code changes.

**Verification status:**
- A fresh environment of `requirements.txt` is exactly what it lists, and 2,281 tests pass in it (1 skipped), on Python 3.12 with Node 24 on the PATH.
- With the new bundle, TLS verifies to every outside host WATA names, as with the old one: `gateway.saxobank.com`, `live.logonvalidation.net` and `live-streaming.saxobank.com` (DigiCert), `api-capital.backend-capital.com` (GlobalSign), `api-streaming-capital.backend-capital.com` (Amazon), `api.telegram.org` (GoDaddy), `datafeed.dukascopy.com` (ZeroSSL).

**Not seen:** the server. At the deploy:
- the image's certifi: 2026.10.19;
- the image's Debian store, which Saxo's and Capital.com's streams verify with (`websockets`, not certifi): whether `/etc/ssl/certs/ca-certificates.crt` still holds "emSign ECC Root CA - G3". The `python:3.14` base is pulled again at each build, so Debian's fix arrives with it;
- pip's own copy of certifi, used only while the image is built.

## v0.26.0 - Signal rules on seconds bars, model checks, and trials made elsewhere

**Release date:** 2026-10-21, with v0.24.0 and v0.25.0

The gaps marked "later" after the first research session through the MCP endpoint (2026-10-21).

- **Signal rules run on bars of any length**, live and shadow, from 10 seconds to a day: the Signal rules dialog offers 10, 20, 30 and 45 s and "Other length…", as the Backtests form does. One check for every rule, backtest and replay (`checked_bars`). The engine was already able to: it ticks every 10 s and builds bars of any length. A rule on 10-second bars runs its script over up to 8,300 bars a day at each new one, about what one on R60 does. A signal its run delays past `max_signal_age_seconds` (60 s) is not sent, journaled with why; Claude's shadow rules stay held to `admin.mcp.shadow_day_ms` an engine day. `rule_shadow_save` takes them (`bars="10S"`).
- **A model check**: a finished run on the recorded turbos made again on turbos made up from the index, without random entries, and its trades paired with the recorded run's by signal. It says how far apart their results are, the correlation, the same exit and both totals. "Check the turbo model" on the run's page (once the turbos are measured), MCP `backtest_derive` kind `model_check` (no trial counted) then `backtest_result` part `model_check`. The comparison is the lab's own (`compare_trades`); the check's folder keeps the recorded run's trades (`recorded_trades.json`) and writes `model_check.json`.
- **Trials made elsewhere count in the journal**: MCP `exp_trials` and `python -m lab exp trials <id> --count N --notes "…"` write down configurations tried outside the runs (a port screened on the Mac, by hand). They count in the family's trials, so in later sweeps' overfitting figures, and the Experiments page lists them with what they were. The 35 variants of the first session can now be counted. 39 tools.
- **The MCP's day budget counts trading days**: weekdays outside the live closed dates, where it counted every calendar day. 120 days with 100 random repetitions fit within `max_day_simulations` (about 86 × 101 = 8,686 of 10,000), where 81 repetitions did before.
- **Every backtest starts with 1,000 €** unless it says another (`DEFAULT_CAPITAL`, 10,000 € before, used when no balance was known): the live balance is no longer handed in, but offered beside it ("start from it" under the form's account; `backtest_options`). At 21.82 €, 34 entries of the session found no turbo to buy. Paper days start with it too; runs made before keep theirs.
- **Not done**, as decided: a second live price source for multi-provider scripts (`request.security` reads Capital.com only).

**On the live path**: the TA engine accepts rules on seconds bars, inert until one is made; the check of a rule's bars is shared by the admin and the engine. The trader, the web server and the position monitor are untouched. Every backtest made before shows as stale (the code digest); its results are unchanged at the same capital.

**Verification status:**
- 2,281 tests pass in Python's suite, 1 skipped, on Python 3.12 with Node 24 on the PATH. Not run on Python 3.14 here: CI runs both. 842 pass in the interface's suite; lint, type checks, the build, `build:runner` and `build:checker` are clean.
- **At the same capital, a run gives the same files as v0.25.0's code**: a run of the EMA cross on the fake admin's recorded days (10 trades, 10 random repetitions), replayed by both versions on the same files. Trades, signals, days, random entries and edge are identical; the summary differs only in `made_with` and the new `model_check` (empty).
- Through the MCP SDK's client, on the fake admin (`FAKE_MCP=1`):
  - `exp_trials` wrote down 35 trials made elsewhere, and the next sweep's `trials_before` was 36;
  - a run without a capital started with 1,000 €;
  - a model check derived from it ran and paired its 6 made-up trades with the recorded ones by signal (`backtest_result` part `model_check`), on a made-up measure;
  - 120 days of made-up turbos with 100 random repetitions were accepted;
  - a shadow rule on 10-second bars was saved after its first run, 8,250 bars in 83 ms.
- In the browser pane:
  - the recorded run's page shows the check with what its trades say, and the check's page its badge, its link and the same sentences;
  - the Experiments page shows "+35 tried elsewhere" and 39 in the family;
  - the Signal rules page lists the 10 s rule;
  - the form's options give 1,000 € with the live balance beside it (the "start from it" link is the interface tests'; the pane, hidden, held the form's steps).
- The engine's tests run a live rule on 10-second bars (sent at the tick after its bar, refused 75 s late) and refuse a rule edited by hand into a length WATA does not make.

**Not seen:** the server. After a deploy:
- a 10 s shadow rule's journal and paper days, and the engine's time per run with it;
- a model check through Claude Code on the server's recorded days, once the turbos are measured again on more days than the four the model rests on;
- `exp_trials` for the 35 variants of the first session, in an open experiment of the family `kernel-avg-vwap` (6a5787, where they were screened, is concluded and refuses them).

## v0.25.0 - No entry around US releases, backtests on WATA's own fills, and MCP calls that end in time

**Release date:** 2026-10-21, with v0.26.0

The gaps marked "soon" after the first research session through the MCP endpoint (2026-10-21).

- **No entry around US releases, once the rule is added** (`macro_news` in `trade.rules`, added and removed from the Configuration page's "US releases" section; absent, nothing changes). From 5 minutes before CPI, the jobs report (NFP), PCE or an FOMC statement to 15 minutes after, and to 45 after an FOMC statement, for its press conference: `{"events": ["FOMC", "CPI", "NFP", "PCE"], "minutes_before": 5, "minutes_after": 15, "minutes_after_fomc": 45}`, which `ConfigurationManager` checks (known releases, whole minutes from 0 to 120, 180 after FOMC).
  - The trader checks it right after the trading hours, before anything is sold or bought, and says why: "Breaking trading rule: no entry around CPI (14:30) from 14:25 to 14:45." A release is at New York's hour whichever side changed its clocks (CPI is at 13:30 in Paris on 11 March). Closes, the scheduler's close of the day and open positions are untouched.
  - The calendar moved to the trading side (`src/trade/macro_calendar.py`, `src/trade/data/macro_calendar.csv`; the backtests and the lab read it from there). The trader reads it once at its start, since it changes only with a deploy; one that cannot be read never stops it: it trades without a window and says so in its log.
  - Past the calendar's last day nothing is known, so the trader trades on. From 30 days before that day, while the rule is on, the scheduler sends a line to Telegram each morning at 09:00 and the admin's health has a "US releases calendar" warning: "The calendar of US releases ends on 2026-12-23, in 30 day(s): update it (python -m lab calendar check), then deploy". PCE is known to 23 December 2026, the others to December 2027.
  - The dashboard's Next entry tile says the window in force ("CPI at 14:30: no entry until 14:45") or the next one of the day (`news` in `/api/overview`), as the trader would (`entryState.ts`).
  - Backtests repeat it at the trader's place and with its words: `news_window` and the `news_*` settings, read from the rule as the services read it, so off by default and in every run made before. `tests/test_backtest_parity.py` holds the two to the same refusals: inside, both edges, FOMC's 45 minutes, outside, past the calendar's end.
- **A backtest may draw its orders from WATA's own** (`execution: measured`, "Order times and prices" in the Backtests form; `fixed`, the default, is unchanged). The service reads WATA's orders of the last three months with the execution measure's SQL and keeps them with the run (`execution.json`). Each order's time to its fill, and its price against the turbo's quote when it left, are drawn from them, seeded by seed, repetition and day as the random entries are: a run made again draws the same. The random entries draw the same way, and a stress step adds its delay and price on top. Fewer than 10 measures of a kind refuse the run, saying which. The summary says it (`made_with.execution`: the period, the orders), and so does the run page.
- **No MCP call lasts a minute.** Claude Code ends an HTTP call after 60 s when the server's entry sets no `timeout`, and the Mac's sets none.
  - The six tools that run a Pine study (`pine_check`, `pine_run`, `pine_signals`, `pine_compare`, `pine_save`, `rule_shadow_save`) answer within 45 s, or hand back a job while the study goes on: `{"job": ..., "state": "running", "next": "job_wait with this job, until it gives the tool's answer"}`. A new tool, `job_wait`, waits up to 45 s and gives that tool's own answer, or its refusal, once there is one (38 tools). A job is its token's, kept 15 minutes after it ends, 5 at most, and lost when the admin restarts. A study refused behind one still at work names it.
  - Every other tool answers within 50 s (100 before), and `backtest_wait` waits 45 s at most.
- **Answers that fit in Claude's context.** `pine_run` lists only the alerts of `alert_titles` when given, 100 at most by default (`max_alerts`, 0 for the counts alone, 500 at most), and fits what is still too long: its plots' points are thinned, then its alerts listed cut, as its `fitted` says (an every-bar script with two plots on two days of ten-second bars came to 34,649 characters with the defaults, now 31,943 with 258 points a plot); `pine_compare` lists 50 pairs by default (`rows`, 0 for the days and totals alone, 300 at most); `backtest_trades` leaves out the turbos each trade was chosen among unless asked (`candidates`).
- **A week of bars a call**: `data_bars` as CSV covers up to 7 days, 70,000 rows (a week of ten-second bars is about 60,000 rows and 6 MB, which Claude Code saves to a file). JSON keeps its 12,000 bars, Dukascopy its month per view.
- **Why a run or a study died is said.**
  - Before each request to the Pine runner, a backtest notes in its progress the engine day, its bars and the runner's peak memory.
  - A run that ends badly gets a `failure`, said first in its error (the run page, MCP `backtest_list` and `backtest_result`): what its exit code means (−9: stopped by the kernel, most likely for memory, the admin's children going first; −6: Node's heap was full; −15: stopped), the phase and the day it was on with that day's bars, the container's memory then, and whether the kernel's OOM killer acted during the run (`memory.events` read at its start and end).
  - The Pine runner keeps the last lines it wrote to stderr and says them when it ends, V8's "JavaScript heap out of memory" for one; a replay adds the engine day and its bars. A study that dies without an answer says the same.

- **The run page's list of changed settings wraps a long label** instead of pushing the page sideways ("Order times and prices: fixed, or drawn from WATA's own orders" did, by 38 pixels at 1280).
- **The fake admin has WATA's orders for the execution measure** (`execution_rows`, made up from its tracked orders): the form's measured values and a run on measured fills ended in an error 500 there.

**On the live path**: the trader's new check, inert until the rule is added; `ConfigurationManager`'s check of it; the scheduler's reminder, sent only while the rule is on. Every backtest made before shows as stale (the code digest); its results are unchanged with the new settings off.

**Verification status:**
- 2,264 tests pass in Python's suite, 1 skipped, on Python 3.12 with Node 24 on the PATH (the real-runner and checker tests ran). Not run on Python 3.14 here: CI runs both. 837 pass in the interface's suite; lint, type checks, the build, `build:runner` and `build:checker` are clean.
- **Fixed fills give the same files as v0.24.0's code.** A run of the EMA cross rule on the fake admin's five recorded days (5 trades, 100 random repetitions) and a sweep of it (four cells of 5 to 12 trades, stop-losses included), each replayed by both versions on the same files: trades, signals, days, random entries, edge and sweep identical; the summaries differ only in `made_with` (the code's digest, the runner's time, `execution: fixed`).
- The window: unit tests (both edges, FOMC's 45 minutes, 11 March when only New York had changed its clocks, past the calendar's end, a calendar that cannot be read) and the parity test hold the trader and the backtests to the same refusals; a backtest of 14 October (CPI) refuses an entry inside it with the trader's words.
- In the browser pane, on the fake admin (`npm run dev:api`): the "US releases" switch added the rule with its defaults (nine fields in the review, saved with a backup) and removed it. With the rule in the configuration and a one-row scratch calendar, the Next entry tile said "Blocked — CPI at 13:15: no entry until 13:30" inside the window and "Open … No entry around CPI (14:30) from 14:25 to 14:45" before it, and the Services card "US releases calendar" with its sentence. The Backtests form showed the rule's settings as live and "Order times and prices"; a run on measured fills drew from the fake's 101 orders, and its page says so.
- Through the MCP SDK's client, on the fake admin (`FAKE_MCP=1`): a Pine study of 57 s handed back as a job at 45.0 s and answered by `job_wait` 13 s later (the same answer when asked again, an unknown job refused with its sentence, both ends in the call log); `pine_run` of an every-bar script, 31,943 characters fitted, 21,560 with `alert_titles` and `max_alerts` 5; `data_bars` of seven days of Capital.com's ten-second bars, one CSV of 36,957 rows (2.8 MB) in 0.8 s, eight days refused.

**Not seen:** the server, its orders, Telegram. After a deploy:
- add the rule from the Configuration page when wanted, then restart the trader (and the admin, for the tile and the health): they read the configuration at their start;
- the journal's refusals inside the next windows, in Paris: FOMC on 28 October from 18:55 to 19:45, PCE on 29 October from 13:25 to 13:45, NFP on 6 November and CPI on 10 November from 14:25 to 14:45;
- the health's "US releases calendar" (known until 2026-12-23), then its warning and the scheduler's Telegram line from 23 November: the PCE releases after 23 December must be added from bea.gov (`python -m lab calendar check`) before then;
- a long `pine_signals` through Claude Code, answered as a job; a run on measured fills from the server's own orders (its `made_with.execution`).

## v0.24.0 - Range bars that say their size, faster time zones, and one trade a bar

**Release date:** 2026-10-21, with v0.26.0

The gaps marked "fix" after the first research session through the MCP endpoint (2026-10-21, the Kernel + Avg multi VWAP indicator).

- **A range is counted in the asset's own tick, and says so.** Dukascopy's files hold thousandths: R60 is 0.06 points there and 6 points are R6000, where Capital.com's US100 (a tick of 0.1) makes R60 6 points. Nothing said it, and the MCP's answers rounded 29143.543 to 29143.5, which hid it.
  - An engine day of more than 40,000 bars is refused before the Pine runner is given it, with what the range is on that asset: "This engine day has more than 40,000 bars of R600 on dukascopy/us100: a tick of this asset is 0.001, so R600 is 0.6 points; one point is R1000 here" (`too_large`, `built_bars` in `src/ta_engine/replay.py`, which builds the bars a few rows at a time so as to stop early). That was the "Dukascopy runs die" of the session: R600 made about 100,000 bars a day, and the runner holds 590 MB at 49,107. Studies answer the sentence, and backtests fail on their first day with it, instead of a runner killed after minutes.
  - Each asset says its `tick` (`src/admin/data.py`: Capital.com from its decimals, Saxo from its last quotes, Dukascopy from its last day), in MCP `data_assets` and under the range of the Backtests form ("R600 is 0.6 points on this asset, whose tick is 0.001 (a point is R1000).").
  - The MCP keeps six significant digits but three decimals at least (`compacted`).
  - A run on Capital.com's day files (`--data files:`) read the tick from the prices with Saxo's floor of two decimals, so R60 was 0.6 points of US100 there and 6 on the server: it now takes the decimals the feed's prices carry (`AssetRows._capital_decimals`), as the feed's table says them.
- **Scripts that read the time in a time zone run about 2.4 times faster.** PineTS 0.11.1 made a new `Intl.DateTimeFormat` at each `hour(time, "Europe/Paris")`, `minute()`... and `timestamp("Europe/Paris", ...)`: 58 % of a run of the Kernel script. `admin_ui/vite/pinetsZoneFormat.ts` keeps one per time zone, in the three copies of PineTS the admin ships (the TA engine's runner, the pages' Pine worker, Vela-pinets' worker), each change one exact piece of PineTS found once or the build stops. The runner says it: `"pinets": "0.11.1+zone-format"`, which a run's `made_with` keeps. Nothing a script computes changes.
- **A signal rule may have its own days of history** (0 to 30; empty, the engine's `history_days`): fewer for a script that needs little warm-up, which then costs less at each bar. The Kernel script gives the same signals with none, on 4,623 bars instead of 8,381 on 20 October. The engine keeps an epic's ten-second bars from the earliest any of its rules needs and starts each rule at its own; a change of it loads the rule again. The Signal rules page has the field; a run made from a rule takes it (the form's "Start from a signal rule", MCP `backtest_start` with `source.from_rule`), and so do `python -m src.ta_engine.replay --rule` and Claude's `pine_signals` and `rule_shadow_save` (`history_days`, -1 for the engine's again).
- **The time a day may cost one of Claude's shadow rules is a setting**: `admin.mcp.shadow_day_ms` (2,000 by default, 500 to 10,000), where it was 2 s in the code. Each rule is first run on its own history.
- **One bar, one trade: an entry wins over a close of the same bar.** The trader takes one signal after the other, and an entry sells the other side before it buys: a close after it sold the position just opened. With every exit of the Kernel indicator mapped, 31 of its 33 closes of 19 and 20 October fell on the bar of an entry, and E2-1 of the session had 38 instant losses. The TA engine sends the entry alone and journals the close as not sent, with why; backtests and studies list it as no signal (`settled` in `src/ta_engine/signals.py`, used by `Engine._signals` and `ReplaySession.window`). The web server does the same for TradingView's alerts: a `close-position` does not replace an entry of the same `signal_timestamp` (`SignalStore.upsert`), while an entry still replaces a close. The scheduler's close of the day is kept apart (indice `n/a`). An entry the trader refuses (one already open in its direction) leaves the position as it was.

**On the live path**, only the web server's signal store changes; the trader's and the position monitor's code is untouched. Every backtest made before shows as stale: the code of `src/ta_engine` is in their digest. Their results change only where a bar raised an entry and a close, or on Capital.com's day files.

**Verification status:**
- 2,212 tests pass in Python's suite, 2 skipped, on Python 3.12 with Node 24 on the PATH (the real-runner and checker tests ran). Not run on Python 3.14 here: this Mac has 3.12 only; CI runs both. 834 pass in the interface's suite (827 before); lint, type checks, the build, `build:runner` and `build:checker` are clean, and the dev server's prepared PineTS carries the change too.
- PineTS patched beside PineTS as it is (`vite/pinetsZoneFormat.test.ts`): every hour, minute, weekday and timestamp the same over half-hour bars around the four clock changes of 2026, 4 formatters made instead of 576 on 96 bars. Through the built runner, confined as the engine runs it: the day of 49,107 bars captured from the session in 8.1 s instead of 19.8 s, its answer identical; a real-runner test reads Paris's hours across 25 October.
- On a real Dukascopy day (17 September) with the Kernel v2 script: R600 refused in 0.8 s with the sentence above (it ran 43 s and reached 854 MB before); R6000 runs on 2,016 and 2,030 bars, its signals the same as before, 0.43 to 0.50 s of script a day instead of 0.88 to 0.92.
- On the Capital.com days of 19 and 20 October (the MCP's CSVs, as day files) with v1 and every exit mapped: 33 entries as with the exits ignored, 2 closes sent, none on the bar of an entry.
- In the browser pane, on the fake admin (`npm run dev:api`): the range hint for Capital.com's US100 and Dukascopy's history; the Signal rules dialog's history field (31 refused, 0 reviewed as "None: from the restart", the rule kept with 0) and its "One bar, one trade" line.

**Not seen:** the server. After a deploy: `pine_signals` of the Kernel script on Capital.com R60 for a day (its time per engine day); `rule_shadow_save` with `history_days` 0, refused at 2 s with its new time or accepted under a raised `shadow_day_ms`; `data_bars` of a Dukascopy day, with its three decimals. Backtests made before, to be run again where they matter.

## v0.23.0 - Claude does strategy research on the server

**Release date:** 2026-10-20, with v0.22.0

- **An MCP endpoint for Claude Code, in the admin** (`src/admin/mcp_server/`): `/mcp` on the admin's address, so over WireGuard only, off unless `admin.mcp_enabled` is true. It opens with a bearer token made on the server (`watamcptoken create <name>`, printed once, 90 days; the file keeps its sha256 only). It refuses any request that carries an `Origin`, has its own throttle, answers at most four calls of a token at once, and is paused from the System page without a restart. The official SDK (`mcp` 2.3.0) is loaded only when the endpoint is on, and speaks both the 2025-11-25 protocol and the stateless 2026-07-28 one, in JSON.
- **37 tools for the research loop**, with a `/wata:research` prompt and server instructions that carry the lab skill's rules (what not to believe, the lock box, the few recorded days):
  - the data: recorded days, bars of 10 s to a day (or CSV, which Claude Code saves to a file on the Mac), the signals WATA received, its closed trades without account keys, measured execution;
  - Pine: list, read, **check** (PineTS's compile with file and line, and TradingView's v6 rules through a confined checker, `src/ta_engine/checker/check.mjs`), **run** on recorded bars (every plot summarised and sampled, alerts, PineTS's runtime warnings), **signals** as a rule would raise them, **compare** with TradingView's, save (checked first, Claude's own scripts only) and delete (its own, unused);
  - backtests: options, start (the Backtests form's body, with an idempotency key), derive (stress, a sweep's cell, neighbours), wait, list, result by part, trades, days, signals, compare, remove (its own), the turbo model;
  - the experiment journal and the lock box, kept on the server: trials counted per family and given to sweeps, the lock box refused to every study and run but a candidate's final check, widened only;
  - rules: every rule with its paper days, a rule's signals beside TradingView's, and **Claude's own shadow rules** (make, change, switch off, remove, set a run made from one as its reference), behind `admin.mcp.shadow_rules`, off by default. A rule is first run as the engine would on the last finished day of its epic, and refused if it fails, takes over 2 s an engine day or has over 20,000 bars a day.
- **What Claude can never do, enforced and tested**: trade, pause entries, publish to RabbitMQ or reach the web server; edit the configuration or read a credential; take a rule live or change one that is live or the operator's; run free SQL. The tools reach the admin through `ResearchServices` alone (named queries, no trading service); a test calls every tool with tripwires in place of everything that trades, another holds the endpoint's imports to an allowlist, and the only change of mode in its code is between shadow and off.
- **Claude's share of a small server**: its runs wait behind the operator's and the paper runs (at most 3 waiting, 60 a day, 10,000 day-simulations a request, 5 GB of disk kept free); Pine studies are child processes, one at a time, nice 10, 384 MB of Node heap, 90 s, 20 minutes an hour, refused when the container's memory is short; every child process of the admin is the first the kernel stops when memory runs out (`oom_score_adj` 1000). The runner's answers are shared by studies and backtests (`admin.backtests_signal_cache_mb`, oldest pruned). The admin container gets 1536M of memory instead of 1024M (`deploy/docker-compose.yml`), so that a study fits beside a backtest more often.
- **The TA engine runs live and shadow rules on two runners.** Live rules go first in each tick; shadow rules get `shadow_seconds_per_tick` (5 s) of it, the one given its bars longest ago first, and keep their bars when they wait. A shadow rule that fails, hangs or ends its runner reloads the shadow rules alone, never a live one; the shadow runner (256 MB, nice 10) is the first process stopped when memory runs short, and is stopped while no shadow rule is on. A Claude-made shadow rule's trouble is said on Telegram once a day at most.
- **Watching it**: System › Claude (the endpoint's state, its tokens, revoked from there, Claude's share against its limits, its last calls and refusals, the pause); Backtests › Experiments (the journal, and the lock box, which the operator moves or clears); a "Claude" badge on its runs, scripts, rules and experiments. Every call is logged in `wata-admin/mcp.jsonl`; only a revocation and the pause are audited (each audit entry goes to Telegram).
- **Shared code**: the backtest form's request (`backtest_requests.py`) and the bars of an asset (`asset_bars.py`) serve the routes and the tools alike; the lab's journal and studies moved to `src/research/` (the lab's commands are unchanged). The Pine runner can answer every bar's plots and runtime warnings (`detail`), its default answer unchanged. The study child reads recorded day files with `--data files:<folder>`, as backtests do.
- **The lab skill** says the server path, and no longer lists `close-long`/`close-short`, which are not actions.

**Verification status:**
- 2,184 tests pass in Python's suite, 16 skipped (live infrastructure and the real-database tests), on Python 3.14 and 3.12, with Node 24 on the PATH (the tests that run the real Pine runner and the rules checker ran); 827 in the interface's suite (815 before); lint, type checks, the build, `build:runner` and `build:checker` are clean.
- The TA engine: 72 tests (62 before; the 62 unchanged), with stand-in runners that end, time out or fail a load: a shadow runner's end never reloads a live rule, whose signal is still sent; live rules go first; shadow rules take turns and lose no bar; a rule moved to live is loaded on the live runner; the shadow runner stops with the last shadow rule.
- End to end on the fake admin with the endpoint on (`FAKE_MCP=1 npm run dev:api`, which prints a development token), through the SDK's client, in both protocol revisions (31 calls each):
  - `status`, the coverage, `pine_save` of the lab's EMA cross, `pine_check` (and a broken draft's line and column), `pine_run`, `pine_signals`, `pine_compare`;
  - an experiment, a backtest sent twice with one idempotency key (one run), its result and edge, its trades as CSV, its days, a stress run, a day of bars as CSV (107 kB);
  - a shadow rule, run first as the engine would (2,431 bars, 17 ms a day), its switch to live refused, a run made from it set as its reference;
  - the conclusion (2 trials counted), the removals.
- A headless Claude Code session (`claude -p`, the endpoint given in a scratch `--mcp-config` with a headers helper) made five calls through it: `status`, `pine_list`, `exp_new`, `pine_check`, `exp_conclude`. Each was logged in `mcp.jsonl`; nothing was audited or sent to Telegram.
- In the browser pane, on the same fake admin: System › Claude in dark at 1440 and in light at 390; the Claude badge on the runs and on Signal rules (on the scripts' list in the tests only); Experiments, where a lock box set from the page was then refused to Claude's `pine_run` and to a narrower `lockbox_set`, and cleared; no horizontal scroll at 390, no console error.

**Not seen:** the server's data and the path over WireGuard; Claude Code against the real server (the first check is the operator's, after a deploy: `/mcp` shows `wata` connected, and `status` the server's days). The TA engine's two runners on the server's feed.

## v0.22.0 - Bars of seconds on the Charts page and in backtests

**Release date:** 2026-10-20, with v0.23.0

- **Bars of 10, 20, 30 and 45 seconds, and of any length typed.** WATA records ten-second bars, and now makes bars of any whole number of seconds from 10 to a day of them, named as Pine names a timeframe (`10S`, `45S`, `90S`; `1`, `60` for minutes):
  - Technical Analysis › Charts: Vela's timeframe menu offers 10, 20, 30 and 45 seconds, 1, 5 and 15 minutes and an hour; the **Length** field above the chart takes any other (`90s`, `2m`, `4h`; a plain number is minutes) and says why it refuses one (under ten seconds, over a day). The address carries the length (`tf=45S`), so a chart of seconds can be shared and reloaded. Capital.com moves live at these lengths, Saxo every ten seconds;
  - the editor's test offers the same lengths;
  - a backtest takes the menu's lengths, or "Other length…" and one typed; its review, its run and its days say the length, and a day's chart is drawn at it.
- **Vela's chart learns seconds.** Vela 0.8.4 lists seconds in its menus, but its chart read `10S` as an hour: the history it loads, the gaps it heals and the bar a quote moves are counted from that. Its time axis, crosshair and data window also stopped at the minute. `admin_ui/vite/velaSeconds.ts` patches the pinned package when the interface is built (and in the dev server). Each change replaces one exact piece of Vela's code, and the build stops if one is no longer there, so a new version of Vela is looked at before it ships (`vite/velaSeconds.test.ts` runs Vela's own functions, patched).
- **A length that is no multiple of ten seconds.** A bar of 45 seconds holds the ten-second bars that begin in it, 40 or 50 seconds of prices by turns, and closes when the last of them does: in a backtest the alert of the bar of 14:00:00 counts at 14:00:50, never before its prices are known. The Charts page and the backtest form say so when such a length is chosen. Multiples of ten seconds are exact.
- **What serves them:**
  - the admin's API serves bars of any whole number of seconds from 10 to a day (it took 10, 30, 60, 300, 900 and 3600 only: `interval_served` in `src/admin/prices.py`), from the recorder's and the feed's tables and from Dukascopy's days alike;
  - backtests and the TA replay (`--bars 45S`) take any length (`checked_bars(..., any_length=True)` in `src/admin/ta_rules.py`; `bucket_end` in `src/ta_engine/bars.py`);
  - a script's `request.security` of seconds is read as WATA makes it, on WATA's grid, rather than built by PineTS from shorter bars (`engine/provider.ts`).
- **Signal rules keep whole minutes** (1, 5, 15 or 60, or range bars). The TA engine runs a rule's script over all its bars at each new one, which bars of seconds would make several times a minute on the server. Nothing changes for the engine in production.

**Verification status:**
- 812 tests pass in the interface's suite (776 before), 1,921 in Python's with 16 skipped (live infrastructure and the two real-database tests); lint, type checks and both builds are clean. The tests that pinned the old lengths (45 seconds refused) were changed to the new ones.
- Through the real Pine runner, a script on bars of 45 seconds: `timeframe.in_seconds()` 45, `timeframe.isseconds` true, `time_close` 45 or 50 seconds after `time`, and `request.security(..., "90S", close)` asked of WATA as 90-second bars.
- In a browser, on the admin application over made-up data (`npm run dev:api`):
  - Capital.com's US100 opened at `tf=45S`: 45-second bars, the note, the crosshair at 14:07:30, an axis of 14:42:30, 14:43, 14:43:45; live, a new bar began at 14:44:15, 45 seconds after the one before;
  - `90s` typed in the Length field: the chart and the address moved to 90 seconds; `5s` refused with its reason, the chart left as it was;
  - 10 seconds, with the EMA cross script on it: its lines, its crosses and an alert of the 10-second chart;
  - a Saxo turbo at 20 seconds;
  - a backtest on 45-second bars typed in the form, run to its end: its request kept `45S`, each of its 338 signals of the script came from a bar closed 45 or 50 seconds after it opened, on a whole ten seconds, and reached the trader 3 seconds later; the chart of a day drawn at 45 seconds, its crosshair at 13:06:45;
  - the Charts page in dark at a phone's width.

**Not seen:** Capital.com's real stream and the server's recorded prices at these lengths; a Dukascopy chart (no history in the checkout tried). A length chosen in Vela's own menu: the browser used passes no click to Vela's menus (seen before, in v0.21.2's checks), so the menu was seen listing the lengths and the Length field was used instead.

## v0.21.2 - The Charts page moves live

**Release date:** 2026-10-20

- **The Vela chart of Technical Analysis › Charts stood still.** It drew the bars read when it opened, then nothing more, whatever the asset: Capital.com's US100 was frozen there while its chart on the Assets View moved. Vela draws a chart as history alone unless it is told the chart is live (`live`, off by default), and only a live chart asks its data provider for what moves its last bar. The workspace was built without it, and the tests' stand-in for Vela never looked. It is now built live (`TaChart.tsx`, tested):
  - a Capital.com chart moves at each quote of the feed's stream, as the Assets View does, and its range bars at each ten seconds closed;
  - a Saxo chart, of an index or a turbo, moves every ten seconds with the recorder's bars;
  - Dukascopy stays history.
- **The scripts on a chart are run in full again when its bars change.** On a live chart Vela would run them in PineTS's live mode, which in PineTS 0.11.1 raises none of the alerts of the bars it adds and leaves `request.security` behind; the TA engine does not use it either. The chart draws what the editor's test and the engine compute from the same bars (`chart/pineEngine.ts`, tested):
  - while quotes come, a script runs again a second apart at most, the last quote included;
  - an alert of the bar in progress, raised again by each run, is shown once;
  - an error that every run repeats is shown once, and again after a run that works or new inputs.
- **A Saxo chart opened before the day's first bar, or left open overnight, now reads today's bars as they are recorded.** It asked for bars up to the asset's last day as it was when the chart opened, and stayed on the day before. An asset the recorder still records is now read up to today, as Capital.com's already was (`data/wataBars.ts`, tested).
- **The range bars a live chart missed are all read back.** When a chart comes back (its tab shown again, its stream connected again), Vela asks for the bars since its last one, as many as its timeframe would hold. Range bars can come faster: only the newest that many were served, which left a hole. Since a time, a range ticker's bars are now all served (`chart/wataProvider.ts`, tested).

**Verification status:**
- 776 tests pass in the interface's suite; lint, type checks and build are clean. The new tests fail without the fix.
- In a browser, on the admin application over made-up data (`npm run dev:api`):
  - Capital.com's US100 at one minute, before the fix: no stream opened, the last price unchanged for half a minute;
  - after it: the last bar moves at each quote, and a new one begins on the minute;
  - with the EMA cross script on it: one whole run a second (501 bars each), its lines moving with the close;
  - the chart moved to five minutes in place: the stream of the one-minute chart closed, another opened;
  - its range bars (R60): the last one moves at each ten seconds closed, one read of range bars each time;
  - a Saxo turbo and the Saxo index, their charts opened at 07:31 and 07:46 before the made-up recorder's 08:00 open: each read today's bars every ten seconds, drew the 08:00 bar once recorded, then the 08:01 one, as the API served them.

**Not seen:** Capital.com's real stream, the server's recorded Saxo prices, a Dukascopy chart (no history in the checkout tried).

## v0.21.1 - The Capital.com feed's log no longer floods

**Release date:** 2026-10-20

- **The feed's live prices are no longer sent back by RabbitMQ.** While no chart follows them, no queue is bound to `wata.prices`, and aio-pika asks by default for such a message to come back. Nothing read them: the feed logged `aiormq.channel - Unhandled message ... Basic.Return` as an error several times a second, already so before v0.21.0, and its log rotated every 20 minutes, keeping about ten hours. A price no chart listens to is now dropped by RabbitMQ, as intended (`mandatory=False` in `RabbitExchange.publish`, `src/capital_feed/live.py`, tested).

## v0.21.0 - A research lab, and backtests that say how far to trust them

**Release date:** 2026-10-20

Finding a strategy means trying many, and the best of many tries looks good by chance. This release gives the backtests what tells a found edge from luck, a place on a Mac where Claude can try strategies by the hundred over a copy of the server's data, and long histories on turbos made up from the index.

- **A research lab** (`lab/`, on a Mac, never on the server):
  - **The server's days**: the admin can write each finished day as files while the market is closed (`src/admin/research_export.py`: the recorder's and the Capital.com feed's bars, the signals, positions, orders, a snapshot of the settings; never `config.json` itself, an order's bodies or a token). "Write them" on the Backtests page switches it on; the last 30 days are kept there.
  - **Each night**, `lab/sync.sh` pulls them over SSH (yours to run, or `lab/launchd/install.sh` to have the Mac run it) into a PostgreSQL of its own.
  - **The real admin app** over that copy, on 127.0.0.1:8083 (`python -m lab serve`), running four backtests at once, two years at most.
  - **A command line** for Claude, `python -m lab`: runs and sweeps from a spec, an experiment journal that counts every trial, a lock box of last days kept for a candidate's final check, reports as pages, SQL over the files, and every reading below. `.claude/skills/wata-lab/SKILL.md` is the method it follows.
  - **A script's signals are kept** per day (`_signal_cache`): running the same script again on the same days costs nothing.
- **How far to trust a run**:
  - **Risk figures** on its days: Sharpe, Sortino and Calmar ratios (from 20 days), the longest time under a former high, the worst 5 % of days, a win for a loss, and the share of its best a winning trade kept.
  - **The same days otherwise**: the worst drawdown when they are drawn again in another order, and the total without the best one, two, three days.
  - **Later and dearer fills**: "Stress this run" makes it again with each order up to 15 s later and up to 0.05 € a turbo dearer.
  - **The edge of the entries**: how the index moved 1 to 60 minutes after each entry signal, taken or not, against moments drawn at random in the same hours, and where it lives (hour, direction, signals taken or not, kind of day).
  - **Kinds of day**: each day's gap, range, volatility, trend, US open and macro news (FOMC, CPI, jobs report, PCE, from the Fed's, BLS's and BEA's own calendars); the trades broken down by them and by their place against the US open.
- **Sweeps say whether choosing worked**: a walk-forward (the best combination chosen again window after window on the days before, kept on the days after, beside the best one known only afterwards), the probability that choosing the best was fitting, and the deflated Sharpe ratio with every combination tried counted.
- **Long histories on made-up turbos**: the recorded turbos are measured against Dukascopy's Nasdaq (euros a point, the level beside the knock-out one, the spacing of the levels, the spread by hour), and a run can trade turbos made up from the index on any day it has, up to two years. "Measure them" on the Backtests page; every page says when a run's turbos were made up. `python -m lab model validate` checks the model on the recorded days.
- **Measured, not assumed**: what WATA's own orders took and cost (from a signal to the trader, from an order to Saxo, the price paid against the quote recorded, the refusals), with "Use the measured values" in the form.
- **Against real trades**: a replay of the signals WATA received is set beside WATA's real positions of the same signals, trade by trade.
- **Shadow rules on paper**: each finished day, a rule in shadow is traded on the signals it raised, and the Signal rules page sets those days beside the daily range of the run chosen as the rule's reference ("Set as the rule's reference" on a run made from it).

## v0.20.0 - Technical Analysis: Pine scripts, Vela charts, WATA's own signals, and backtests on the recorded turbos

**Release date:** 2026-10-20, deployed together with v0.21.0

WATA's signals came from TradingView alone: an indicator written in Pine Script on TradingView's charts, its alerts posted to the webhook. The admin gains a **Technical Analysis** section that does the same on WATA's own data, and lets WATA make its own signals from it, in shadow until a rule is switched to live.

- **Indicators** (`/ta/indicators`): Pine Script v5 and v6, written in the browser with the language highlighted and checked as it is typed (TradingView's own rules, then a compile by PineTS that marks the line of each error, then WATA's own checks: an unknown library, a ticker WATA does not have). Each save is a new version, kept as a plain `.pine` file under `var/lib/admin/ta/`, with its history, a diff, and a version put back. Libraries are kept like scripts, and `import user/Name/1 as alias` imports the WATA library of that name, inlined before the script runs. A `.pine` file can be imported, and a script downloaded.
- **A test beside the editor**: the script run on a day of one of WATA's assets (Capital.com, Dukascopy, Saxo), at 1 to 60 minutes or as range bars, with its plots and every alert of the day. Its bars, and the script as it ran (libraries inlined), can be downloaded for pinets-cli or an AI agent.
- **Charts** (`/ta/charts`): the Vela SuperChart on WATA's assets, Capital.com live, with WATA's scripts in Vela's own indicator list and their inputs in its settings. Range bars are a ticker of their own (`CAPITAL_US100.R60`). The layout is kept by the admin, shared between devices. An asset's page and the editor open their asset or script there.
- **Signal rules** (`/ta/signals`): a script run on each bar of a Capital.com asset as the bar closes. A rule pins the script's version and each library's, sets the inputs (read from the script by PineTS) and maps each alert to nothing, `long`, `short`, `close-position`, or what its message says (WATA's webhook JSON). A new rule is in shadow: its signals are journaled, never sent. Live is an operator action: allowed only when actions are, confirmed with a slide that says TradingView's alerts for the indice must be off, one live rule per indice, audited and announced on Telegram, as is any change to a live rule. The page shows the engine's state and each rule's, the journal of signals with what the trader made of each one sent, and a day of a rule's signals beside TradingView's, paired, listed and marked on the day's prices.
- **The TA engine** (`src/ta_engine/`, compose profile `ta`, `trade.config.ta_engine`, off by default): reads the Capital.com feed's bars from PostgreSQL, builds the rules' bars as the charts do, runs each new bar through PineTS, journals each signal in `ta_signal` before anything is sent, and sends a live rule's signals to the web server's `/internal/signal`. It never sends:
  - for a shadow rule;
  - for the history loaded at a start;
  - for a bar evaluated again;
  - for a bar that closed more than a minute ago;
  - while the feed is stale;
  - an action other than `long`, `short` or `close-position`.

  The scripts run in a Node process of their own, confined: no file but its own, no environment, no import once started, no network functions, its memory capped, and a time limit per bar. "TA engine" is on the System page once switched on, its log on the Logs page.
- **A replay command**, `python -m src.ta_engine.replay`: what a rule or a script would have raised on recorded days (Capital.com's from the database, Dukascopy's from the day files), through the same runner and the engine's daily restart, as JSON. Nothing is sent or written. `python -m src.ta_engine.runner` checks the runner where it is installed.
- **Licences**: PineTS and Vela-pinets are under the AGPL-3.0; the admin and the engine built with them are offered with their whole source, the public repository ("Licences and source" on the System page, and `third-party-notices.txt` served with the admin). WATA's own files stay under the MIT licence.

**Backtests, rebuilt from scratch.** The former backtester traded one strategy, written in Python, on a model of the turbo. The new one trades either a Pine script of the Technical Analysis section or the signals WATA received. It trades them on the turbos the price recorder kept, as WATA would have.

- **What is replayed**:
  - the trader's checks on an entry, in its order;
  - its choice of turbo, made by its own helpers among the turbos recorded and quoted at that moment;
  - its sizing;
  - the position monitor's exits, on the turbo's recorded bid every ten seconds;
  - the close of the day.

  An entry with no turbo recorded at that moment is shown, not traded. `tests/test_backtest_parity.py` holds the copies of those rules to the live code.
- **New backtest**, in five steps:
  1. the asset and its bars: Capital.com, Dukascopy or the index Saxo quotes, as time bars or range bars, or as a signal rule has them;
  2. the strategy: a script with its inputs and what its alerts become, or the journal;
  3. the period, with what was recorded each day;
  4. WATA's settings for that run only, each beside its live value, and what a backtest assumes: the account, the delays, how a stop fills, slippage and commissions;
  5. a review.
- **A run** shows:
  - its figures, and how far to trust them: random entries traded on the same turbos, the range of its total, room for a stop and a target;
  - its curve, in percent or in money;
  - why signals were refused and why trades were sold;
  - breakdowns, as the Exits page makes them;
  - every day, trade and signal, as tables with CSV export.
- **A day** shows the asset as the strategy read it, with every signal and trade marked.
- **A trade, clicked**, opens:
  - the chart of the turbo it bought: its bid every ten seconds, with the price paid, the stop, the day's target and the trailing stop as it moved;
  - the turbos the trader looked at;
  - its size, cost and result;
  - the signals that bought it and sold it.
- **Sweeps and Compare are rebuilt on it.** A sweep tries one or two settings or script inputs, holds the last days out, and can make a combination a run of its own. Compare sets up to four runs side by side: their figures, what they did not share, and their curves.
- **On the server**, a run is a child process of the admin:
  - one at a time, at the lowest priority;
  - it reads the recorded turbos through a read-only connection, a day at a time;
  - it writes files under `var/lib/backtests/runs/`;
  - 92 days, 10 runs waiting and 3 h per run at most.
- **Dukascopy's history moved to the Data section.** Its downloads, "Keep the prices up to date" and its coverage are on Dukascopy's provider page (`/api/data/providers/dukascopy/...`). The code is in `src/price_history/` and `src/admin/downloads.py`.
- **Removed**:
  - `src/strategy/`: the Python strategy, its turbo model and its calibration, the comparison with real trades;
  - its commands and its pages.

  The former runs can be deleted from the Backtests page ("Remove them").

**Verification status:**
- **Tests:**
  - 1,778 pass in the Python suite, in an environment built from `requirements.txt`; the 14 skipped are the live database and queue tests, which need their servers. One test runs the built runner in its confinement on Node 24.
  - Of those, 57 run the trader's and the position monitor's own code beside the backtest's copies of their rules (`tests/test_backtest_parity.py`), and one runs a backtest through a real child process.
  - 730 in the interface's suite; lint, type checks and build are clean.
- **Real PostgreSQL 16:**
  - the engine's tables, its writes and the admin's reads, joined to the trader's journal, before and after the engine first ran;
  - the backtest's reads of the recorded turbos (`DatabaseData`, on a read-only connection) and the coverage queries of the form.
- **In a browser** (the admin application over made-up data; Chromium dark at 1,440 pixels, WebKit light at a phone's width):
  - Indicators, editor, test, history, Charts, range bars, Vela's indicator list;
  - a rule made, switched off and back, the live confirmation cancelled, and the comparison.

  No policy violation in the console: PineTS compiles in the worker files only, in WebKit too.
- **Backtests, in a browser** (the same server, its made-up recorded turbos written as the files the command reads, the real Pine runner):
  - three runs asked for through the form: the EMA cross with the stop changed, a replay of the journal, and a sweep of the stop and the day's target;
  - their pages, a day, a trade opened on its turbo, the comparison, in Chromium dark and light at 1,440 pixels and WebKit light at a phone's width, with a clean console.

  These checks found three faults, now fixed and tested:
  - a replay of the journal failed: its timestamps, as the database gives them, could not be written;
  - the folder of that failed run broke the list of runs;
  - a page opened from a scrolled one kept its scroll, so a trade opened from a run showed the bottom of its day.
- **The user's Kernel multi-VWAP script** (with its two libraries, kept out of the repository) compiled and ran through the replay on the three Dukascopy days kept locally:
  - **2026-09-17:** neither the replay nor the Python port of the strategy (`src/strategy/kernel_vwap.py`, removed since with the former backtester) raised anything.
  - **2026-09-15 and 2026-09-22:** the afternoon entries and the exits at the end of the session agree within one to two minutes; the morning entries differ. The port builds its range bars from mid prices rounded to 0.1, weights its VWAP by each ten seconds and simplifies the script; the replay runs the script itself on bid bars.
- **pinets-cli 0.1.15** ran a downloaded script, its library inlined, on its downloaded bars.

**Not run against the server's data, Capital.com's real stream, a real signal reaching the trader, or Docker:** the image's Node and the runner inside it are checked by CI's image job. No backtest has run on the server's recorded turbos yet.

### Upgrade Notes

- Nothing changes until the `ta` profile is added and `trade.config.ta_engine.enabled` set: see the README, "Technical Analysis". The engine needs the Capital.com feed for its bars.
- `package.sh` now also builds the engine's runner (`npm run build:runner`), and the image carries Node 24's `node` binary.
- The admin keeps the scripts and rules under `var/lib/admin/ta/`, inside its existing writable folder; the engine mounts that folder read-only.
- **The admin container gets 1024M of memory instead of 512M** (`deploy/docker-compose.yml`): a backtest's child and its Pine runner run inside it.
- **The former backtester's runs stay on disk** until "Remove them" on the Backtests page. Dukascopy's downloaded prices are kept. The state of "Keep the prices up to date" carries over: it is copied once to `var/lib/price_history/dukascopy/_fetch/`.
- **`python -m src.strategy.*` no longer exists.** Dukascopy's history is fetched with `python -m src.price_history.dukascopy`. Backtests are asked for from the admin; `python -m src.backtest.run` is the command it runs.

### Known Limits

- **PineTS is not TradingView.** It runs most of Pine v5 and v6, not all of it, and not always identically:
  - a symbol has no exchange time zone (its days are UTC days);
  - `request.security` reads WATA's assets only, live only Capital.com's;
  - PineTS 0.11.1 passes the named arguments of `alertcondition()` in the wrong place, which WATA puts right.
- **Each new bar runs the script over all of a rule's bars**: PineTS's own live update raised none of the user's script's alerts. The user's script took 0.5 s per thousand one-minute bars here, and 1.2 s while reading two other series; `history_days` bounds it.
- A rule runs on Capital.com's assets only: Saxo and Dukascopy have no live bars in WATA.
- The comparison with TradingView takes every signal the trader received for the indice from elsewhere than the engine: a manual signal from the admin counts as TradingView's.
- Vela 0.8.4 warns in the console about an indicator added on range bars ("rendering unanchored"); it draws it.
- **Backtests only cover the days the price recorder ran** (on the server since about 2026-10-13). For a while, runs will say they rest on too few trades and days.
- **A backtest sees ten seconds at a time**, against a monitor that looks about every second. The order of prices inside ten seconds is assumed, and how a stop is filled there is a setting.
- **The turbos the trader looked at are worked out again** from the recorded ones: a new issue is seen up to five minutes late.
- **Not modelled in backtests**:
  - a knocked-out turbo's residual value;
  - Saxo's commissions (settings, 0 by default);
  - the web server keeping only the newest signal of each indice;
  - the protective stop.

---

## v0.19.0 - Orders: every request to Saxo, and why Saxo refused one

**Release date:** deployed in the package numbered 0.18.0

On 2026-10-14 Saxo refused a market sell during a volatile market: "À ce stade, seuls des ordres limites sont autorisés" (at this stage only limit orders are allowed). The admin showed nothing of it. An order Saxo refuses has no id, and only the orders Saxo accepted were kept. The only traces were a log line and a Telegram message, and there Saxo's message was read in the wrong place: "Bad Request", then Saxo's whole answer, twice.

- **Every request to Saxo's order endpoint is kept with Saxo's answer** (table `order_request`): orders placed (buys, sells, the opt-in protective stop) and cancellations, accepted or not. Each one keeps:
  - what was sent, without the account's keys;
  - why it was sent: the exit rule, the explicit close, or the signal behind a buy;
  - what came back: the HTTP status, Saxo's code and message, and its answer as received.

  A request that got no answer (a timeout) is kept as such: Saxo may have taken the order all the same. The trader and the position monitor write these rows in the background, as they do the orders' moments: nothing on the way to an order waits for them, and a failure to write changes nothing of a trade (tested). How WATA trades is unchanged.
- **Orders page: what Saxo did not take is a row among the orders.** All the attempts made with one intent (selling one position, buying for one signal) form one row, placed at the first attempt: "Refused by Saxo ×37", how long it lasted, and the position.
  - Opened, the row shows:
    - what happened, in a sentence;
    - Saxo's answers, each with how many times it was given;
    - the order that went through afterwards, or "The position is still open";
    - every attempt, with what was sent and what came back.
  - The Result filter shows these rows alone, and the search finds them by the position's id or by a word of Saxo's message. A fourth figure above the table counts them, and the CSV export carries Saxo's code, message and status.
  - The sale that finally went through shows the refusals as the first step of its timeline.
- **Positions page.** An open position whose sell Saxo does not take says so under its name ("Sell refused ×37 · 14:04:30"), with Saxo's message on hover. Its details list every request about it, preceded by a sentence while it is still open.
- **A live notice** when Saxo refuses to sell an open position: at most once a minute per position, with the count since the last notice, and a browser notification when the tab is hidden. A refused buy was already announced, as its signal failing.
- **Saxo's answer said once, where Saxo put it**, in the logs, on Telegram and in the signal journal. The line of 2026-10-14 now reads: `ERROR closing 7779123400: API error placing order (Status: 400) (Saxo: À ce stade, seuls des ordres limites sont autorisés [OtherError]) (Order: Sell 6.0 units of 61252187)`. The Telegram message of a refused buy gives Saxo's code and message (it said "N/A"), and no longer prints the account key inside the order it shows.

- **The server's ports for HTTP and HTTPS can be set in `.env`** (`HTTP_PORT`, `HTTPS_PORT`, 80 and 443 when not set: nothing changes for a server that does not set them). HTTP is redirected to `HTTPS_PORT`, and the install script opens both in the firewall. TradingView and Let's Encrypt still only use 80 and 443, so another port needs something in front that forwards those (see the README, "Other ports than 80 and 443"). The admin interface's second port was already `ADMIN_PORT`; the development compose file reads `DEV_WEB_SERVER_PORT` and `DEV_RABBITMQ_PORT`.

**Verification status:** covered by tests: 1,552 in the Python suite, in an environment built from `requirements.txt`, and 642 in the interface's. One of them sends the refusal of 2026-10-14 through the real client: the record says 400, OtherError and Saxo's message, the error's text gives that message once, and the position can still be sold. The new table and the admin's reads were run on a real PostgreSQL 16, through the live tests and a new one in which refused requests written by the real manager are read back as a run, as an order's requests and as an open position's refused sells. The pages were driven in a browser against the admin application over made-up data, in both themes, at phone width and at 1,280 pixels:
- the run of 37 refusals, opened, filtered, and found by a word of Saxo's message;
- the marker and the details of an open position;
- the live notice.

**Not run against Saxo, the server's data or Docker**: the first refusal recorded on the server will be the first real one. Run on Python 3.12 only: CI runs 3.12 and 3.14.

### Upgrade Notes

- Nothing to configure. The trader and the position monitor create `order_request` at their next start; until then the admin reads it as empty.
- The Orders page reads the period's requests at each refresh: at most the 20,000 most recent.

### Known Limits

- **Not changed, on purpose:**
  - A sell decided by an exit rule that Saxo refuses is sent again at each price update, about once a second, for as long as the rule holds, with a Telegram message each time.
  - On a stop-loss, the start of the cooldown moves forward with each attempt.
  - A protective stop cancelled before a refused sell is not placed again.
  - WATA sends market orders only: while Saxo accepts nothing but limit orders, every attempt is refused.
- Which exception a refusal raises is unchanged. The client still reads Saxo's code at the top level to decide it, so an `InsufficientFunds` that Saxo puts under `ErrorInfo` is not recognised as one (it was not before either), and the client's order-endpoint branch still never runs.
- A request is written once its answer is known: a service stopped in between leaves no row for it.
- What is shown is bounded:
  - a run of refusals ends after five minutes without an attempt;
  - a run shows its 50 most recent attempts, and a position's details its 500 most recent requests.
- The Open tab's marker counts every refused sell of the position, including any from before an accepted sell that did not close it.

---

## v0.18.0 - Data section: Providers and Assets View

**Release date:** deployed as 0.18.0, with v0.19.0's orders

The Prices page showed what the Saxo price recorder kept, one instrument at a time, and nothing else: the Dukascopy history the backtests read was fetched and counted on the Backtests page but never shown, and the recorder's settings could only be changed in the JSON of the Configuration page. The page gives way to a **Data** section in the sidebar, between Analysis and Operations, with two pages.

- **Assets View** (`/data/assets`) lists every asset WATA keeps prices of: the index and the turbos recorded from Saxo, and each Dukascopy history. It can be filtered by type, provider and status, searched, and narrowed to the assets seen in the last 30 days, 7 days or today. A click on a row opens it to its last day with data: what the list of instruments said of it, and the chart of that day. The turbos Saxo left without a quote today are marked, as on the Prices page. Addresses of the Prices page still work: they lead here, or to the asset they showed.
- **Each asset has a page** (`/data/assets/<provider>/<key>`), reached from its name. It opens on the asset's last day with data, not on today: a turbo knocked out last week has nothing today. Five tabs: the chart of the Prices page, with its intervals, range bars and export; statistics, day by day (open, high, low, close, change, range, spread, quote changes, share of the trading time covered, Saxo's volume when recorded) and the spread through the day by half hour; the quality of the data (what Saxo left without a quote, the recorder's gaps, the coverage of each day; for Dukascopy, the days fetched); WATA's trades on the turbo, or on the turbos of the index; and every detail kept, with how it is recorded or where its files are.
- **Providers** (`/data/providers`) lists Saxo's price recorder and Dukascopy: the status of each (for the recorder: switched on in the configuration, running, writing), its assets, the span of its data, and a page of its own. "Add provider" lists what WATA has code for, Capital.com being shown as to come: a provider is code, not a setting.
- **The price recorder's settings can be changed from its provider**: switched on or off, the volume, the number of instruments, the refresh, the rate, the retention, the alert, and the instruments recorded besides the turbos (an index, by its UIC). The change is shown before it is saved, with what it costs (months of bars dropped by a shorter retention, prices that can never be fetched again once it is switched off), and saved as the Configuration page saves: validated, the previous file kept, audited. The recorder applies it at its next start, which the admin cannot do: the page says so.
- **Every page says where it is, in the top bar**: Home, the section, the page, and on a deeper page what it shows (an asset, a provider, a backtest, a day of it), each step a link back. On a phone, the step before the page alone. It takes the place of the backtest pages' links back to the list and to their run.
- **Other Dukascopy indices can be fetched**: the US 500 (`USA500IDXUSD`) and the Germany 40 (`DEUIDXEUR`), each under a label and in a folder of its own, from the Dukascopy page, still one download at a time. Keeping the history up to date stays for the Nasdaq history, the one backtests read. The downloader is unchanged, so no backtest is marked stale.

**Verification status:** covered by tests (1,495 in the Python suite, in an environment built from `requirements.txt`; 610 in the interface's). The query that lists the assets was run on a real PostgreSQL 16: about 45 ms for 3,041 instruments over four monthly partitions and 894,000 bars, the partitions pruned at run time; an instrument whose months the retention had dropped is not listed. A live test of the list was added to `tests/test_infrastructure_live.py`; the live database tests pass against a PostgreSQL 16. The pages were driven in a browser against the admin application over made-up prices, in both themes, at phone width and at 1,280 pixels: the recorder's settings saved to the development server's own configuration, a Dukascopy instrument added and removed. Dukascopy's symbols were fetched to be checked: the US 500 on two weekdays, at 7,730 to 7,800, and the Germany 40 on one, at about 25,380, each written as a day of bars. The US 30 (`USA30IDXUSD`) decoded at its level, about 51,000, but Dukascopy answered some of its hours with "503 Service Unavailable" at every try, so that none of its days could be written: it is not offered. **Not run against Saxo or the server's data, nor in Docker.**

### Upgrade Notes

- Nothing to configure. The admin writes one more file, `dukascopy/instruments.json` in the price history folder, where it could already write: no new mount.
- A setting of the recorder saved from its page applies at the recorder's next start (`docker restart price_recorder1`); switching it on for the first time also needs `recorder` in `COMPOSE_PROFILES`, then `watastop` and `watastart`.
- The admin's `/api/prices/*` routes are now under `/api/data/`.

### Known Limits

- The share of trading time covered counts WATA's trading hours, those of the Nasdaq session, for every asset, the Germany 40 included.
- A trade on a turbo the recorder never saw is not attributed to its index on the Trades tab.
- Dukascopy's files are days of UTC: a Dukascopy asset's days are those of its files, and it opens on the last one, whose last two hours fall on the next day in Paris.
- The admin never asks Saxo whether it quotes an instrument added by its UIC, sharing Saxo's limits with the trader: `wataprices probe --uic <n>`, on the server, says so before it is added.
- A Dukascopy history is read a month at a time at most; range bars, statistics and the recorder's gaps 31 days at most, what Saxo left without a quote 7.

---

## v0.17.0 - Prices page: missing quotes, details of an instrument, signal marks, Saxo's volume

**Release date:** 2026-10-13

Some turbos have stretches without a bar although the recorder was running and other turbos were quoted: Saxo sent no quote for them. Nothing on the Prices page said so, and "Recording gaps" answered "Recorded without interruption" under a chart with holes.

- **Turbos with missing quotes are marked in the list of instruments.** Two things are looked for, in trading hours and outside the recorder's own gaps: a hole of 30 seconds or more between two bars of a day, and a turbo with no more bar 15 minutes or more before the other turbos of its indice stopped. A turbo whose bid had fallen under a quarter of its highest of the day is taken for knocked out and is not marked. The count is above the list, and "Recording gaps" lists the times of the instrument shown beside the recorder's own. Worked out for periods of up to 7 days.
- **An instrument opens in the list** (the arrow beside its name) to its last bid and ask, the volume Saxo counted, its quote changes, its first and last bar, the share of the time it had a quote, its holes, its stop or probable knock-out.
- **The signals can be left off the chart**: "Signal marks", beside the legend, kept in the browser. The trades stay.
- **Saxo's volume can be recorded** (`trade.config.price_recorder.record_volume`, off by default): the total of the day of each instrument, in a table of its own (`price_volume`), one row each time it changes. `wataprices probe --uic <a turbo>` says whether Saxo has one for an instrument: to be asked before switching it on. The bars, their table and the code that builds them are unchanged, so no backtest is marked stale.

**Verification status:** the rule, the queries' text, the routes, the recorder and the pages are covered by tests (Python suite and interface suite pass, in an environment built from `requirements.txt`). The new SQL was run on a real PostgreSQL 16 with bars placed by hand (a hole, a stop, a knock-out, a volume over two days, a retention) and on a made-up week of two million bars: 0.25 s for a day, 1.2 s for the week, on a development machine. The page was driven in a browser against the admin application over made-up prices, in both themes and at phone width. **Not run against Saxo or the server's data**: whether Saxo sends a volume for the turbos, and whether it takes a subscription that asks for one, is unknown until `wataprices probe` and then `record_volume` are tried there; the thresholds of the rule (30 seconds, 15 minutes, a quarter) have only seen made-up prices.

---

## v0.16.0 - Python 3.14

**Release date:** 2026-10-13

The second of the two releases that move WATA from Python 3.12 to 3.14, and the one that does it: the image is now built with Python 3.14. Everything this needed was done in v0.15.0, on Python 3.12, so that this release is one line of the `Dockerfile` and nothing else. It was written to be deployed after v0.15.0 had traded for some days, so that a fault would say which of the two it came from. **The operator chose to deploy v0.14.0, v0.15.0 and this one together**, on 2026-10-13: the server went from 0.13.0 to 0.16.0 in one step, and a fault seen from now on may come from any of the three.

**Verification status:** CI built this image, with Python 3.14 for the server's platform, on the commits of v0.15.0: it holds exactly the libraries of `requirements.txt`, every service loads inside it, the tests pass on 3.14 and so do the tests against a real PostgreSQL and a real RabbitMQ. On a development machine, with Python 3.14.8: the same backtest, with its random entries, gives the same files to the byte as on 3.12; the admin application was started and every page visited in a browser, with a backtest launched from the page. **Not run yet**: the new CI job that builds the image with the server's own command (it runs on GitHub at the next push).

**At its deployment** (2026-10-13, 20:39 to 20:41 Paris: no position open, and no entry possible that evening, the day's result being above its limit; about two minutes without the trading services): the image was first built on the server while the application ran, and checked there: Python 3.14.8, the libraries of `requirements.txt` and no other, the server's own configuration accepted, every service loaded. After the start, all eight services ran on Python 3.14.8 with no error in their logs; the position monitor, the watchlist manager and the price recorder connected to Saxo's stream through the new WebSocket client and got their subscriptions; the trader took up its polling; the start messages of the trader and the monitor went through RabbitMQ and reached Telegram; `order_execution` was created. In the hour and a half that followed: Saxo closed the stream five times, at each renewal of the token, and each of the three clients came back within about a second with its subscriptions, as before the upgrade; the 21:55 closing signal went from the scheduler through the web server to the trader, which journaled that nothing was open; the 22:00 daily report went from the scheduler through RabbitMQ to the position monitor, reached Telegram and recorded the account's balance. **Not seen yet**: an order (none could be placed that evening: the first trade of the next session is the first on all of this), and a signal from TradingView (none arrived between the start and 22:04; the longest gap between two that day had been about three hours).

---

### What Changed

- **The image is built with Python 3.14** (`ARG PYTHON_VERSION=3.14`): the 3.14 current on the day the server builds it, 3.14.8 as this is written. Python 3.12 only receives security fixes and stops in 2028; 3.14 gets bug fixes until October 2027 and security fixes until October 2030.
- **The system under it does not change.** Looked up on the server at the deploy: the image in use already stood on Debian 13, the server asking the registry for the base image at each build, and the new one does too, with the same OpenSSL (3.5.7), which every connection to Saxo and to Telegram goes through, and the same compression library (zlib 1.3.1).
- **CI keeps testing Python 3.12** beside 3.14, and building the image with it, until 3.14 has run in production: going back stays something that is tested. A new job builds the image with the server's own command and checks that the Python it gets is the one the `Dockerfile` names.

Nothing changes in WATA's code, in its libraries or in the database.

### Upgrade Notes

- **This deploy stops the trading services longer than usual, unless it is prepared.** The install script stops the application and then builds the image, and this build has a new base image to download and every library to install again for Python 3.14. To keep the stop as short as any other, build the image once on the server while the application still runs, from a copy of this version's files and under a tag of its own (`docker build --platform=linux/amd64 -t wata-base:next .`): the build that follows the stop then finds the base image and the libraries already there. Done so on 2026-10-13: 46 seconds for that first build, and a stop of about two minutes.
- **Deploy it with no position open**, at a time when none can be opened.
- **To go back**: stop, put the previous version back in `/app/wata/VERSION`, give the previous image the tag `wata-base:latest` again, start. The previous image holds its own Python 3.12, so both come back together. Tag it before the deploy (`docker tag wata-base:latest wata-base:rollback-<version>`): the build replaces `latest`.
- **Watch after the start**: each service's start line, Saxo's authorisation without a manual login, the position monitor's first snapshot of the stream, the token renewal and the stream's reconnection about twenty minutes later, then the first signal from the webhook to its position and the first Telegram message.
- Nothing to configure.

### Known Limits

- **The price recorder writes one warning at its start**: `'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16`. It comes from uvloop, when the recorder asks to be told of a stop signal, and changes nothing today. It is the one thing Python 3.14 said in the services' output.
- A day of prices fetched again is expected to give the same file as before: a backtest keeps a digest of the price files it read, the library that compresses them is the same in both images, and on a development machine the two Pythons gave the same bytes. Not compared on the server itself.
- A development environment on Python 3.12 still runs the tests, for as long as CI does.

## v0.15.0 - The libraries brought forward, ahead of Python 3.14

**Release date:** 2026-10-13, deployed together with v0.16.0: it never ran on a server on Python 3.12

WATA runs on Python 3.12, which only receives security fixes and stops in 2028. Moving to Python 3.14 is not held back by WATA's code but by its libraries: ten of the versions it was pinned to cannot be installed on 3.14 at all, and several others that every signal and every position go through were released long before 3.14 existed. This release brings those libraries to versions that run on both, **still on Python 3.12**, so that the change of interpreter that follows is that and nothing else. Nothing changes in what WATA does.

**Verification status:** every test passes on Python 3.12 and on Python 3.14.8 with the new libraries (1,279 tests), on a development machine. The stream client's new tests were written and passed on the library of before, then showed what its upgrade breaks, then passed again once the client was adapted. The database tests ran against a real PostgreSQL 16.2 with asyncpg 0.30 and 0.31, on both interpreters. The same backtest, with its two hundred runs of random entries, gives the same files to the byte on both. The admin application was started on Python 3.14 and every page visited in a browser, a backtest asked for from the page included. All fifty-four libraries have a ready-made build for the server's platform on both interpreters. CI then ran its new jobs: the image builds with Python 3.12 and 3.14 and every service loads in it, and the tests against a real PostgreSQL and a real RabbitMQ pass on both. One job, the tests on 3.12, failed once and passed on the commits before and after it; which test it was is not known, and a failed test is now named beside its job. **Against Saxo**, the stream's new client was first used at the deployment of v0.16.0: see there.

---

### What Changed

#### Libraries
- **Upgraded to the first release that supports Python 3.14** (each also runs on 3.12): pydantic 2.7 to 2.12, asyncpg 0.30 to 0.31, uvloop 0.19 to 0.22, uvicorn 0.29 to 0.38, httptools 0.6 to 0.7, websockets 12 to 16, pika 1.3 to 1.4, aio-pika 9.5 to 9.6, jsonschema 4.22 to 4.26 with referencing and rpds-py, duckdb 1.1 to 1.4, PyYAML 6.0.1 to 6.0.3, typing_extensions 4.12 to 4.14. The defaults of every call WATA makes into them were compared before and after: none changed, but for the WebSocket client below.
- **Removed, because nothing uses them**: twenty-five pins (selenium, trio, psycopg, typer, rich, orjson, ujson, Jinja2, python-multipart, watchfiles and others). Read from the installed packages' own requirements, not guessed: nothing WATA imports needs them.
- **`requirements.txt` is now the whole environment.** Sixteen libraries were installed without being named (aiormq, yarl, h2, cffi…), so their version was chosen by pip on the server at each deploy. They are pinned, and CI fails when anything is installed that the file does not name, or the other way round.

#### The link to Saxo's stream
- The WebSocket library's default client was replaced upstream by a new one. `SaxoStreamClient` is adapted to it: same keepalive (a ping every 20 seconds), same limits, same reconnection, and still a direct connection (the new client would have looked for a proxy in the environment).
- **Its connection had no test.** It now has thirteen, ten of them against a WebSocket server on the machine: opening with the token and the context, reading Saxo's frames, Saxo dropping the connection without a word (the case seen every twenty minutes in production), coming back from the last message read without subscribing again, a `_disconnect` message, leaving. Without the adaptation, the upgrade would have broken the stop of the position monitor and every change of price subscription, and no test of before would have said so.

#### How the services start
- The trader, the position monitor and the price recorder run on uvloop as before, without making it the default of the whole process: the mechanism that did so is deprecated in Python 3.14 and removed in 3.16.

#### The image
- **Its Python is one line at the top of the `Dockerfile`** (`ARG PYTHON_VERSION=3.12`). CI builds the image with 3.12 and with 3.14.
- **Libraries are installed before the code is copied**, and only from ready-made builds. Until now every deploy downloaded all of them again while the trading services were stopped, because the code was copied first. A deploy that only changes the code now reuses them. The first build after this release still downloads everything.

#### CI
- The tests run on Python 3.12 and 3.14.
- **The image is built** for each, as the server builds it, and every service is loaded inside it.
- **PostgreSQL and RabbitMQ for real**, through the services' own code, on each Python: what the other tests replace with fakes.

### Upgrade Notes

- Nothing to configure, no change to the database.
- The first deploy of this release takes longer than usual: a few minutes for the libraries, once.
- A development environment needs `pip install -r requirements.txt` again: with the libraries of before, the stream client's new tests fail.

### Known Limits

- The web servers still load the WebSocket library's old server code, through uvicorn, though none of them serves a WebSocket. It is deprecated upstream, not removed.

## v0.14.0 - Each order followed to its position

**Release date:** 2026-10-13, deployed together with v0.16.0

An order and the position it gave were linked in the database and hardly on the pages, and nothing said how long Saxo took to turn one into the other: an order's time was written once its position had been confirmed, an order that gave no position was cancelled and written nowhere, and the sell order that closes a position was never recorded at all. This release makes the trader and the position monitor keep the moments of every order, and the admin interface show them, so that a slow entry or an order given up can be explained from the Orders page.

**Verification status:** covered by unit tests, six deliberate faults in the new trading code were each caught by them, and the pages were driven in a browser on a development machine, in both themes and at a phone's width, on made-up orders. The new SQL (the table, the two writes of an order in either order, the admin's joined reads through a read-only connection and their fallback) was run against a real PostgreSQL 16.2 on that machine. **On the server** since 2026-10-13: the table was created at the start, and no order could be placed that evening, the day's result being above its limit. **No real order has been followed yet**: the first trade of the next session will be the first. No new request is made to Saxo; what is read is the answer to requests WATA already made.

---

### What's New

#### Orders page: what became of each order
- **Sell orders and orders that gave no position are listed**, like the others. Until now the page only held the buy orders that had given a position.
- **Result**: opened a position, closed a position, no position (cancelled, or cancel refused), not seen closed, stopped by an error. The position's id is a link to it.
- **Took**: from the order leaving to WATA seeing its position open (or closed), or to the giving up. Sort on it to find the slow ones.
- **Three figures for the period**: the typical time from order to position, the slowest entry, and the number of orders Saxo accepted that gave no position.
- **A row opens onto each moment**: signal received, order sent, accepted by Saxo, filled at Saxo, seen by WATA, each with the time since the step before. For an order given up: how long and how many times the trader looked, then what Saxo answered to the cancellation, in Saxo's own words when it refused.
- Two clocks are involved and the page says which is which: the server's for what WATA did and saw, Saxo's for the fill. "Order sent" to "filled at Saxo" is the time that speaks about Saxo; "seen by WATA" also holds WATA's own waits between two looks.

#### Positions page: the order that opened it, the order that closed it
- A position's row opens onto **"Opened by order"** and **"Closed by order"**, each with its moments and a link to the order.
- A position Saxo closed by itself (a knock-out, a stop resting at Saxo, a sale made there by hand) says so, instead of showing no sell order without a word.
- A link from an order lands on its position among all of them, found and opened; a link from a position does the same on the Orders page, whenever the order was placed.

#### What the trading services now keep
- A table of their own, `order_execution`: for each order Saxo accepted, when it left, when Saxo answered, when its position was seen open or closed and at which look, or when the service gave up, with Saxo's answer to the cancellation.
- Kept in memory while the order is placed and **written in the background**, once Saxo has the order: nothing on the way to a buy or a sell waits for it, and a failure to write it is logged and changes nothing of the trade.
- The journal of signals keeps the order's id for a signal whose order gave no position, so that the signal behind such an order is found.

### What Changed

- **An order's recorded time is when it left**, no longer the moment after its position was confirmed (up to some ten seconds later). Orders already recorded keep the time they have: for them the page shows Saxo's fill time and the second WATA wrote the order down, and says the rest was not kept.
- The Orders page hides the side, the type and the cost until they are picked in the column list: the result says whether it was a buy or a sale.
- The message of an order that gave no position (Telegram, logs, journal) carries Saxo's answer when it refused the cancellation.

### Upgrade Notes

- Nothing to configure. The table is created by the trader and the position monitor when they start with this version; until then the admin reads the orders alone, as before.
- **Past orders are not filled in**: an order placed before this version has no moments, and its sell order was never recorded.

### Known Limits

- A cancellation Saxo refuses can mean that the order was filled after the trader stopped looking: such a position is at Saxo and not in WATA's database. The page says so and asks for a look at the account; WATA does nothing about it by itself.
- The stop order resting at Saxo (opt-in) is still not listed among the orders.

## v0.13.0 - Backtests that say how far to trust them, and what to change

**Release date:** 2026-10-13

A backtest gave a total, and nothing to tell a good one from luck. With WATA's rules a position is sold at its stop or as soon as the day has reached its target, so much of a result comes from the exits whatever the entry was. This release makes every backtest say what its total is worth, lets the model be checked against what WATA really did, and fetches the prices without anyone watching. It then gives the means to look for what to change: several values tried at once with days held out, the trades broken down, one trade followed on the turbo, and the result in money. Nothing changes for the trading services.

**Verification status:** covered by unit tests, and the pages were driven in a browser against the admin application on a development machine: a backtest with its random entries, the measure of the turbo, a replay of signals and its comparison, a sweep and the backtest made of one of its combinations, the breakdowns, a trade followed, renaming and removal, in both themes and at a phone's width. All of it on three days of prices, with "real trades" made up from them. At its deployment (2026-10-13, market open, no position open, about two minutes without the trading services) the admin's new code was run read-only on the server: it read the live rules, the folders of prices and runs, and both database reads ran against the real PostgreSQL (442 closed positions, 2 journal rows). **Not yet run on a server**: a backtest, a sweep, the measure of the turbo and the comparison with real trades, and the unattended download against Dukascopy.

---

### What's New

#### Every backtest is set against entries taken at random
- The same days are traded again **with a coin and a clock**: on each day as many entries as the strategy asked for, each at a moment drawn in the hours it may enter in, long or short by a coin, and only WATA's rules to end them. Two hundred times over, with the same draws from one run to the next.
- A run now says **the share of those random runs it did better than**. Near 50 %, the entries have shown nothing the exit rules do not explain; the page says so in words, and shows where the run stands among them.
- In the list of runs, in the comparison of runs and for each leverage of a run: "Beats random".

#### Figures that say what they rest on
- **Per trade**: what a trade brings on average, what was won for 1 lost, the most trades lost in a row.
- **The range of the total** when the same number of days is drawn again from the days tested (not given for fewer than five days).
- **Room for a stop and a target**: each trade is kept with the worst level it went through, beside its best. A run says how far winning trades fell before they won, and how high losing trades had been.
- **"Too few to conclude"**, in words, for a run of fewer than 30 trades or 20 days.

#### The turbo measured on WATA's closed trades
- A backtest prices a turbo from the index with three numbers that were guesses: what is paid for it, how many index points move it by 1.00, and its spread. **"Measure on real trades"** (Backtests page) reads WATA's closed positions and the index at the open and the close of each, and finds the turbo they describe, with the range the leverage could be in and how far the result is from the real trades.
- Backtests are then made with it: once for the leverage found, once for each end of its range.
- From a checkout: `python -m src.strategy.calibration --trades trades.json`.

#### The signals WATA really received, replayed
- "New backtest" can replay **the signals of WATA's own journal** instead of a strategy, through the same rules and the same turbo.
- The run is then set **against what WATA really did**: each real trade beside the model's, how far apart their results are, whether they ended the same way, the trades only one of the two made, and the days of both. The gaps found are the margin every backtest is to be read with.
- A signal is replayed as the trader receives it: an entry the rules refuse closes nothing. The minimum confidence asked of a signal, when that check is on, is now one of the rules a backtest applies.

#### Prices without babysitting
- **"Keep the prices up to date"** (Backtests page, off until switched on): the service fetches what is missing of the last 30 to 500 days by itself, the newest days first, a few at a time.
- **Only while the market is closed** by the live rules, and it waits longer each time the source stops answering: half an hour, then twice as long each time, up to twelve hours.
- A day the source has no more of is not asked for again.

#### Several values at once, with days held out
- "New backtest" has a section **"Try several values"**: one setting or two (a parameter of the strategy, a rule of WATA), each with the values to try. Every combination is replayed **in one pass over the prices**, of a strategy or of the signals WATA received.
- **The last days of the period are held out** (30 %, or 10 to 50): the page shows every combination on the days a value may be chosen on and, beside it, on the days that had no say in the choice. It says in words what became of the best one there: "it held up", or "it was fitted to the first ones".
- A combination is made **a backtest of its own** with one button, to be read with all a backtest says. From a checkout: `python -m src.strategy.sweep --axis rule:day_profit_target=1,1.7,3`.

#### Where a result comes from, and one trade followed
- **Breakdowns** on a run: its trades by hour of entry, weekday, direction, what they were entered and left on, confidence and holding time, with the same figures as the Exits page has for real trades, and how much of the best level was given back.
- **A trade leads to its day**, brought into view around it, with **the turbo's result every ten seconds** from the entry to the exit against the stop, the day's target and the trailing stop.

#### Less kind fills, and the result in money
- A backtest filled every decision at the first price of the next ten seconds. It can now **wait longer** before a fill and **lose index points** on each: what a result owes to perfect fills shows at once.
- With a starting capital (10,000 unless said otherwise), a run gives its result **in money at the size WATA would have given each trade**: the sizing by time of day and by confidence of the configuration, in whole turbos. Per trade, per day, and the account after each day.

#### Runs that can be found again, and trusted to be what they were
- A run keeps **what it was made with**: the version of the code and a fingerprint of the prices it read. One whose code or prices have changed since is marked **stale**, in the list and on its page.
- A run can be **named again and tagged** after it was made; the list is searched by tags.
- **Several runs are removed at once**: those ticked, the failed or stopped ones, or those older than 30 days. The list says what the runs and the prices take on disk.

---

### Things to Know

- **A total proves nothing by itself.** Read it with the random runs it beat and with the number of trades and days it rests on.
- **A sweep finds the value that did best, which is not the value that will.** The held-out days are the guard, and on a short period they are few. A sweep runs on one turbo and without the random entries.
- **Nothing is removed by itself.** There is no automatic retention: runs are removed when someone asks, a tick or a button at a time.
- **The money leaves out fees**: the broker's commission on each order is not counted.
- **Files of prices fetched from now on are the same to the byte whenever they are written.** The ones fetched before keep the time they were written in their header: a run made on them is marked stale if they are fetched again, even with the same prices.
- **Runs made before this release** show their figures as before, and a note where the new ones would be: run them again to have them.
- **Once the turbo is measured, new runs use it**, and can no longer be compared side by side with runs made on the assumed leverages (80, 110, 140): they have no leverage in common.
- **The measure of the turbo is rough.** The index of the history is another broker's and in dollars; the turbo is quoted in euros. A line through fewer than thirty trades is a first idea, and the page says so.
- **Both need the prices of the days WATA traded.** A trade whose day has no prices is left out of the measure; a signal that came at a moment without prices is not replayed.
- **Disk space:** about 5 MB for each day of prices, some 400 MB for the 120 days the unattended download goes back by default.
- **A backtest takes longer**: the random entries are the larger part of it, the more so as the rules hold a position longer. Three days took one second on a laptop with the example rules, eight with rules that sell nothing before the end of the day: count minutes for a year, more on a small server, where it runs at the lowest priority. A run is stopped after three hours. `--baseline 0` leaves the random entries out from the command line.

---

## v0.12.0 - Price Recorder

**Release date:** not yet released

This release adds an optional service that records the prices of the turbos WATA could buy, and of their index, every ten seconds, and a page of the admin interface to look at them. Saxo's history goes no finer than one minute, so this is what a later backtest on real prices needs, and it can only be collected from the day it is switched on. Nothing changes for the trading services, and nothing is recorded unless enabled.

**Verification status:** covered by unit tests, including the recorder fed by the real streaming client with made-up messages. It has not been run against Saxo, PostgreSQL or in Docker when this entry was written. Two things in particular are unconfirmed: that Saxo accepts a third streaming connection on the same token beside the Position Monitor and the Watchlist Manager, and how many instruments it allows in one price subscription (the recorder asks for at most 100 at a time). Run it in Saxo's simulation environment first. The Prices page was driven in a browser against the admin application running over made-up prices; its queries, like the recorder's, have not been run against a real PostgreSQL.

---

### New Features

#### Price Recorder (opt-in)
- New `price_recorder` service (`src/price_recorder/`), started with the Compose profile `recorder` and `trade.config.price_recorder.enabled`.
- **What is recorded:** the candidates of the Trader's own turbo selection (the same search, 20 per indice and direction), each kept until the end of the day once seen, and the index of each allowed indice.
- **Ten-second bars** of the bid and of the ask (open, high, low, close), built from Saxo's one-second stream and written to PostgreSQL (`price_bar_10s`, one partition per month), with each instrument's direction, knock-out level and issuer in `price_instrument`.
- **Gaps are explicit:** no bar is written for a time when no quote was arriving, and `price_recorder_coverage` says for each ten seconds whether the recorder was running and receiving.
- **It says when it stops:** a Telegram message after five minutes without a bar during trading hours, another when it resumes, and a "Price recorder" line among the services of the admin interface.
- **A database outage does not lose bars at once:** up to an hour of them waits in memory and is written when the database answers again.
- `wataprices --from ... --to ...` exports a period to a compressed CSV file; `wataprices probe ...` says whether Saxo prices an instrument for the account, to choose the one recorded for an index.
- New optional section `trade.config.price_recorder` (see the README). `retention_months` is 0 by default: nothing is ever deleted unless it is set.

#### Prices page in the admin interface
- New **Prices** page (Analysis): the recorded bars of one instrument as a candlestick chart, for today, yesterday, the last seven days or any range.
- **Bars of 10 s, 30 s, 1 min, 5 min, 15 min or 1 h**, of the bid or of the ask. Longer bars are computed by PostgreSQL from the ten-second ones; a chart holds at most 12,000 bars, and the lengths that would exceed it for the period are not offered.
- **Under a turbo, the index it follows**, on the same time axis.
- **What WATA did, on the prices:** entries and exits at the price paid and received (the exit with its result), and the signals received with what the Trader did with them. On the chart of an index, the trades of all its turbos.
- **What is missing is shown as missing:** a short stretch without bars is left empty on the chart rather than closed up, and the times of trading hours for which nothing was recorded are listed with their duration and whether the recorder was not running or no quote was arriving (for periods up to 31 days).
- The figures of the bar under the pointer are written above the chart; the bars are also in a table, with CSV export. The view (instrument, period, bar length, side) is in the address and can be bookmarked.
- When the period includes today, the page reads the data again every 30 seconds.
- **Range bars** ("Range", beside the bar lengths): a bar ends when the price has left a range of a set size, as in TradingView's range charts. The size is typed in ticks (60R) or as a price move, the two fields following each other, stepped with "−" and "+", or left to the page ("Auto", decided once per view so that a day still going is not redrawn with other bars). Built from the ten-second bars of the side shown, for periods up to 31 days; under a turbo, the index at the end of each bar. Each bar is given with when it began, when it ended and how long it lasted. No bar is made up across a night or a recording gap: the bar in progress ends there and the chart leaves an empty slot. The last size used is remembered in the browser, one for the index and one for turbos.
- Charts are drawn with TradingView's Lightweight Charts (Apache License 2.0), whose notice is on the page. It is only loaded with this page.
- Four read-only routes for it: `/api/prices/instruments`, `/api/prices/bars`, `/api/prices/range-bars`, `/api/prices/context`.

#### Strategy backtests, from the command line and in the admin interface
- WATA can **replay a strategy on prices of the past** and say what it would have done with it: which entries the Trader would have taken or refused, and where the Position Monitor would have sold. This is the first part of computing the signals inside WATA. **Nothing live uses it**: TradingView still sends the signals and no service runs the strategy.
- **The strategy** (`kernel_vwap`) is the one of the TradingView script, on one feed: on range bars of the index, a kernel regression crossing the VWAP of the session is an entry. `entry_mode=cross_catchup` adds an entry the script does not have, for a move that began before entries were allowed.
- **WATA's own rules are applied**, read from the configuration: market hours, trade limit, cooldown, day profit cap and loss limit, then stop, take-profit, trailing stop, daily target and end-of-day close, on a turbo modelled from the index. Index points alone say little: with the example configuration a position is sold at −15 %, or as soon as the day reaches +1.7 %.
- **Prices** come from Dukascopy's public tick files (US Tech 100, since 2013), fetched and kept as the ten-second bars the Price Recorder writes: `python -m src.strategy.history.dukascopy`. Files exported by `wataprices` are read the same way.
- **In the admin interface**, a new **Backtests** page (under Analysis): ask for a run with its period, the strategy's parameters and, for that run only, other values of WATA's rules; follow it; read its figures per leverage assumed, its curve, its days, its trades and the entries refused, with CSV export; open **one day as a chart** (range bars, kernel, VWAP and band, entries, exits and refusals); set **up to four runs side by side**; see which days have prices and **fetch more from the page**.
- From a checkout: `python -m src.strategy.backtest --from … --to …`.

#### Light theme for the admin interface
- The interface was dark only. It is now light or dark: it **follows the system setting**, and a button in the top bar and on the login page sets it to always light, to always dark, or back to following the system. The choice is kept in the browser.
- The theme is set before the page is painted, so a load does not flash in the other theme.
- Every page, dialog, menu, toast and chart has both themes. In light, the profit, loss, warning and information colours and the secondary text were given darker steps than their dark counterparts, so that each is readable (4.5:1 at least) on every light surface.
- Dark is unchanged: its pages were compared pixel for pixel before and after.

#### Admin interface without a domain of its own, and from a phone
- The interface can be served on a **second HTTPS port of the API domain** (`https://<API_DOMAIN>:8443/`), with the same certificate and behind the same allowlist: `ADMIN_DOMAIN` set to the API domain, `ADMIN_ENTRYPOINT=adminsecure` and `ADMIN_PORT=0.0.0.0:8443` in `.env`.
- Given the API domain without the other two lines, the interface is not served and the webhook keeps its route: the admin route now has the lowest priority.
- The README describes how to reach it **from an address that changes**, such as a phone on cellular data: a WireGuard tunnel to the server, and the tunnel's range as allowlist.
- Both were run on one server: the second port answers 403 outside the allowlist and serves the interface inside it, and an iPhone signed in through the tunnel, recorded under its VPN address. The device configuration first written in the README never connected; the one given now did. No other device was tried.

---

### Bug Fixes

- **Admin interface, development only:** signing in through the Vite dev server (`npm run dev`) was refused as a cross-origin request, because the proxy rewrote the `Host` header. It is now passed on unchanged. The deployed interface was not affected.

---

### Security

- **Eleven dependencies with known vulnerabilities were upgraded** in `requirements.txt`: the 27 alerts raised by GitHub's Dependabot, and four more recent advisories on the same packages. `anyio` 4.14.2, `cryptography` 50.0.1, `duckdb` 1.1.3, `idna` 3.15, `orjson` 3.11.9, `Pygments` 2.20.0, `python-dotenv` 1.2.2, `python-multipart` 0.0.32, `requests` 2.33.1, `ujson` 5.13.0, `urllib3` 2.8.0. No other installed package changes version.
- WATA's own code uses three of them: `cryptography` (the encrypted Saxo and webhook tokens), `requests` (Saxo token requests, Telegram) and `duckdb` (the old migration tool). **The stored tokens stay readable:** a token file written with the previous versions is read with the new ones, and the reverse, so neither this upgrade nor a return to the previous image invalidates them. A DuckDB file written by 0.10.2 is read by 1.1.3.
- **FastAPI, Starlette and pytest now have a version in `requirements.txt`**, like every other line. Dependabot does not check a line without one, and the versions that were installed, held back by an old `typing_extensions` pin, had six advisories of their own, five of them on Starlette 0.50.0. Now `fastapi` 0.135.1, `starlette` 1.3.1, `typing_extensions` 4.12.2, `pytest` 9.0.3, `pytest-asyncio` 1.4.0 and `pytest-cov` 7.1.0. The public advisory database (OSV) reports nothing on any of the 81 packages installed from the file.
- **Webhook server: the IP allowlist could be skipped with a malformed `Host` header.** This is one of the Starlette advisories, as it applied to WATA: a request to `/webhook` whose `Host` was, for instance, `x/latest-signals?` was taken for one of the internal paths, which are exempt from the IP check. It was still stopped by the token, and Traefik does not forward such a `Host` from the internet, so it concerned a caller reaching the container directly. Closed twice: by Starlette 1.3.1, and by the check now reading the path the router uses.
- The new versions are installed when the image is rebuilt, that is at the next deployment.

---

### Things to Know

- **Backtests run in the admin container**, as a child process at the lowest priority, one at a time, and the container is now limited to **one processor** (`cpus: '1.0'`). They are files under two new folders, `var/lib/backtests` and `var/lib/price_history`, which the installer creates and the admin mounts. `admin.backtests_enabled: false` switches the page off. None of this had been run in Docker or on a server when this entry was written: the interface was driven in a browser against the admin application on a development machine, running real backtests on three days of prices.
- **A backtest's turbo is a model.** Its leverage and its spread are assumptions until they are calibrated on real trades, so a run is made for three leverages and a conclusion should hold for all of them.
- **Dukascopy's prices are another broker's**, from an address it does not document, and its server refuses many requests and stops answering for a while when asked too much: a month takes hours and several downloads. One download at a time.
- **Traefik listens on one more port, 8443, whether the admin interface is used or not.** Only on the server itself (`127.0.0.1`) unless `ADMIN_PORT` says otherwise, so nothing new is reachable from outside. If something on the server already uses that port, Traefik does not start and the webhook is down with it: check with `ss -lnt | grep 8443` before upgrading, and give `ADMIN_PORT` another one if so (`ADMIN_PORT=127.0.0.1:9443`).
- **The recording starts when it is switched on.** Earlier prices cannot be fetched at this resolution.
- **Disk space:** roughly 8 to 14 GB a year for one indice, more on days when the index moves a lot. `max_instruments` bounds it.
- **The index may need naming.** If Saxo does not price the underlying of an indice for the account, the log says so and the instrument to record goes in `price_recorder.instruments`.
- **A delayed feed is recorded as such** (`delayed_minutes`), not corrected.
- **A range bar here is not bar for bar TradingView's.** "1R" is one step of the recorded Saxo prices, which may not be the tick of the symbol you look at in TradingView; what happens inside ten seconds is assumed (low before high when the price rose); and a bar opens where the previous one closed, where TradingView does not say what it does. Close enough to read the same moves, not to count the same bars.

---

## v0.11.0 - Admin Web Interface

**Release date:** not yet released

This release adds an optional web interface for the operator, and the hooks it needs in the Trader and the Position Monitor: a pause of new entries, a journal of what was done with each signal, the live value of open positions, the close of a single position, and a history of the account balance. The interface is a new service that is not started unless enabled. No configuration change is required to upgrade; three new tables are created automatically.

**Verification status:** covered by unit tests of the trading-path changes, of the admin API and of the interface, and by driving the built interface in a browser against the real admin application running over fake data. Nothing in this release had been run in Docker, behind Traefik, or against the real PostgreSQL, RabbitMQ and Saxo when this entry was written. The changes to the Trader and the Position Monitor should be exercised in Saxo's simulation environment before a live deployment.

---

### Behaviour Changes

- **The Trader reads one more value before opening a position:** whether new entries are paused. Like the other rule checks that read the database, a failure of that read means the signal is not traded.
- **The Trader records what it did with each signal** (`signal_journal`): executed, positions closed, refused by which rule, failed with which error, or ignored. It is written in the background, after the decision, and a failure to write it is only logged.
- **The Position Monitor publishes the latest price and performance of open positions** (`position_live_value`) every `streaming.live_value_interval_seconds` (5 s by default, `0` to switch it off). It is a copy made on a timer beside the position checks, never inside them.
- **The Position Monitor accepts a request to close one position** on the `trading-ops` queue (`close_position`). A request older than 60 seconds is ignored and reported on Telegram, so one that waited in the queue while the monitor was down cannot sell anything later. Such a close is recorded with the reason `Explicit Close (position <id>)`.
- **The daily report records the account balance** (`account_balance_history`, one value per day, the latest wins).

---

### New Features

#### Admin Interface (opt-in)
- New `admin` service (`src/admin/`, FastAPI on port 8082) serving a React interface built from `admin_ui/`.
- **Dashboard:** day profit and loss, trades against the daily limit, whether the next signal would be traded and what holds it back (pause, trading hours, closed date, daily limits, cooldown), open positions with their live value and unrealised profit or loss, latest signals, state of each service and of the Saxo connection. Changes are pushed to the page as they happen, with optional browser notifications.
- **On every page:** the top bar says when new entries are paused, when Saxo needs a new authorisation and when the services run an older configuration, each with a link to where it is dealt with.
- **Positions, orders, performance:** history with filters and CSV export; cumulative and daily profit and loss, win rate, long/short and per-instrument figures, read from PostgreSQL and grouped by day in `trade.config.general.timezone`. Unlike the Observable dashboard, it includes the trades recorded since the migration to PostgreSQL.
- **Report:** the figures of the daily Telegram report (profit factor, drawdown, streaks, weeks, months, years, milestone projections), computed by the same code, with the account balance over time.
- **Exits:** results by what closed the trade, by hour of entry and by holding time, and how much of the best level reached was given back.
- **Signals:** latest signal per indice, the journal of what the Trader did with each signal, and a form to send a manual `long` / `short` / `close-long` / `close-short` / `close-position` signal (through the Web Server's `/internal/signal`, so the Trader's rules apply).
- **Watchlist and execution timing:** the turbo that would be bought now per indice and direction; the time the Trader spent on each step of its recent trades.
- **Operator actions:** pause and resume new entries, close one position, hand over the Saxo authorization code from the browser, request the daily report. Each asks for a confirmation, is kept in an audit trail and announced on Telegram. Pausing, which only holds things back, is confirmed with one click; everything that acts on money or re-enables trading is confirmed with a slide.
- **Configuration editor:** a field that does not hold a usable value says so and blocks saving, and leaving the page with unsaved changes asks first. `config.json` shown with secrets masked. Only `trade.rules`, `trade.config`, `reporting` and `logging.level` can be changed, with forms for the common settings (including sizing periods, confidence bands, allowed indices and closed dates) and a JSON view for the rest. A change is validated with `ConfigurationManager`, the previous file is kept, and previous versions can be compared and their trading parameters restored. It takes effect the next time each service starts.
- **System and logs:** RabbitMQ queues, Watchlist Manager snapshot, Saxo token expiry, and the end of each service's log.

#### Pausing new entries
- While paused, the Trader refuses every `long` and `short` signal before closing or opening anything. Closing signals, the end-of-day close and the Position Monitor's stop-loss, take-profit and trailing stop keep working.
- The pause survives restarts (it is stored in `trade_risk_state`) until it is lifted from the interface.

#### Enabling and securing it
- Started only with the Compose profile `admin`: set `COMPOSE_PROFILES=admin`, `ADMIN_DOMAIN` and `ADMIN_ALLOWED_IPS` in `.env`.
- Served by Traefik on its own domain with its own IP allowlist, then a password login (`wataadminpasswd`, at least 12 characters, stored as a scrypt hash). Until a password is set, logins are refused.
- Optional second factor: `wataadmintotp` prints a key for an authenticator app, after which a one-time code is required at login (`wataadmintotp --disable` to remove it).
- The container reads PostgreSQL through connections set to read-only (a safeguard against a query that writes, not a separate database account); the pause of new entries is the one thing it writes, through a separate connection. It mounts `var/lib/saxo_auth`, `var/lib/web_server` and `var/lib/admin` rather than all of `var/lib`, the logs of the other services read-only, and has no Docker socket.
- The configuration editor never writes credentials, infrastructure settings or the `admin` section, when saving or when restoring. The addresses the services connect to and listen on (`trade.config.signal_polling.service_url`, `trade.config.watchlist_manager.service_url`, `api_host`, `api_port`) sit inside the editable sections and are refused as well: they are changed on the server.
- The login lockout and the audit trail use the address reported by the reverse proxy named in `admin.trusted_proxy_hosts` (`["traefik"]` by default). A request reaching the admin port any other way is known by its own address.
- Optional `admin` section in `config.json`; `actions_enabled` and `config_edit_enabled` switch the operator actions and the editor off.

---

### Things to Know

- **Requesting the daily report early changes what is recorded for the day.** The report writes today's row of `trade_performance`, and that row is written once: requested before the last trade, it keeps the figures of that moment. The interface says so before sending.
- **A saved or restored configuration applies at each service's next start,** including when a service restarts by itself after a crash. Until then the running services keep the configuration they started with.
- **Restoring a previous version restores its trading parameters only.** Credentials, infrastructure settings and the `admin` section stay as they are on the server, whatever that version held for them.
- **A manual `long` or `short` signal closes the positions already open,** like a TradingView alert does. The interface says so before sending.
- **The signal journal starts with this version** and lists the signals the Trader received. A signal replaced by a newer one for the same indice before the Trader read it is not listed.
- **The account balance curve starts with the first daily report after the upgrade,** one point per day.
- **Building the package now needs Node.js** (version in `admin_ui/.nvmrc`): `./package.sh` builds the interface before creating the zip. Nothing is built on the server.

---

### Technical Changes

- New tables `position_live_value`, `signal_journal` and `account_balance_history`, created by `init_schema` when the Trader or the Position Monitor starts.
- `TradingRule.check_entries_paused()`; `AsyncDbRiskStateManager` gains `pause_entries`, `resume_entries` and `get_entries_paused_since`.
- `SignalJournal` (`src/trade/signal_journal.py`); `AsyncPerformanceMonitor.live_values()` and the `only_position_id` argument of `close_managed_positions_by_criteria`.
- `PostgresConnectionManager` accepts optional `server_settings`; `SaxoAuth.get_token_status()` describes the stored token without exposing it.
- `deploy/install_package.sh` creates `var/lib/admin` and `var/log/wata-admin` and installs `wataadminpasswd` and `wataadmintotp`.
- CI runs the interface's lint, type-check, tests and build.

---

## v0.10.0 - Exactly-Once Signals, Faster Execution & Resilient Monitoring

**Release date:** 2026-10-11

This release fixes how signals travel from the webhook to the Trader, removes fixed waits from the trade path, keeps the Saxo session ready between signals, and makes the Position Monitor independent of slow callbacks and of the database. No configuration change or database migration is required to upgrade.

**Verification status:** covered by unit tests, a local run of the real Web Server with the Trader's polling loop, and a simulation of the Position Monitor against fake Saxo latencies. The changes had not yet been exercised against Saxo's API when this release was cut. The broker-side protective stop in particular must be verified in Saxo's simulation environment before it is enabled on a live account.

---

### Behaviour Changes

- **Each signal is executed once.** The Web Server keeps the latest signal per `indice` until a newer one replaces it, and the Trader used to process every stored signal again on every poll. It now skips any `signal_uuid` it has already handled. Before this, a stored `long`/`short` stayed executable for `max_signal_age_seconds`, a stored `close-position` would have closed every later position, and the Trader log was flooded (two lines per stored signal every 500 ms).
- **The end-of-day close now works.** In v0.9.0 the Scheduler container did not mount `var/lib`, so it could not read the Web Server token: every `close-position` it sent at `close_position_time` was rejected with `401 Unauthorized`, and positions stayed open overnight and over weekends. After this upgrade, open positions are closed at `close_position_time` every day.
- **A restarted Trader does not replay old signals.** Signals the Web Server received more than `startup_replay_max_age_seconds` (10 s) before the first poll are ignored.
- **The Position Monitor reconciles the database with Saxo less often.** It used to call Saxo's REST API on every price update. It now reconciles when a position leaves the stream and every `reconcile_interval_seconds` (30 s).
- **The loss cooldown also applies to positions closed outside WATA** (for example in the Saxo platform, or by the protective stop) when they closed at a loss.

---

### New Features

#### Long-Polling Signal Delivery
- `/latest-signals` accepts `after=<seq>` and `wait_ms`: the request is held until a newer signal is stored. The Trader receives a signal as soon as the webhook stores it instead of on the next 500 ms poll.
- The Trader falls back to interval polling by itself if the Web Server does not support long-polling.

#### Saxo Session Kept Ready
- The Trader calls Saxo every `keep_warm_interval_seconds` (30 s) between signals. This keeps the HTTP connection open and renews the access token away from the trade path.
- Optional spending-power cache for order sizing (`balance_cache_max_age_seconds`, off by default).

#### Broker-Side Protective Stop (opt-in)
- With `position_management.protective_stop.enabled`, each new position gets a resting sell stop at Saxo, so that a Position Monitor outage is survivable.
- `percent` must be wider than `stoploss_percent`: the Position Monitor's own stop acts first in normal operation.
- The stop is placed after the entry and rounded down to a valid tick. Nothing is sent if the instrument does not list the order type as supported; the trade notification says whether the stop was placed.
- Any order resting on a position is cancelled before WATA closes that position.

---

### Changed

#### Trade Path
- On a reversal, the sell is sent and its bookkeeping (closed-position lookup, database update, notification) runs in the background. In close-then-open mode the Trader waits only until Saxo shows the old position closed, capped at the previous 1.5 s.
- Position confirmation after an order checks immediately instead of sleeping 300 ms first, then backs off within the same overall patience.
- The balance request starts alongside the turbo selection, and the watchlist lookup reuses one HTTP connection.

#### Saxo Authentication
- A renewed access token is swapped into the existing HTTP client instead of rebuilding the client.
- Token handling no longer runs on the event loop, and token requests have a 10 s timeout.

#### Position Monitor
- Reading the stream and checking positions are separate tasks. A slow check or a closure in progress no longer delays reading price updates, and updates that arrive meanwhile are merged so the next check works on current prices.
- Stop-loss, take-profit and trailing-stop sells are sent first; the closure is recorded in the background.
- Position checks keep working when PostgreSQL is slow or unreachable, using the last state read.
- The trailing-stop peak is kept in memory and saved when it rises.

---

### Fixed

- The Scheduler's end-of-day `close-position` never reached the Trader (missing `var/lib` volume on `scheduler1` in `deploy/docker-compose.yml`).
- Trader, Position Monitor and Watchlist Manager could renew the Saxo token at the same time with the same single-use refresh token. Renewal now takes a lock shared by the services, and the token file is written atomically.
- A position with a sell order in flight could be sold a second time.
- A closed position not yet listed by Saxo after 1.5 s was left open in the database; the lookup now retries.
- The database was not reconciled when the last open position left the stream.

---

### Configuration and Deployment

All new settings are optional; `etc/config_example.json` lists them with their defaults.

| Setting | Default | Purpose |
|---------|---------|---------|
| `trade.config.signal_polling.long_poll_wait_ms` | 1000 | Long-poll wait; `0` restores polling every `interval_ms` |
| `trade.config.signal_polling.startup_replay_max_age_seconds` | 10 | Replay window after a Trader restart |
| `trade.config.general.keep_warm_interval_seconds` | 30 | Saxo keep-warm call; `0` disables it |
| `trade.config.general.streaming.reconcile_interval_seconds` | 30 | Period of the safety-net reconciliation |
| `trade.config.buying_power.balance_cache_max_age_seconds` | 0 | Spending-power cache for sizing; `0` asks Saxo on every trade |
| `trade.config.position_management.protective_stop` | disabled | `enabled`, `percent`, `order_type` (default `StopIfTraded`) |

- Deploy the Web Server and the Trader together to get long-polling.
- Use the `deploy/docker-compose.yml` of this release: it adds the `var/lib` volume to `scheduler1`. Do not apply that volume to v0.9.0 on its own, where a delivered `close-position` would be executed again on every poll.
- The Saxo token directory gains a `.token_refresh.lock` file.

---

## v0.9.0 - Async Architecture, PostgreSQL & Streaming

**Release date:** 2026-10-11

This is a cumulative release covering all changes since the last published tag, v0.6.1. It includes the v0.7.0 transition to async services, PostgreSQL, and Saxo WebSocket streaming, along with the subsequent trading, reporting, watchlist, signal-handling, and deployment changes.

---

### v0.7.0 Foundation: Async Architecture, PostgreSQL & Streaming

#### Breaking and Migration Changes

- Replaced embedded DuckDB with PostgreSQL 16 and an async `asyncpg` connection pool. The DuckDB config is no longer used at runtime; `src/database/migration.py` migrates existing data.
- Added the `position_monitor` service. Position checks no longer depend on the scheduler's 7-second REST polling job.
- Split RabbitMQ routing into `trading-signals` for trade signals and `trading-ops` for operational commands.
- Added required `postgresql` and `trade.config.general.streaming` configuration and the PostgreSQL and Position Monitor deployment services. The migration guide below contains the v0.6.x upgrade steps.

#### Async Trading Services

- Moved service-critical paths to `async`/`await`. The Saxo HTTP client uses `httpx.AsyncClient` with HTTP/2, async rate limiting, and token refresh.
- Added async instrument search, order placement, position queries, trading orchestration, and performance monitoring, including parallel searches and bounded-concurrency position checks.
- Reworked the Trader to consume signals with `aio-pika` and added an async Telegram sender.
- Added PostgreSQL managers for orders, positions, and trade performance, with connection pooling and automatic schema creation.

#### Real-Time Saxo WebSocket Streaming

- Added a persistent Saxo streaming client for position subscriptions. It decodes binary and delta-compressed updates, maintains a position snapshot, and handles heartbeats, subscription resets, and disconnects.
- Added automatic reconnect with exponential backoff and message-ID resume, plus periodic WebSocket reauthorization.
- The Position Monitor evaluates streamed positions for stop-loss, take-profit, and trailing-stop rules; scheduled operational events, including daily statistics, use `trading-ops`.

#### v0.7.0 Performance Impact

| Metric | Before (v0.6.x) | After (v0.7.0) |
|--------|-----------------|----------------|
| Position-check latency | Up to 7s (poll interval) | Sub-second (stream push) |
| API calls per check cycle | 3+ REST calls | 0 (data pushed by WebSocket) |
| HTTP protocol | HTTP/1.1 (`requests`) | HTTP/2 (`httpx`) |
| Database | Embedded DuckDB | Pooled PostgreSQL |
| I/O model | Synchronous/blocking | Async (`asyncio`/`uvloop`) |

---

### Additional Changes Since v0.6.1

- Expanded configurable risk-management and trading rules, improved instrument ranking by Bid price and InfoPrices diagnostics, and optimized async turbo-candidate validation.
- Improved PostgreSQL and streaming reliability, including consistent date/datetime normalization.
- Enhanced simulation and reporting, expanded CI and unit-test coverage, and added a trading-dashboard build script.

---

### v0.9.0 Updates

#### Breaking Changes

- **Signal delivery no longer uses the `trading-signals` RabbitMQ queue.** The web server keeps the newest signal per `indice` in memory, and the async Trader polls `/latest-signals` (500 ms by default). Older signals for an `indice` are ignored; only the latest is retained.
- **Legacy synchronous Saxo trading services have been removed.** Integrations must use the async services in `src/trade/async_services.py`.

---

#### New Features

##### Background Turbo Watchlists
- Added a dedicated `watchlist_manager1` service that periodically selects the best turbo candidates for configured indices and directions.
- Candidate prices are refreshed from Saxo's streaming API and cached for the Trader. The manager exposes health and best-candidate endpoints and reports whether cached prices are stale.

##### Secure Webhook Deployment
- Added Traefik as the public webhook ingress, with HTTP-to-HTTPS redirection and automatic Let's Encrypt certificates.
- Added an IP allowlist for TradingView webhook traffic.

##### Trading Reports and Monitoring
- Daily reports now include account balance, milestone projections, closed-trade history, and daily profit history.
- Added signal timing details and an optional early trigger for daily statistics.

---

#### Changed

- Improved async turbo candidate discovery and validation to reduce unnecessary work during instrument selection.
- Improved Saxo streaming and PostgreSQL handling, including consistent normalization of date and datetime values.
- Updated trading simulation and risk-rule logic, and expanded reporting calculations.
- Improved deployment and package-install scripts, HTTP/2 support for the async API client, and configuration handling.

---

#### Configuration and Deployment

- Add the `trade.config.watchlist_manager` and `trade.config.signal_polling` settings from `etc/config_example.json` to the deployment configuration.
- Set `ACME_EMAIL` and `API_DOMAIN` in `deploy/.env` to enable HTTPS certificate issuance for the webhook endpoint.
- Deploy the new `watchlist_manager1` service with the updated Compose configuration.

---

## v0.7.0 - Async Architecture & Real-Time Streaming

**Release date:** 2026-03-29

This is a major architectural release that migrates WATA from a synchronous, polling-based system to a fully asynchronous, real-time streaming architecture. Every service-critical path is now `async/await`, the database backend moves from DuckDB to PostgreSQL, and position monitoring switches from 7-second REST API polling to Saxo's WebSocket Streaming API.

---

### Breaking Changes

- **Database: DuckDB → PostgreSQL.** The embedded DuckDB database is replaced by a PostgreSQL 16 instance. A migration tool is provided (see Migration Guide below). The `duckdb` config section is no longer used at runtime.
- **New service: `position_monitor`.** Position monitoring is now a dedicated container (`WATA_APP_ROLE=position_monitor`) that must be deployed alongside the existing services.
- **Scheduler no longer polls positions.** The `job_check_positions_on_saxo_api` job (every 7 seconds) has been removed. Position checks are now driven by real-time WebSocket streaming in the Position Monitor.
- **RabbitMQ queue split.** Trading signals now go to `trading-signals` (consumed by Trader); operational commands go to `trading-ops` (consumed by Position Monitor). Previously everything went to a single queue.
- **New config sections required.** `postgresql` and `trade.config.general.streaming` must be added to `config.json` (see Migration Guide).

---

### New Features

#### Real-Time WebSocket Streaming (Phase 2)
- **`src/saxo_streaming/client.py`** - New persistent async WebSocket client for Saxo Bank's streaming API.
  - Subscribes to position updates via `POST port/v1/positions/subscriptions`.
  - Parses binary frames using the existing `decode_ws_msg()` utility.
  - Applies delta-compressed updates to an in-memory position snapshot.
  - Handles all Saxo control messages: `_heartbeat`, `_resetsubscriptions`, `_disconnect`.
  - Auto-reconnects with exponential backoff and `messageid` resume.
  - Periodically re-authorises the WebSocket connection (default: every 15 minutes).

#### Async Architecture (Phase 1)
- **`src/saxo_openapi/async_client.py`** - Async HTTP client using `httpx.AsyncClient` with HTTP/2, async rate limiting, and hot-swappable tokens.
- **`src/trade/async_services.py`** - Async reimplementation of all trade services:
  - `AsyncSaxoApiClient` - token-refreshing facade.
  - `AsyncInstrumentService` - parallel turbo search.
  - `AsyncOrderService` - non-blocking order placement.
  - `AsyncPositionService` - concurrent position queries.
  - `AsyncTradingOrchestrator` - parallel instrument search + spending-power fetch.
  - `AsyncPerformanceMonitor` - SL/TP/trailing-stop checks with bounded-concurrency closures.
- **`src/database/postgres.py`** - Full async PostgreSQL layer with connection pooling (`asyncpg`), auto-schema creation, and equivalent managers for orders, positions, and trade performance.
- **`src/mq_telegram/async_tools.py`** - Async Telegram message sender using `aio-pika`.
- **`src/trader/__init__.py`** - Fully async Trader service consuming from `trading-signals` queue via `aio-pika`.
- **`src/position_monitor/__init__.py`** - New dedicated service that combines WebSocket streaming for real-time position monitoring with queue consumption for time-triggered events (`daily_stats`).
- **`src/database/migration.py`** - One-time DuckDB→PostgreSQL migration tool.

---

### Changed

- **`deploy/docker-compose.yml`**
  - Added `postgres1` service (PostgreSQL 16 Alpine) with health checks and volume mapping.
  - Added `position_monitor1` service as a new container.
  - Trader and web server now depend on `postgres1` health check.

- **`src/start_python_script.sh`**
  - Added `position_monitor` and `trader_legacy` cases.
  - The new `trader` role runs the async Trader; `trader_legacy` runs the old sync `main.py`.

- **`src/scheduler/__init__.py`**
  - Removed `job_check_positions_on_saxo_api` (7-second polling loop).
  - Added smart queue routing: `daily_stats` and `check_positions_on_saxo_api` → `trading-ops`; trade signals → `trading-signals`.
  - `job_daily_stats` and `job_close_position` are unchanged.

- **`src/web_server/__init__.py`**
  - Webhook now publishes to `trading-signals` queue instead of the generic queue.

- **`src/trade/async_services.py`**
  - `AsyncPerformanceMonitor` gains `check_positions_from_stream(streamed_positions)` - accepts pre-fetched position data from the WebSocket stream instead of polling the REST API.
  - Internal logic refactored into shared `_evaluate_positions()` used by both stream-fed and REST-polled paths.

- **`etc/config_example.json`**
  - Added `postgresql` section with DSN and pool sizing.
  - Added `trade.config.general.streaming` section with `refresh_rate_ms`, `reconnect_delay_seconds`, `max_reconnect_delay_seconds`, `reauth_interval_seconds`.
  - Existing `websocket` and `position_check` sections kept for backward compatibility.

- **`requirements.txt`**
  - Added: `asyncpg==0.30.0`, `aio-pika==9.5.4`.
  - Already present: `websockets==12.0`, `httpx==0.27.0`, `uvloop==0.19.0`.

---

### Architecture Diagram (Before → After)

**Before (v0.6.x):**
```
TradingView → Webhook → RabbitMQ (single queue) → Trader (sync, requests)
                                                       ↕
Scheduler (every 7s) → RabbitMQ → Trader → Saxo REST API (3+ calls per check)
                                                       ↕
                                                    DuckDB
```

**After (v0.7.0):**
```
TradingView → Webhook → RabbitMQ [trading-signals] → Async Trader (httpx/HTTP2)
                                                           ↕
                                                      PostgreSQL
                                                           ↕
             Saxo WebSocket Stream ←→ Position Monitor (real-time)
                                                           ↕
             Scheduler → RabbitMQ [trading-ops] → Position Monitor (daily_stats)
```

---

### Performance Impact

| Metric | Before (v0.6.x) | After (v0.7.0) |
|--------|-----------------|-----------------|
| Position check latency | Up to 7s (poll interval) | Sub-second (stream push) |
| API calls per check cycle | 3+ REST calls | 0 (data pushed via WebSocket) |
| HTTP protocol | HTTP/1.1 (requests) | HTTP/2 (httpx) |
| Database | DuckDB (embedded, single-writer) | PostgreSQL (pooled, concurrent) |
| Message broker pattern | Single queue | Split queues (signals vs ops) |
| I/O model | Synchronous (blocking) | Fully async (asyncio + uvloop) |

---

# Migration Guide - v0.6.x → v0.7.0

## Prerequisites

- Docker and Docker Compose
- Access to your current `config.json` and DuckDB database file
- A brief maintenance window (services will be restarted)

## Step 1: Set PostgreSQL Password

In `/app/wata/.env` in your server, set a secure postgres password:

```bash
POSTGRES_PASSWORD="your_secure_password_here"
```

## Step 2: Update Configuration

Add the following sections to your `etc/config.json`:

### 2a. PostgreSQL connection

Add at the top level (next to `duckdb`):

```json
"postgresql": {
  "dsn": "postgresql://wata:YOUR_PASSWORD@postgres1:5432/wata",
  "pool_min_size": 2,
  "pool_max_size": 10
}
```

> **Important:** Replace `YOUR_PASSWORD` with a strong password. Set the same password as the `POSTGRES_PASSWORD` environment variable in `/app/wata/.env`.

### 2b. Streaming configuration

Add inside `trade.config.general`:

```json
"streaming": {
  "refresh_rate_ms": 1000,
  "reconnect_delay_seconds": 1.0,
  "max_reconnect_delay_seconds": 30.0,
  "reauth_interval_seconds": 900
}
```

| Key | Description | Default |
|-----|-------------|---------|
| `refresh_rate_ms` | How often Saxo pushes position updates (milliseconds) | `1000` |
| `reconnect_delay_seconds` | Initial delay before reconnecting after a WS drop | `1.0` |
| `max_reconnect_delay_seconds` | Cap for exponential-backoff reconnection | `30.0` |
| `reauth_interval_seconds` | How often to re-authorise the WS connection | `900` (15 min) |

### 2c. Keep existing sections

The `duckdb`, `websocket`, and `position_check` config sections can remain - they are not used by the new services but won't cause errors.

## Step 3: Rebuild the Docker Image

```bash
./docker_build.sh
```

This rebuilds `wata-base:latest` with the new code and dependencies.

## Step 4: Start PostgreSQL First

```bash
cd deploy
docker compose up -d postgres1
```

Wait for it to be healthy:

```bash
docker compose ps  # should show postgres1 as "healthy"
```

## Step 5: Migrate Data from DuckDB

Run the migration tool from inside a container:

```bash
docker compose run --rm \
  -e WATA_APP_ROLE=trader \
  -e WATA_CONFIG_PATH=/app/etc/config.json \
  trader1 \
  python -m src.database.migration
```

This reads all data from your existing DuckDB file and inserts it into PostgreSQL. The tool is idempotent - running it twice won't duplicate data.

Verify the migration:

```bash
docker compose exec postgres1 psql -U wata -c "SELECT count(*) FROM turbo_data_order;"
docker compose exec postgres1 psql -U wata -c "SELECT count(*) FROM turbo_data_position;"
```

## Step 6: Deploy All Services

```bash
docker compose up -d
```

This starts all services including the new `position_monitor1` container.

## Step 7: Verify

1. **Check logs** for the Position Monitor:
   ```bash
   docker compose logs -f position_monitor1
   ```
   You should see:
   ```
   WebSocket connected.
   Position subscription created (refId=pos-xxxx). Snapshot: N positions.
   ```

2. **Check Telegram** - you should receive:
   ```
   WATA Position Monitor vX.X.X is running (WebSocket streaming + trading-ops queue).
   ```

3. **Verify the scheduler** no longer polls:
   ```bash
   docker compose logs scheduler1 | grep check_positions
   ```
   Should show no new `check_positions_on_saxo_api` messages.

4. **Test a webhook** to verify the full signal flow still works end-to-end.

## Rollback

If you need to roll back:

1. Revert to the previous Docker image (tag your images before upgrading).
2. The DuckDB database file is untouched - the migration tool only reads from it.
3. Remove the `postgresql` and `streaming` config sections from `config.json`.
4. Bring down the new containers: `docker compose down`.
5. Restart with the old image.

## New Service Map

| Container | Role | Queue | Purpose |
|-----------|------|-------|---------|
| `web_server1` | web_server | publishes to `trading-signals` | Webhook endpoint |
| `trader1` | trader | consumes `trading-signals` | Executes trades |
| `position_monitor1` | position_monitor | WebSocket + consumes `trading-ops` | Real-time SL/TP/trailing-stop + daily stats |
| `scheduler1` | scheduler | publishes to `trading-ops` / `trading-signals` | Timed events (daily_stats, close-position) |
| `telegram1` | telegram | consumes `telegram` queue | Notifications |
| `rabbitmq1` | - | - | Message broker |
| `postgres1` | - | - | Database |
