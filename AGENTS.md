# WATA — AGENTS.md

## Project

Automated trading assistant for Saxo Bank Knock-out warrants (Turbos). Python 3.12, FastAPI, async microservices in Docker.

## Service map

| Role               | Entrypoint (`__init__.py`) | Communicates via                          |
|--------------------|-----------------------------|-------------------------------------------|
| `web_server`       | `src/web_server/`           | FastAPI on `:80`, query-param token auth  |
| `trader`           | `src/trader/`               | HTTP-polls web_server `/latest-signals`   |
| `position_monitor` | `src/position_monitor/`     | Saxo WebSocket streaming + `trading-ops`  |
| `scheduler`        | `src/scheduler/`            | Publishes to `trading-ops` / web_server   |
| `telegram`         | `src/mq_telegram/`          | Consumes `telegram` queue                 |
| `watchlist_manager`| `src/watchlist_manager/`    | REST API on `:8081`                       |
| `admin`            | `src/admin/`                | FastAPI on `:8082`, cookie session; serves the React UI built from `admin_ui/` |
| `price_recorder`   | `src/price_recorder/`       | Own Saxo stream; writes PostgreSQL only. Nothing calls it |

Each service runs as a Docker container; `WATA_APP_ROLE` env var selects which `__init__.py` runs via `src/start_python_script.sh`.

## Infrastructure

- **RabbitMQ** — queues: `trading-signals` (trade actions, no longer consumed), `trading-ops` (ops/daily_stats), `telegram_channel` (notifications)
- **PostgreSQL 16** — replaces DuckDB (migrated in v0.7.0). DuckDB config is dead.
- **Traefik** — reverse proxy, Let's Encrypt, TradingView IP allowlist
- **Config** — JSON at `WATA_CONFIG_PATH` path, validated strictly by `ConfigurationManager`

## Commands

```bash
# Run all tests
PYTHONPATH=./:src/ pytest --cov=src --cov-report=term-missing --cov-report=html -vv tests/

# Run a single test file
PYTHONPATH=./:src/ pytest tests/test_web_server.py -vv

# Backtest a strategy (fetch index history first; nothing here touches the trading services)
PYTHONPATH=./:src/ python3 -m src.strategy.history.dukascopy --from 2026-09-14 --to 2026-10-12
PYTHONPATH=./:src/ python3 -m src.strategy.backtest --from 2026-09-14 --to 2026-10-12
PYTHONPATH=./:src/ python3 -m src.strategy.calibration --trades trades.json   # the turbo, measured on real trades
PYTHONPATH=./:src/ python3 -m src.strategy.sweep --from 2026-09-14 --to 2026-10-12 --axis rule:day_profit_target=1,1.7,3

# Build deployable zip (also builds admin_ui/ into src/admin/static; needs Node, see admin_ui/.nvmrc)
./package.sh          # creates wata_app_v<VERSION>.zip

# Admin interface (in admin_ui/)
npm run lint && npm run typecheck && npm test && npm run build
npm run dev:api       # the real admin app over fake data on 127.0.0.1:8082 (password printed at start)
npm run dev           # Vite dev server, proxies /api to it

# Deploy (via Ansible)
./deploy/tools/deploy_app_to_your_server.sh
```

## Testing quirks

- **`WATA_CONFIG_PATH` must be set** before importing `src.web_server` (tests set `os.environ["WATA_CONFIG_PATH"] = "tests/test_config.json"`)
- Test config at `tests/test_config.json` — omit `day_trading` rule if testing with `trading_mode != "day_trading"`
- `tests/test_web_server.py` patches `verify_token` and `Request.client` for IP filtering tests
- `tests/test_config.json` uses minimal rule set; adding more rules requires updating config

## Architecture notes

