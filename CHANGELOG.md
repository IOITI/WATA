# Changelog

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

## Step 1: Update Configuration

Add the following sections to your `etc/config.json`:

### 1a. PostgreSQL connection

Add at the top level (next to `duckdb`):

```json
"postgresql": {
  "dsn": "postgresql://wata:YOUR_PASSWORD@postgres1:5432/wata",
  "pool_min_size": 2,
  "pool_max_size": 10
}
```

> **Important:** Replace `YOUR_PASSWORD` with a strong password. Set the same password as the `POSTGRES_PASSWORD` environment variable in `docker-compose.yml`.

### 1b. Streaming configuration

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

### 1c. Keep existing sections

The `duckdb`, `websocket`, and `position_check` config sections can remain - they are not used by the new services but won't cause errors.

## Step 2: Set PostgreSQL Password

In `deploy/docker-compose.yml`, set a secure postgres password:

```bash
export POSTGRES_PASSWORD="your_secure_password_here"
```

Or edit the `docker-compose.yml` directly (the `postgres1` service `POSTGRES_PASSWORD` environment variable).

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
