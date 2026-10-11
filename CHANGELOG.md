# Changelog

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