- **Trader does NOT consume RabbitMQ** for trade signals — it long-polls `web_server:80/latest-signals` via `SignalPollingClient`. Old RabbitMQ `trading-signals` queue consumption was removed.
- **Signals are executed once.** The web_server store keeps the latest signal per indice until a newer one replaces it, so the trader dedups on `signal_uuid` (`select_signals_to_dispatch` in `src/trader/__init__.py`).
- **Position Monitor** uses Saxo WebSocket streaming (replaces old 7s REST polling). Still consumes `trading-ops` for `daily_stats` and fallback `check_positions_on_saxo_api`.
- **Stream reader and position checks are decoupled.** `SaxoStreamClient` only merges deltas and flags a change; a separate task calls the callback with the latest snapshot (updates during a slow callback are coalesced). Don't await slow work in the receive loop.
- **Closures are recorded in the background** (`_record_closed_position_in_background`); `_closing_position_ids` keeps a position from being sold twice meanwhile. DB↔Saxo reconciliation (`StreamPositionHandler`) runs when a position leaves the stream and on a timer, not per tick.
- **Broker-side protective stop** (`position_management.protective_stop`) is opt-in and untested against Saxo; closes cancel a position's `RelatedOpenOrders` before selling.
- **Scheduler** sends `close-position` via HTTP POST to `web_server:80/internal/signal` (Bearer token), and `daily_stats` via RabbitMQ `trading-ops`.
- **Two auth schemes**: webhook uses `?token=...` query param; internal endpoints use HTTP Bearer token.
- **An access check reads the path from `request.scope["path"]`, not from `request.url`**: the URL is rebuilt from the `Host` header, which the caller writes. The web server's IP allowlist was skipped that way (`check_ip`, tested in `tests/test_web_server_advanced.py`).
- **Every line of `requirements.txt` has a version.** Dependabot does not check a line without one, and pip then picks whatever the other pins allow (an old `typing_extensions` pin kept FastAPI and Starlette on versions with known advisories). The file is not a full lock: packages it does not list (`h2`, `cffi`, `aiormq`…) are still chosen at build time.
- **`uvloop.install()`** at startup of trader and position_monitor.
- **Schema** defined as plain dicts in `src/schema/__init__.py` (not pydantic), validated with `jsonschema`.
- **Admin is opt-in** (compose profile `admin`). It reads PostgreSQL through a read-only pool, reaches the other services through what they expose (`/latest-signals`, `/internal/signal`, watchlist endpoints, RabbitMQ, the Saxo token files) and has no Docker socket. Its only DB write is the pause of new entries (`trade_risk_state`, separate connection). All its outside access goes through `AdminContext` (`src/admin/context.py`), which tests replace with fakes.
- **What the trading services do for the admin** (all additive, none may block or fail a trade): the trader checks `TradingRule.check_entries_paused()` before opening and journals each signal's outcome in the background (`SignalJournal`, table `signal_journal`); the position monitor publishes `AsyncPerformanceMonitor.live_values()` to `position_live_value` on a timer and handles the `close_position` ops message (ignored when older than 60 s); both record the account balance with the daily report (`account_balance_history`).
- **Admin config edits only touch `trade.rules`, `trade.config`, `reporting`, `logging.level`** (`src/admin/config_editor.py`); everything else is merged back from disk, on save and on backup restore alike (both go through `merge_editable`). `PROTECTED_PATHS` lists the infrastructure keys inside those sections (service URLs, listen address): a new key there that says where a service connects must be added to it. Services read the config at start, so a saved change applies at each service's next start.
- **Price recorder is opt-in** (compose profile `recorder` + `trade.config.price_recorder.enabled`) and outside the trading path: it reuses `AsyncInstrumentService.build_watchlist_candidates` and `SaxoStreamClient` unchanged, validates its own config section (`src/price_recorder/settings.py`, deliberately not in `ConfigurationManager`: a mistake there must not stop the trader), and creates its own tables (`PRICE_HISTORY_SCHEMA_SQL`, not part of `init_schema`). `BarBuilder` (`bars.py`) is pure and time-driven; the stream callback must stay memory-only. Lost bars cannot be re-fetched from Saxo, so a change that can drop them needs a test. Untested against Saxo.
- **Strategies (`src/strategy/`) are outside the trading path**: no trading service imports the package, and it has no service of its own yet (TradingView still sends the signals). A strategy only sees ten-second bars of the index in order (`IndexBar`) and answers with the position it wants; `engine.run` feeds it a trading day at a time, each day rebuilt from midnight of the trading timezone, and lets one change of target at most out of a bar. Keep it pure (no clock, no I/O): the same code is meant to run live later, reading the recorded bars.
- **The backtest repeats WATA's rules for a clock that is not the present** (`src/strategy/execution.py`): the trader's checks on an entry (`handle_trading_signal`, `TradingRule`), the position monitor's exits (`AsyncPerformanceMonitor._evaluate_positions`) and the day's result (`_calculate_final_percentage`). A change to one of those must be made there too, with its test (`tests/test_strategy_execution.py`), or backtests describe a WATA that no longer exists. The turbo is a model (`TurboModel`: value = distance to the knock-out level), its leverage and spread are assumptions until calibrated on real trades, which is why `python -m src.strategy.backtest` runs once per `--points-per-unit`.
- **Backtest history comes from Dukascopy** (`src/strategy/history/dukascopy.py`): an undocumented public address that refuses many requests and stops answering for a while when asked too much (never run two downloads at once), another broker's prices than the ones traded. Hours are kept as fetched under `var/lib/price_history/` and turned into ten-second bars by the recorder's own `BarBuilder`, in the columns of `wataprices`, so recorded and fetched prices are read by the same code (`src/strategy/sources.py`). A day with a missing hour is never written. Tests never reach the network (the fetch is a function they replace).
- **Backtests asked for from the admin are child processes and folders of files** (`src/admin/backtests.py`, reached as `AdminContext.backtests`): the admin runs `python -m src.strategy.backtest` (and the downloader) itself, one run and one download at a time, at the lowest priority, and reads what the command wrote under `admin.persistant.backtests_path`. Nothing goes to the database. A run id is made by the service and matched against `RUN_ID` before any path is built: keep it that way for every new route. They change nothing live, so they are not operator actions (no audit entry, no Telegram), and have their own switch (`admin.backtests_enabled`). Tests give the service a fake launcher (`tests/test_admin_backtests.py`); one of them goes through a real child process.
- **A backtest is never read alone** (`src/strategy/baseline.py`, `report.py`): the same days are traded two hundred times with entries at random under the same rules, and the summary says the share of them the run beat (`against_random`), how much it rests on (`sample`) and the range of its total with the days drawn again (`total_range`). The draws are seeded per repetition, so two runs are compared against the same random entries: keep them reproducible. The baseline passes over the bars in which a simulator has nothing open and nothing waiting (`Simulator.busy`); `tests/test_strategy_baseline.py` checks that this changes nothing.
- **A signal is an order, a strategy's decision is a position wanted** (`Simulator(entries_are_orders=True)`, `src/strategy/signals.py`): replaying the signals WATA received, an entry the rules refuse closes nothing, as in `handle_trading_signal`; a strategy going from long to short ends the long whatever becomes of the short. The replay exists to test the model against real trades (`src/strategy/comparison.py`): a change to the trader's order of checks must be made in the simulator too.
- **A rule added to `WataRules` needs a default that changes nothing** (`min_confidence = 0.0`): a run keeps the rules it was made with as a file, and `from_dict` reads the runs made before the rule existed. In the same way, a figure added to a run's summary is absent from older runs: the pages take it as optional (`BacktestFigures`), and the service reads a missing CSV column as empty.
- **A sweep is one pass over the prices** (`src/strategy/sweep.py`): one strategy per distinct set of parameters (`engine.Feed`), one simulator per combination, each given only the bars in which it has something open or waiting. Its figures are split at the first day held out, a trade counting on the day it was entered. A combination must give what a backtest of its own gives: `tests/test_strategy_sweep.py` compares the two, keep that test whatever is optimised. No random entries and no money there, on purpose.
- **What WATA would have put on a trade is repeated too** (`WataRules.position_scale`, `report.sized`): the trader's sizing by time of day and by confidence, and the whole turbos an order buys (`_calculate_position_scale`, `_calculate_bid_amount` in `src/trade/async_services.py`). A change there must be made here, or the money a backtest gives is another WATA's. The percentages do not use it: they count each trade as the whole stake, as the day's result does.
- **A run keeps what it was made with** (`src/strategy/fingerprint.py`): a digest of `src/strategy/` and of the bar builders, and one of the files of prices it read. The admin compares them with today's and marks the run stale. For that, a day of prices is written without the time in its gzip header (`write_day_file`): the same bars give the same bytes. A file that decides a backtest's result and is outside those paths must be added to `_CODE`.
- **The admin's routes read the database, the backtest service never does**: for a replay of real signals and for the measure of the turbo, the route reads the journal and the closed positions (`AdminQueries.journal_between`, `closed_positions`) and hands the rows over; the service writes them as files and starts a command (`src.strategy.backtest --signals`, `src.strategy.calibration`), like any run. The turbo measured is kept under `_calibration/` of the backtests folder and becomes the default of new runs (`default_turbo`).
- **Prices kept up to date** (`BacktestService.keep_prices`, a task of the admin application): off until the operator switches it on from the page, which is kept as a file with the downloads' own state (`_fetch/keep.json`, `keeper.json`). It only starts a download while the market is closed by the live rules, asks for the newest missing days a few at a time, doubles its wait each time the server leaves hours unanswered, and settles a day the server has no more of so that it is never asked for again. It must never hurry: Dukascopy shuts an address out. Its downloads are announced with `automatic: true`, which the page does not turn into a notice.
- **Breakdowns of a backtest's trades use the live Exits page's figures** (`stats_by`, `given_back` in `src/admin/analysis.py`): a figure added to one shows in the other. A trade's path (`BacktestService.trade_path`) is replayed from the prices when asked, with the levels of the rules the run was made with, and put together beyond 1,500 points.
- **The admin may write to seven places, listed in a test** (`test_admin_writes_nowhere_else`, `tests/test_deploy_compose.py`): a new writable mount is a decision, not a side effect. Its container is the only one with a processor cap, because of the backtests.
- **The dashboard's "Next entry" tile repeats the trader's entry rules** (`admin_ui/src/features/dashboard/entryState.ts`: pause, closed date, trading and risky hours, day target and stop, trade limit, cooldown) on what `/api/overview` returns. A change to those rules in `src/trade/rules.py` must be made there too, with its test (`entryState.test.ts`), or the tile says "Open" for a signal the trader refuses.
- **Admin health summary** (`src/admin/health.py`) has a `saxo` entry that is not a service: it is "down" when the refresh token is missing or expired, which turns the top bar's summary and the "Saxo: authorise" chip on every page.
- **Admin dialogs** render in `document.body` (`components/ui/settle-modal.tsx`) with focus moved in and trapped; a component test that opens one needs a data router only if it uses `LeaveGuard` (`useBlocker`).
- **Admin live events** (`/api/events`, SSE): uvicorn does not tell the app that a browser left and writing to it does not fail, so the stream asks `request.is_disconnected()` itself. Keep `SecurityHeadersMiddleware` a plain ASGI middleware (`@app.middleware("http")` hides the disconnect from a streamed response) and keep `timeout_graceful_shutdown` on `uvicorn.run`, or an open page keeps the container from stopping. `tests/test_admin_api.py` checks both on a real socket (`RunningAdmin`), which the in-memory `TestClient` cannot show.
- **Admin client address** (`client_ip` in `src/admin/auth.py`): `X-Forwarded-For` is believed only from `admin.trusted_proxy_hosts` (default `["traefik"]`); other containers can reach `admin1:8082` directly and are known by their own address.
- **The admin has two ways in through Traefik** (`deploy/docker-compose.yml`): its own domain on `websecure`, or the API domain on the entrypoint `adminsecure` (8443), chosen by `ADMIN_ENTRYPOINT`. That port is published on `127.0.0.1` unless `ADMIN_PORT` says otherwise, and over IPv4 only when it is opened (`0.0.0.0:8443`): over IPv6, Docker's proxy would show Traefik its own address and not the caller's, which the allowlist needs. The admin route has `priority=1`, so that the webhook keeps its route if both are given one domain on one port; every route must name its entrypoint, or it is served on 8443 too (`tests/test_deploy_compose.py`). Seen working on a server from an iPhone over WireGuard; the device configuration of the README is the one that connected (the first one written, with the server's IPv4 address as endpoint and no keepalive, never sent a packet).
- **The admin's Prices page reads the recorder's tables, nothing else** (`src/admin/routers/prices.py`, `AdminQueries.price_*`, pure gap logic in `src/admin/prices.py`). Bars above ten seconds are aggregated in SQL from a whitelist of intervals (`INTERVALS`); a request is refused beyond `MAX_BUCKETS`. Routers must not import `src.admin.queries` at module level (it needs asyncpg, which the tests do not have).
- **Range bars of the Prices page are built in Python, not in SQL** (`range_bars` in `src/admin/prices.py`, pure): from the ten-second bars of one side, read a page at a time (`AdminQueries.price_series`), counted in whole ticks so that a complete bar is exactly its range. Each bar has two kinds of time: `t`, unique, is only its place on the chart's axis (the library refuses two points at one time); `began` / `ended` are what is shown and exported. How a bar grows and ends is `RangeBarBuilder` (`src/price_recorder/range_bars.py`), which the strategies use too, one ten-second bar at a time: a change there changes the Prices page and every backtest. Its rules (a bar ends when the price leaves its range, no bar made up across a pause of more than 60 s, low before high inside ten seconds that rose) are stated in its docstring and in the README: change both together. On the page, both kinds of bars become one `ChartModel` (`admin_ui/src/features/prices/chartModel.ts`); the chart, the markers and the legend only know that.
- **Lightweight Charts is confined to `admin_ui/src/features/prices/PriceChart.tsx`.** It only knows UTC: `chartData.ts` shifts times to the trading timezone. Its licence requires the notice and link on every page that shows a chart: `ChartAttribution` goes wherever `PriceChart` goes (the Prices page, a day of a backtest). One fault of 5.2.1 is worked around there, for the index line and for the lines drawn over the prices alike (see `admin_ui/README.md`). Tests use `admin_ui/src/test/fakeCharts.ts`.
- **The backtest pages** (`admin_ui/src/features/backtests/`) keep what can be wrong without a browser in plain functions with tests: `model.ts` (what a run changed, the curve, the form's checks, every sentence that says how far a run can be trusted, a sweep's grid, tints and verdict, the levels a trade's path can show) and `dayChart.ts` (a day as candles, lines and marks, and the stretch of it a trade is brought into view with). Runs compared on one chart take the `--chart-compare-*` colours of `styles/theme.css`, four at most: every pair was validated for colour-blind readers in both themes, and a fifth has no colour that passes.
- **The admin interface has two themes** switched by `dark` on `<html>`: `public/theme.js` sets it before the first paint (a file because the CSP allows no inline script) and `src/lib/theme.ts` keeps it while the application runs; the two hold the same rule. A class that names a colour needs both values (`light dark:dark`) or a token that has both; status colours as text must stay at 4.5:1 on every surface of their theme (`styles/theme.css`). Canvas charts read their colours once: `PriceChart` is keyed by the theme.
- **`admin_ui/src/components/ui/` is copied from EasyUI** (shadcn-style, MIT). Add components with `npx shadcn@latest add Surajmaurya1/easyui/<slug>`; local changes are listed in `admin_ui/README.md`.

## Database migration

`src/database/migration.py` — one-time DuckDB→PostgreSQL migration tool. Idempotent. Run via `docker compose run --rm trader1 python -m src.database.migration`.

## Testing & verification

- **Run all tests**: `PYTHONPATH=./:src/ pytest --cov=src --cov-report=term-missing --cov-report=html -vv tests/`
- **Single test file**: `PYTHONPATH=./:src/ pytest tests/test_web_server.py -vv`
- **Required env**: `WATA_CONFIG_PATH=tests/test_config.json` (tests set this internally, but must exist)
- **Test config** at `tests/test_config.json` — minimal rule set; omit `day_trading` rule if testing with `trading_mode != "day_trading"`

## CI / Deploy

- **CI**: GitHub Actions (`.github/workflows/ci.yml`) — runs `tests.sh` on push/PR to main
- **Build package**: `./package.sh` → creates `wata_app_v<VERSION>.zip`
- **Deploy**: `./deploy/tools/deploy_app_to_your_server.sh` (uses Ansible playbook `deploy_app.yml`)
