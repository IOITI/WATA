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
- **`uvloop.install()`** at startup of trader and position_monitor.
- **Schema** defined as plain dicts in `src/schema/__init__.py` (not pydantic), validated with `jsonschema`.
- **Admin is opt-in** (compose profile `admin`). It reads PostgreSQL through a read-only pool, reaches the other services through what they expose (`/latest-signals`, `/internal/signal`, watchlist endpoints, RabbitMQ, the Saxo token files) and has no Docker socket. Its only DB write is the pause of new entries (`trade_risk_state`, separate connection). All its outside access goes through `AdminContext` (`src/admin/context.py`), which tests replace with fakes.
- **What the trading services do for the admin** (all additive, none may block or fail a trade): the trader checks `TradingRule.check_entries_paused()` before opening and journals each signal's outcome in the background (`SignalJournal`, table `signal_journal`); the position monitor publishes `AsyncPerformanceMonitor.live_values()` to `position_live_value` on a timer and handles the `close_position` ops message (ignored when older than 60 s); both record the account balance with the daily report (`account_balance_history`).
- **Admin config edits only touch `trade.rules`, `trade.config`, `reporting`, `logging.level`** (`src/admin/config_editor.py`); everything else is merged back from disk, on save and on backup restore alike (both go through `merge_editable`). `PROTECTED_PATHS` lists the infrastructure keys inside those sections (service URLs, listen address): a new key there that says where a service connects must be added to it. Services read the config at start, so a saved change applies at each service's next start.
- **Price recorder is opt-in** (compose profile `recorder` + `trade.config.price_recorder.enabled`) and outside the trading path: it reuses `AsyncInstrumentService.build_watchlist_candidates` and `SaxoStreamClient` unchanged, validates its own config section (`src/price_recorder/settings.py`, deliberately not in `ConfigurationManager`: a mistake there must not stop the trader), and creates its own tables (`PRICE_HISTORY_SCHEMA_SQL`, not part of `init_schema`). `BarBuilder` (`bars.py`) is pure and time-driven; the stream callback must stay memory-only. Lost bars cannot be re-fetched from Saxo, so a change that can drop them needs a test. Untested against Saxo.
- **The dashboard's "Next entry" tile repeats the trader's entry rules** (`admin_ui/src/features/dashboard/entryState.ts`: pause, closed date, trading and risky hours, day target and stop, trade limit, cooldown) on what `/api/overview` returns. A change to those rules in `src/trade/rules.py` must be made there too, with its test (`entryState.test.ts`), or the tile says "Open" for a signal the trader refuses.
- **Admin health summary** (`src/admin/health.py`) has a `saxo` entry that is not a service: it is "down" when the refresh token is missing or expired, which turns the top bar's summary and the "Saxo: authorise" chip on every page.
- **Admin dialogs** render in `document.body` (`components/ui/settle-modal.tsx`) with focus moved in and trapped; a component test that opens one needs a data router only if it uses `LeaveGuard` (`useBlocker`).
- **Admin live events** (`/api/events`, SSE): uvicorn does not tell the app that a browser left and writing to it does not fail, so the stream asks `request.is_disconnected()` itself. Keep `SecurityHeadersMiddleware` a plain ASGI middleware (`@app.middleware("http")` hides the disconnect from a streamed response) and keep `timeout_graceful_shutdown` on `uvicorn.run`, or an open page keeps the container from stopping. `tests/test_admin_api.py` checks both on a real socket (`RunningAdmin`), which the in-memory `TestClient` cannot show.
- **Admin client address** (`client_ip` in `src/admin/auth.py`): `X-Forwarded-For` is believed only from `admin.trusted_proxy_hosts` (default `["traefik"]`); other containers can reach `admin1:8082` directly and are known by their own address.
- **The admin's Prices page reads the recorder's tables, nothing else** (`src/admin/routers/prices.py`, `AdminQueries.price_*`, pure gap logic in `src/admin/prices.py`). Bars above ten seconds are aggregated in SQL from a whitelist of intervals (`INTERVALS`); a request is refused beyond `MAX_BUCKETS`. Routers must not import `src.admin.queries` at module level (it needs asyncpg, which the tests do not have).
- **Range bars of the Prices page are built in Python, not in SQL** (`range_bars` in `src/admin/prices.py`, pure): from the ten-second bars of one side, read a page at a time (`AdminQueries.price_series`), counted in whole ticks so that a complete bar is exactly its range. Each bar has two kinds of time: `t`, unique, is only its place on the chart's axis (the library refuses two points at one time); `began` / `ended` are what is shown and exported. The rules (a bar ends when the price leaves its range, no bar made up across a pause of more than 60 s, low before high inside ten seconds that rose) are stated in the docstring and in the README: change both together. On the page, both kinds of bars become one `ChartModel` (`admin_ui/src/features/prices/chartModel.ts`); the chart, the markers and the legend only know that.
- **Lightweight Charts is confined to `admin_ui/src/features/prices/PriceChart.tsx`.** It only knows UTC: `chartData.ts` shifts times to the trading timezone. Its licence requires the notice and link on the page; one fault of 5.2.1 is worked around there (see `admin_ui/README.md`). Tests use `admin_ui/src/test/fakeCharts.ts`.
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
