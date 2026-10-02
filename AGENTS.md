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

Each service runs as a Docker container; `WATA_APP_ROLE` env var selects which `__init__.py` runs via `src/start_python_script.sh`.

## Infrastructure

- **RabbitMQ** — queues: `trading-signals` (trade actions), `trading-ops` (ops/daily_stats), `telegram` (notifications)
- **PostgreSQL 16** — replaces DuckDB (migrated in v0.7.0). DuckDB config is dead.
- **Traefik** — reverse proxy, Let's Encrypt, TradingView IP allowlist
- **Config** — JSON at `WATA_CONFIG_PATH` path, validated strictly by `ConfigurationManager`

## Commands

```bash
# Run all tests
PYTHONPATH=./:src/ pytest --cov=src --cov-report=term-missing --cov-report=html -vv tests/

# Run a single test file
PYTHONPATH=./:src/ pytest tests/test_web_server.py -vv

# Build deployable zip
./package.sh          # creates wata_app_v<VERSION>.zip

# Deploy (via Ansible)
./deploy/tools/deploy_app_to_your_server.sh
```

## Testing quirks

- **`WATA_CONFIG_PATH` must be set** before importing `src.web_server` (tests set `os.environ["WATA_CONFIG_PATH"] = "tests/test_config.json"`)
- Test config at `tests/test_config.json` — omit `day_trading` rule if testing with `trading_mode != "day_trading"`
- `tests/test_web_server.py` patches `verify_token` and `Request.client` for IP filtering tests
- `tests/test_config.json` uses minimal rule set; adding more rules requires updating config

## Architecture notes

- **Trader does NOT consume RabbitMQ** for trade signals — it HTTP-polls `web_server:80/latest-signals` via `SignalPollingClient`. Old RabbitMQ `trading-signals` queue consumption was removed.
- **Position Monitor** uses Saxo WebSocket streaming (replaces old 7s REST polling). Still consumes `trading-ops` for `daily_stats` and fallback `check_positions_on_saxo_api`.
- **Scheduler** sends `close-position` via HTTP POST to `web_server:80/internal/signal` (Bearer token), and `daily_stats` via RabbitMQ `trading-ops`.
- **Two auth schemes**: webhook uses `?token=...` query param; internal endpoints use HTTP Bearer token.
- **`uvloop.install()`** at startup of trader and position_monitor.
- **Schema** defined as plain dicts in `src/schema/__init__.py` (not pydantic), validated with `jsonschema`.

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
