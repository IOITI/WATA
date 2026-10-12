<div align="center">
    <a href="https://ioiti.github.io/wata-docs/">
        <img src="docs/images/wata_animated_banner.gif" alt="WATA Logo"/>
    </a>
    <h1 style="margin-left: 20px; margin-top: 0; margin-bottom: 2;">WATA - Warrants Automated Trading Assistant</h1>
    <p>Automated trading system for Saxo Bank's Knock-out warrants (Turbos), executing trades via webhook signals.</p>
</div>


![GitHub repo size](https://img.shields.io/github/repo-size/IOITI/wata?style=flat-square)
![GitHub contributors](https://img.shields.io/github/contributors/IOITI/wata?style=flat-square)
![GitHub issues](https://img.shields.io/github/issues/IOITI/wata?style=flat-square)
![GitHub license](https://img.shields.io/github/license/IOITI/wata?style=flat-square)
![GitHub last commit](https://img.shields.io/github/last-commit/IOITI/wata?style=flat-square)
![GitHub Release Date](https://img.shields.io/github/release-date/IOITI/wata?style=flat-square)
![GitHub Stars](https://img.shields.io/github/stars/IOITI/wata?style=social) 
![GitHub Forks](https://img.shields.io/github/forks/IOITI/wata?style=social)

<div align="center">
  <H2><a href="https://ioiti.github.io/wata-docs/docs/intro">📚Documentation Website</a></H2>
</div>

---

> [!WARNING]
> 
> **This is a personal learning project and not production-ready software.**
> 
>  Do not risk money which you are afraid to lose. USE THE SOFTWARE AT YOUR OWN RISK. THE AUTHORS AND ALL AFFILIATES ASSUME NO RESPONSIBILITY FOR YOUR TRADING RESULTS.
>
> Always start by running WATA with small money amounts to test the system and its performance. Use a secondary Saxo account if possible. Understand the risks involved in trading before you start, and do not engage money before you understand how it works and what profit/loss you should expect.
>
> **Risk Warning: WATA CAN LOSE ALL YOUR MONEY due to:**
> - Insufficient code testing
> - Limited security measures on your server
> - Lack of fail-safe mechanisms
> - No comprehensive monitoring included
> - Absence of fail-over systems
> - Limited user experience
> - Change of Saxo Bank API
> - Because trading with leverage is very risky
> 
> *This software is provided "as is" without warranty. The authors accept no liability for any damages arising from its use.*

## 🎯 Purpose

WATA (Warrants Automated Trading Assistant) is an algorithmic trading system compagnon, designed for automated execution of Knock-out warrants (Turbos) on Saxo Bank. It serves as a reliable bridge between trading signals and actual market execution, offering several key benefits:

- **Automated Execution**: Eliminates emotional bias and human error by executing trades based on predefined rules and signals (from TradingView, for example)
- **Risk Management**: Implements systematic position monitoring with stop-loss and take-profit mechanisms
- **Cached Instrument Selection**: Maintains a background watchlist of candidate turbos for configured indices and directions
- **Performance Tracking**: Provides comprehensive analytics and reporting for trade analysis
- **Real-time Monitoring**: Uses Saxo WebSocket updates for open positions and delivers notifications via Telegram
- **Scalability**: Uses asynchronous services, PostgreSQL, and a microservice architecture

The system is particularly suited for traders who:
- Want to automate their trading strategies
- Need reliable execution of trading signals
- Require comprehensive trade tracking and analysis
- Value real-time monitoring and alerts
- Prefer systematic, rule-based trading over discretionary decisions

## 🏗️ Architecture

WATA runs as a set of Docker Compose services:

| Component (role) | Purpose |
|------------------|---------|
| **Traefik** | Routes public webhook traffic over HTTPS, obtains TLS certificates, and applies the configured IP allowlist |
| **Web Server** (`web_server`) | Validates webhook signals and keeps the newest signal per `indice` in an in-memory store |
| **Trader** (`trader`) | Polls the Web Server for signals, applies trading rules, calls Saxo asynchronously, and persists trading data |
| **Watchlist Manager** (`watchlist_manager`) | Builds and refreshes cached best-turbo candidates for configured indices and directions |
| **Position Monitor** (`position_monitor`) | Monitors live Saxo position updates and handles stop-loss, take-profit, trailing-stop, and operational events |
| **Scheduler** (`scheduler`) | Sends scheduled operations to `trading-ops` and scheduled close signals to the Web Server |
| **PostgreSQL** | Primary runtime database for orders, positions, and trade performance |
| **RabbitMQ** | Carries `trading-ops` messages and Telegram notifications; the Trader no longer consumes trade signals from a RabbitMQ queue |
| **Telegram** (`telegram`) | Delivers trade and service notifications |
| **Admin** (`admin`, optional) | Web interface for monitoring, performance reporting, operator actions and editing the trading configuration. Off unless enabled, see [Admin Interface](#️-admin-interface) |
| **Price Recorder** (`price_recorder`, optional) | Keeps ten-second bid and ask bars of the turbos the Trader could buy and of their index, for backtesting. Off unless enabled, see [Price Recorder](#️-price-recorder) |

```mermaid
flowchart TD
    classDef external fill:#f9f9f9,stroke:#aaa,stroke-width:1px,color:#000
    classDef service fill:#e1f5fe,stroke:#03a9f4,stroke-width:1px,color:#000
    classDef execution fill:#e8f5e9,stroke:#4caf50,stroke-width:1px,color:#000
    classDef queue fill:#fff8e1,stroke:#ffc107,stroke-width:1px,color:#000
    classDef database fill:#f3e5f5,stroke:#9c27b0,stroke-width:2px,stroke-dasharray:5 5,color:#000

    subgraph ExtSys["External Systems"]
        direction LR
        TV["TradingView"]
        SaxoREST["Saxo Bank REST API"]
        SaxoWS["Saxo Bank WebSocket"]
        TG["Telegram"]
    end

    subgraph CoreSys["WATA Services"]
        direction TB

        Proxy["Traefik<br>HTTPS ingress"]:::service
        WS["Web Server<br>latest-signal store"]:::service
        TR["Async Trader<br>polls /latest-signals"]:::execution
        WM["Watchlist Manager<br>best-turbo cache"]:::service
        PM["Position Monitor"]:::execution
        SC["Scheduler"]:::service
        RMQ["RabbitMQ<br>trading-ops + telegram_channel"]:::queue
        DB[("PostgreSQL 16")]:::database
    end

    TV -->|HTTPS webhook| Proxy --> WS
    TR -->|GET /latest-signals long-poll| WS
    WS -->|newest signal per indice| TR
    TR -->|instrument search and orders| SaxoREST
    TR -->|read and write trading data| DB
    TR -->|cached candidate lookup| WM
    WM -->|candidate search| SaxoREST
    SaxoWS -->|live quote updates| WM
    SaxoWS -->|position updates| PM
    PM -->|position and performance data| DB
    SC -->|scheduled operational messages| RMQ
    RMQ -->|trading-ops| PM
    SC -->|scheduled close signal| WS
    TR -->|notifications| RMQ
    PM -->|notifications| RMQ
    RMQ -->|telegram_channel| TG

    class TV,SaxoREST,SaxoWS,TG external
    class Proxy,WS,WM,SC service
    class TR,PM execution
```

## 📊 Trading Workflow

1. **Signal Reception**
   - Receive webhook traffic through Traefik HTTPS and validate the token and payload schema
   - Keep the newest signal for each `indice` in the Web Server's in-memory store
   - The Trader long-polls `/latest-signals`, so a signal reaches it as soon as it is stored; older signals for an `indice` are ignored
   - Each signal is executed once: the store keeps serving it until a newer one replaces it, and the Trader skips any `signal_uuid` it has already handled
   - After a Trader restart, signals received more than `startup_replay_max_age_seconds` ago (10 s by default) are not replayed

2. **Rule Validation**
   - Verify market hours and timestamp freshness
   - Check allowed indices, duplicate positions, and configured risk limits
   - Use the Watchlist Manager's cached candidates when available and validate instrument eligibility

3. **Trade Execution**
   - Use asynchronous Saxo API calls for instrument search, order calculation, placement, and confirmation
   - Persist orders, positions, and performance data in PostgreSQL

4. **Real-Time Position Monitoring**
   - The Position Monitor consumes Saxo WebSocket updates instead of relying on the old 7-second REST polling loop
   - Evaluate stop-loss, take-profit, and trailing-stop rules; receive scheduled operational events through `trading-ops`
   - Each price update is checked on its own task, always on the latest prices; a slow check or a closure in progress never delays reading the stream
   - Stop-loss checks keep working from the last known state if PostgreSQL is slow or unreachable
   - The database is reconciled with Saxo when a position leaves the stream and every `reconcile_interval_seconds` (30 s by default), not on every update
   - Optionally, a wider sell stop is left at the broker for each new position (`protective_stop`, off by default) so that a Position Monitor outage is survivable

5. **Reporting and Notifications**
   - Generate daily statistics and trade reports, including closed-trade and daily-profit history
   - Send trade and service notifications through the Telegram service

![telegram_notifcation.png](docs/images/telegram_notifcation.png)

## 💾 Database System

PostgreSQL 16 is the primary runtime database. The application uses an `asyncpg` connection pool and creates its schema on startup.

- **Order tracking**: Orders and execution details
- **Position management**: Open and closed positions, including profit/loss data
- **Performance analytics**: Trade performance and daily statistics
- **DuckDB migration**: `src/database/migration.py` imports existing DuckDB data into PostgreSQL. DuckDB is no longer used by the live trading services.

## 🔐 Authentication and Security

### Saxo Bank API Authentication

WATA uses OAuth 2.0 for Saxo Bank API integration:

1. When authenticating, the application send you a URL via Telegram (or logs of container)
2. Open this URL in your browser and log in with your Saxo credentials, and do all the steps to authorize the application (2FA, risk warning, etc.)
3. After authorization, you'll be redirected to a page with a URL containing a `code` parameter
4. Copy the code and run `watasaxoauth` on your server (you will be prompted to enter the code securely)
5. The application completes authentication using this code

**Command Reference**:
- `watasaxoauth`: Run this command and you'll be prompted to enter the authorization code securely (the code won't be visible when typing)
- The authorization code is valid only for a short time (typically a few minutes)

**Troubleshooting**:
- If you receive an invalid/expired code error, repeat the process
- A "Timeout waiting for authorization code" error means the application waited for 5 minutes without receiving the code

Please refer to the [dedicated docs pages for Saxo Authentication](https://ioiti.github.io/wata-docs/docs/saxo-authentication) for more details.

### Webhook Security

WATA uses token-based authentication to secure webhook endpoints:

1. Each webhook request must include a valid token in the URL (e.g., `/webhook?token=YOUR_TOKEN`)
2. This token is securely generated and stored with strong encryption
3. Manage your webhook token with the `watawebtoken` command

**Command Reference**:
- `watawebtoken`: View your current webhook token
- `watawebtoken --new`: Generate a new webhook token
- The command provides instructions on how to use the token in your webhook URL

**Security Features**:
- All tokens are encrypted before storage
- File permissions are restricted to the owner only
- The deployed Traefik route applies an IP allowlist for TradingView webhook traffic

In the Docker Compose deployment, Traefik redirects HTTP to HTTPS and obtains certificates through Let's Encrypt. Configure `API_DOMAIN` and `ACME_EMAIL` in `deploy/.env`, point the domain at the server, and allow inbound ports 80 and 443. HTTPS and the IP allowlist supplement the webhook token; they do not replace it.

## 📖 Detailed How-To Guide

For a comprehensive, step-by-step guide on setting up and using WATA, including:
- Detailed prerequisites and estimated costs
- Setting up Saxo Bank integration
- Configuring Telegram notifications
- Deploying and configuring WATA
- Setting up TradingView webhooks
- Starting and monitoring your trading

Please refer to our detailed [HOW-TO Guide](https://ioiti.github.io/wata-docs/docs/how-to).

## 🚀 Setup & Deployment

### Prerequisites

   - Saxo Bank account with API access
   - Dedicated Ubuntu server (on VPS or local machine)
   - Docker and Docker Compose installed
   - Python 3.12+
   - Ansible (for automated deployment)
   - Telegram bot for notifications
   - _(Optional) TradingView account for webhook signals_

### Detailed Setup

For detailed setup instructions, refer to our [HOW-TO Guide](https://ioiti.github.io/wata-docs/docs/how-to).

### Quick Start

1. **Configure Inventory**
   - Copy the example Ansible inventory file:
     ```bash
     cp deploy/tools/ansible/inventory/inventory_example.ini deploy/tools/ansible/inventory/inventory.ini
     ```
   - Edit `inventory.ini` with your server details

2. **Build Application**
   - Build the package:
     ```bash
     ./package.sh
     ```

3. **Deploy Application**
   - Run the deployment script:
     ```bash
     cd deploy/tools
     ./deploy_app_to_your_server.sh
     ```
   - The script will use Ansible to deploy the application to your server

4. **Configure Application**
   - On the server, set up your credentials in `etc/config.json` (see below in Configuration section)

5. **Manage Application**
   Use the following aliases on your server:
   - `watastart`: Start the application
   - `watastop`: Stop the application
   - `watalogs`: View application logs
   - `watastatus`: Check application status
   - `watasaxoauth`: Launch the secure authorization code submission process
   - `watawebtoken`: View your webhook authentication token
   - `watawebtoken --new`: Generate a new webhook token


### Docker Compose Setup

The current stack is defined in `deploy/docker-compose.yml`; it does not use the old setup service or a separate Compose override. It includes Traefik, PostgreSQL, RabbitMQ, the Web Server, Trader, Scheduler, Telegram, Position Monitor, and Watchlist Manager.

The Compose file uses health checks and service dependencies for PostgreSQL, RabbitMQ, and Traefik. Traefik is the public HTTPS ingress; the other application services communicate over the internal Compose network.

### Configuration

After deployment, you need to set up your configuration:

1. **Configure Environment Variables**
   ```bash
   # Navigate to the deploy directory
   cd /app/deploy
   
   # Copy the example .env file
   cp .env.example .env
   
   # Edit passwords and the public API hostname
   nano .env
   ```
   
   Set `RABBITMQ_PASSWORD`, `ACME_EMAIL`, and `API_DOMAIN`. Also set a strong `POSTGRES_PASSWORD` in `.env`; the Compose file has a fallback if it is omitted, but it should not be used for a real deployment. Point `API_DOMAIN` to the server so Traefik can issue the HTTPS certificate.

2. **Copy the Example Config**
   ```bash
   cp /app/etc/config_example.json /app/etc/config.json
   ```

3. **Update Configuration**
   Edit `/app/etc/config.json` with your specific settings:

   - **Saxo Bank Authentication**
     ```json
     "authentication": {
       "saxo": {
         "app_config_object": {
           "AppName": "your_app_name",
           "AppKey": "your_app_key",
           "AppSecret": "your_app_secret"
         }
       }
     }
     ```

   - **WebServer Security**
     ```json
     "webserver": {
       "persistant": {
         "token_path": "/app/var/lib/web_server/persist_token.json"
       },
       "app_secret": "YOUR_STRONG_SECRET_KEY"
     }
     ```

   - **Telegram Notifications**
     ```json
     "telegram": {
       "bot_token": "your_bot_token",
       "chat_id": "your_chat_id",
       "bot_name": "your_bot_name"
     }
     ```

   - **Trading Rules**
     - `allowed_indices`: Configure allowed trading indices
     - `market_closed_dates`: Set market holidays
     - `day_trading`: Define profit targets and limits

   - **Other Settings**
     - Logging configuration
     - RabbitMQ connection details
     - PostgreSQL DSN and pool sizing (`postgresql`)
     - Saxo streaming refresh, reconnect and reconciliation settings (`trade.config.general.streaming`)
     - Optional broker-side protective stop (`trade.config.position_management.protective_stop`): `percent` must be wider than `stoploss_percent`; verify it in Saxo's simulation environment before enabling it live
     - Cached candidate settings (`trade.config.watchlist_manager`)
     - Signal polling endpoint, long-poll wait (`long_poll_wait_ms`, `0` falls back to polling every `interval_ms`) and startup replay window (`trade.config.signal_polling`)
     - Saxo keep-warm call between signals (`trade.config.general.keep_warm_interval_seconds`, `0` disables it)
     - Optional spending-power cache for order sizing (`trade.config.buying_power.balance_cache_max_age_seconds`, `0` = ask Saxo on every trade)

   The `duckdb` configuration is retained for the one-time migration source and legacy reporting export; it is not the live trading database. See the [v0.6.x migration notes](CHANGELOG.md) for the DuckDB-to-PostgreSQL migration steps.

   For a complete and detailed explanation of all configuration options, please refer to the [CONFIGURATION Guide](https://ioiti.github.io/wata-docs/docs/configuration).

4. **Restart Services**
   ```bash
   watastop
   watastart
   ```

### Usage

For a deployed instance, send TradingView signals to the HTTPS hostname configured by `API_DOMAIN`. The Web Server stores only the newest signal per `indice` in memory; older timestamps for that index are ignored, and pending signals are not persisted across a Web Server restart. The Trader polls this store internally.

Send trading signals to:
```
POST /webhook?token=YOUR_SECRET_TOKEN
```

Payload:
```json
{
  "action": "long",
  "indice": "us100",
  "signal_timestamp": "2023-07-01T12:00:00Z",
  "alert_timestamp": "2023-07-01T12:00:01Z"
}
```

## 🖥️ Admin Interface

An optional web interface for the operator, served on its own domain:

- **Dashboard**: the day's profit and loss, trades against the daily limit, whether the next signal would be traded and what holds it back, open positions with their live value, latest signals and the state of each service. The page is updated as things happen and can raise browser notifications. On every page, the top bar says when new entries are paused, when Saxo needs a new authorisation and when a restart is needed.
- **Positions, orders and performance**: history with CSV export, cumulative and daily profit and loss, win rate, long/short and per-instrument figures, read live from PostgreSQL.
- **Report and exits**: the figures of the daily Telegram report with the account balance over time, and an analysis of how trades ended (by exit, hour of entry, holding time, and how much of the best level was given back).
- **Signals, watchlist and execution timing**: what the Trader did with each signal and why, the turbo that would be bought now, and how long each step of a trade took.
- **Prices**: the bars kept by the [Price Recorder](#️-price-recorder), as a candlestick chart per instrument, from ten-second bars to one-hour ones or as range bars (TradingView's "60R"), with WATA's own entries and exits, the signals received and the index under each turbo. The times that were not recorded are listed, and the bars can be exported to CSV.
- **Operator actions**: pause and resume new entries, close one position, hand over the Saxo authorization code from the browser (instead of `watasaxoauth` over SSH), send a manual `long` / `short` / close signal, request the daily report. Each one asks for an explicit confirmation, is kept in an audit trail and announced on Telegram.
- **Configuration**: view `config.json` with secrets masked and edit the trading parameters (`trade.rules`, `trade.config`, `reporting`, `logging.level`). A change is checked with the same validator the services use, the previous file is kept as `config.json.bak-<date>` and its trading parameters can be restored, and it takes effect the next time each service starts (run `watastop` then `watastart`).
- **Logs**: the end of each service's log file, filtered by level and text.
- **Light and dark**: the interface follows the system setting of the computer or phone, and a button in the top bar (also on the login page) sets it to always light or always dark; the choice is remembered in that browser.

### Enabling it

1. Point a DNS record for the admin domain at the server.
2. Add to `/app/wata/.env`:
   ```
   COMPOSE_PROFILES=admin
   ADMIN_DOMAIN=admin.yourdomain.com
   ADMIN_ALLOWED_IPS=203.0.113.10/32
   ```
   `ADMIN_ALLOWED_IPS` is a comma-separated list of the addresses allowed to reach it: yours only, not TradingView's.
3. `watastart`, then set the password (at least 12 characters): `wataadminpasswd`. Until a password is set, every login is refused.
4. Recommended: `wataadmintotp` prints a key to add to an authenticator app; a one-time code is then required at each login. `wataadmintotp --disable` removes it.

### Security model

- Its own Traefik route with HTTPS and its own IP allowlist, then a password login and, when enabled, a one-time code. Repeated failed logins lock the address out for 15 minutes. Changing the password or the second factor closes open sessions.
- The address used for that lockout and in the audit trail is the one Traefik reports. `admin.trusted_proxy_hosts` (`["traefik"]` by default) names the proxies believed for it; anything else that reaches the admin port is known by its own address.
- The container reads PostgreSQL through connections set to read-only, so that a mistake in a query cannot write anything. This is a safeguard, not a separate database account: it connects as the same user as the trading services. The pause of new entries is the one thing it writes, through a separate connection.
- It mounts only the token directories it needs, the other services' logs read-only, and has no access to Docker: it cannot start, stop or restart anything.
- The configuration editor only ever writes the trading parameters, when saving and when restoring a previous version alike: credentials, infrastructure settings and the `admin` section always stay as they are on the server. Inside the trading sections, the addresses the services connect to and listen on (`trade.config.signal_polling.service_url`, `trade.config.watchlist_manager.service_url`, `api_host` and `api_port`) are refused too.
- Manual signals go through the Web Server like a webhook, so the Trader applies its usual rules to them. A single position is closed by the Position Monitor, on a request that expires after a minute.
- `admin.actions_enabled` and `admin.config_edit_enabled` in `config.json` (both `true` by default) switch the operator actions and the configuration editor off. These two settings cannot be changed from the interface itself.

### Pausing new entries

"Pause new entries" on the dashboard makes the Trader refuse every `long` and `short` signal until entries are resumed. Nothing is closed and nothing is stopped: closing signals, the end-of-day close and the Position Monitor's exits keep working. The pause survives restarts.

### What to keep in mind

- The live value of an open position is what the Position Monitor last saw, published every few seconds (`streaming.live_value_interval_seconds`); the interface shows how old it is.
- The state of the Trader and the Scheduler is read from the start and stop lines of their logs, since they expose nothing to query.
- The signal journal and the account balance curve start with the version that introduced them.

## 🗃️ Price Recorder

Saxo's history goes no finer than one minute. To make a backtest on real prices possible later, WATA can record them itself, every ten seconds, from the day this is switched on. **What is not recorded cannot be fetched afterwards.**

What it records:

- **The turbos the Trader could buy**: the same candidates as its own selection (20 per indice and direction). A turbo stays recorded until the end of the day once it has been a candidate, so the one that was bought has no gap after the index moved on.
- **The index** of each allowed indice.
- **One row per instrument and ten seconds**: open, high, low and close of the bid and of the ask, built from Saxo's one-second stream. A stop-loss touched inside the ten seconds is visible in the low.

It is a separate service with its own Saxo stream and database connections: no trading service uses it, and it cannot slow one down.

### Enabling it

1. In `config.json`, set `trade.config.price_recorder.enabled` to `true`.
2. In `/app/wata/.env`, add `recorder` to the profiles: `COMPOSE_PROFILES=recorder`, or `COMPOSE_PROFILES=admin,recorder` with the admin interface.
3. `watastop`, then `watastart`.
4. Check the index: `wataprices probe --uic 1909050` (the underlying of your indice) says whether Saxo prices it for your account. If it does not, look for one that is, for example `wataprices probe --keywords "US Tech 100"`, and put the entry it prints in `trade.config.price_recorder.instruments`.

When nothing has been recorded for five minutes during trading hours, a Telegram message says so, and another when it resumes. With the admin interface, "Price recorder" is listed with the services.

| Setting (`trade.config.price_recorder`) | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Switches the recording on |
| `instruments` | `[]` | Instruments recorded besides the turbos, as `{"label": "us100", "uic": 4913, "asset_type": "CfdOnIndex"}`. An entry labelled like an indice replaces its index |
| `max_instruments` | `150` | Upper bound of what is recorded at once; beyond it, the turbos seen longest ago are dropped |
| `refresh_interval_seconds` | `300` | How often the candidates are looked for |
| `refresh_rate_ms` | `1000` | Refresh rate asked of Saxo's stream |
| `retention_months` | `0` | `0` keeps everything; otherwise whole months older than this are deleted |
| `alert_after_seconds` | `300` | Silence, during trading hours, after which Telegram is told |

### Reading the data

With the [Admin Interface](#️-admin-interface), the **Prices** page shows what was recorded: choose a period and an instrument, and it draws the bid or the ask as candles of ten seconds to one hour, with the index under a turbo, WATA's entries and exits at their prices, the signals received, and the list of the times in trading hours for which nothing was recorded. A day fits at ten seconds; a longer period needs longer bars (a chart holds at most 12,000 of them).

**Range bars.** "Range" beside the bar lengths shows the same prices as range bars: a bar ends when the price has left a range of the size you set, however long that took.

- The size is typed in ticks, as in TradingView (60R is sixty ticks), or as the price move that makes; the two fields follow each other. Enter, the "Apply" button or leaving the field rebuilds the bars, and "−" / "+" step along 1, 2, 5, 10, 20, 50… ticks. A tick is the smallest step of the recorded prices (0.01 for prices with two decimals). In TradingView it is the tick of the symbol you look at, which may be another size (0.25 on NQ futures, where 60R is 15 points): compare the price move, not the number of ticks.
- "Auto" chooses a size that suits the period. The choice is made once for an instrument, a period and a side, and kept while the page reads the data again; pressing Auto again makes it anew. The page remembers, in the browser, the last size used for the index and the last used for turbos.
- Each bar is shown with when it began and when it ended, to the ten seconds, and how long that was. Where several bars began in the same ten seconds, the chart spaces them a second apart on its axis; the readout, the table and the CSV file give the real times.
- They are built from the ten-second bars of the side shown. Inside ten seconds the order of the high and the low is not recorded: the low is taken first when the ten seconds rose, the high first when they fell. A bar opens where the previous one closed.
- **No bar is made up for a time that was not recorded.** After more than a minute without a bar (a night, a recording gap), the bar in progress ends as it is, the chart leaves one empty slot and a new bar starts at the first price recorded. This is TradingView with "Phantom bars" off.
- Up to 31 days and 12,000 bars at a time; beyond, the page asks for a larger range or a shorter period.

For anything else, three tables in PostgreSQL, created by the recorder:

- `price_instrument`: one row per instrument, with `uic`, `asset_type`, `role` (`turbo` or `underlying`), `indice`, `direction`, `barrier` (the knock-out level) and `issuer`.
- `price_bar_10s`: `ts` (start of the ten seconds, UTC), `instrument_id`, `bid_open/high/low/close`, `ask_open/high/low/close`, `updates` and `delayed_minutes`. One partition per month.
- `price_recorder_coverage`: one row per ten seconds the recorder was running, with `feed_ok`.

How to read a bar:

- `updates = 0` means the quote did not move in those ten seconds: the four values are the quote that was standing.
- **No row** for an instrument means it was not quotable (market closed, turbo knocked out) or that nothing was being recorded. `price_recorder_coverage` tells the two apart: no row there, or `feed_ok = false`, means the prices of that moment are not known.
- A bar opens on the quote that was standing when its ten seconds began, not on the first change inside it.
- `delayed_minutes` is Saxo's own flag: above 0, your account receives that instrument late.

To take a period out, with each instrument's description beside its prices:

```bash
wataprices --from 2026-10-12 --to 2026-10-16 --indice us100
# -> /app/wata/var/lib/price_export/prices_2026-10-12_2026-10-16.csv.gz
```

Days are those of the trading timezone, both included.

### What to keep in mind

- **Size**: about 5,000 rows per instrument and trading day. With 50 to 70 instruments seen in a day, that is roughly 8 to 14 GB a year. Check the free disk space of the server, and set `retention_months` if it is short.
- **Resolution**: inside a bar, the extremes are those of the stream (one second), not of every tick.
- **Not yet run against Saxo**: see the verification status in the changelog. Try it in Saxo's simulation environment first.

## 📈 Reporting

The [Admin Interface](#️-admin-interface) has a performance page that reads the live PostgreSQL database. WATA also generates trading reports and includes a local visualization dashboard built on Observable Framework:

- Daily/cumulative profit tracking
- Performance analysis by action type
- Win-rate and position duration metrics
- Closed-trade and daily-profit history
- Current account balance and milestone projections in daily reports
- Interactive data exploration

![reporting_example.gif](docs/images/reporting_example.gif)

_(yeah, I'm still loosing money... but at least ... I know how much !)_

**Data-source note:** PostgreSQL is the live runtime database, but the current Observable dashboard sync scripts still download the legacy DuckDB file from `/app/wata/var/lib/duckdb/` and export it with the DuckDB CLI. They do not export records written to PostgreSQL after the migration. Treat this dashboard sync as a legacy DuckDB export until a PostgreSQL export path is added; application-generated daily reports use the runtime database.

### Requirements

To use the reporting system, you need:
- Node.js and npm installed
- DuckDB CLI for the existing legacy dashboard export
- Python 3.12+ (with required libraries `./reporting/requirements.txt`)
- Ansible configured with proper inventory (same as deployment stage)

### Setting Up the Dashboard

1. **Run the Setup Script**
   ```bash
   ./reporting/setup_dashboard.sh
   ```
   This script creates a new Observable Framework project in `reporting/trading-dashboard`.

2. **Sync Trading Data**
   ```bash
   ./reporting/sync_reporting_data.sh
   ```
   This script synchronizes your trading data from the server to your local dashboard:
   - Fetches the legacy DuckDB file from your production server
   - Exports the database to Parquet format
   - Generates the necessary JSON files for visualization
   - Copies all data to the Observable Framework project

3. **Start the Dashboard Server**
   ```bash
   ./reporting/start_report_server.sh
   ```
   This launches the development server on port 4321.
   Access the dashboard at: http://localhost:4321

## ❓ Frequently Asked Questions

Here are some of the most common questions about WATA:

### General
- **Is WATA suitable for beginners?**  
  WATA requires understanding of trading concepts, Python, and server management. It's recommended for users with intermediate technical skills.

### Trading
- **What markets can I trade with WATA?**  
  WATA supports trading Knock-out warrants (Turbos) available on Saxo Bank, primarily focusing on major indices.

### Setup
- **What are the minimum server requirements?**  
  A basic VPS with 2GB RAM, 50GB SSD, 2 core CPU should be sufficient for getting started.

For a comprehensive list of questions and answers, please see our detailed [FAQ documentation](https://ioiti.github.io/wata-docs/docs/faq).

## 🤝 Acknowledgements

- [@hootnot](https://github.com/hootnot): [Saxo OpenAPI library](https://github.com/hootnot/saxo_openapi)
- [PostgreSQL](https://www.postgresql.org/), [asyncpg](https://github.com/MagicStack/asyncpg), [HTTPX](https://www.python-httpx.org/), [Observable Framework](https://observablehq.com/framework), [DuckDB](https://duckdb.org/) (migration and legacy reporting), [FastAPI](https://fastapi.tiangolo.com/), [RabbitMQ](https://www.rabbitmq.com/), [Traefik](https://traefik.io/), [Ansible](https://www.ansible.com/)

## 🛠️ Contributors

- [@ioiti](https://github.com/IOITI): Project author and maintainer
- [@hootnot](https://github.com/hootnot): [Saxo OpenAPI library](https://github.com/hootnot/saxo_openapi)

### How to Contribute

We welcome contributions to WATA! Here's how you can help:

1. **Report Issues**: Submit bugs or suggest features via GitHub issues
2. **Submit Pull Requests**: Code improvements, documentation fixes, or new features
3. **Share Feedback**: Let us know how you're using WATA and what could be improved

## 📄 License

MIT License

Copyright (c) 2025 IOITI

## 📚 Documentation Website

WATA have a Docusaurus-based documentation website. The website is hosted on GitHub Pages and can be accessed at [https://ioiti.github.io/wata-docs/](https://ioiti.github.io/wata-docs/).

### Documentation Structure

The documentation is organized into the following sections:

- **Introduction**: Overview of WATA
- **How-To Guide**: Step-by-step instructions for setting up and using WATA
- **Configuration Guide**: Details on how to configure WATA
- **Saxo Authentication**: Guide for authenticating with Saxo Bank's API
- **FAQ**: Frequently asked questions about WATA

### Contributing to Documentation

We welcome contributions to the documentation! If you find any errors or have suggestions for improvements, please submit a pull request or open an issue on [this GitHub repository.](https://github.com/IOITI/wata-docs)
