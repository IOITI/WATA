# WATA (Warrants Automated Trading Assistant) - Agent Guide

This document provides a comprehensive guide for an AI agent to understand, modify, and maintain the WATA project.

## 1. Project Overview

WATA is an automated trading system for executing Knock-out Warrants (Turbos) on the Saxo Bank platform. It operates as a collection of microservices that listen for trading signals (e.g., from TradingView via webhooks) and executes them according to a predefined set of rules.

The system is designed for automated execution, risk management, performance tracking, and real-time monitoring via Telegram.

## 2. Architecture and Services

WATA uses a microservice architecture orchestrated with Docker Compose. A central RabbitMQ message broker facilitates communication between services.

### 2.1. Service Orchestration

- **Docker Compose (`deploy/docker-compose.yml`)**: Defines and configures all the services. All application services use a common base image (`wata-base:latest`).
- **Entrypoint Script (`src/start_python_script.sh`)**: This script is the `CMD` for the Docker image. It reads the `WATA_APP_ROLE` environment variable to determine which Python script to run, effectively launching the specific service for that container.

### 2.2. Service Details

| Service Role | `WATA_APP_ROLE` | Entry Point Script | Purpose |
| :--- | :--- | :--- | :--- |
| **Trader** | `trader` | `src/main.py` | The core service. It consumes trading signals from RabbitMQ, checks them against trading rules, executes trades via the Saxo API, and manages positions. |
| **Web Server** | `web_server` | `src/web_server/__init__.py` | A FastAPI application that exposes a webhook endpoint. It receives signals from external sources, validates them, and publishes them to the `trading-action` RabbitMQ queue. |
| **Scheduler** | `scheduler` | `src/scheduler/__init__.py` | Runs periodic tasks using the `schedule` library. It can trigger actions like `check_positions_on_saxo_api` or `daily_stats` by publishing messages to RabbitMQ. |
| **Telegram** | `telegram` | `src/mq_telegram/__init__.py` | Consumes messages from a dedicated RabbitMQ queue and forwards them to a specified Telegram chat, providing real-time notifications and alerts. |
| **RabbitMQ** | N/A | N/A | The message broker that decouples the services and handles asynchronous communication. |

## 3. Codebase Structure

- `src/`: Contains all the core source code for the application's microservices.
  - `main.py`: Entry point for the **Trader** service.
  - `web_server/`: Code for the **Web Server** service, including the FastAPI app and token management.
  - `scheduler/`: Code for the **Scheduler** service.
  - `mq_telegram/`: Code for the **Telegram Notifier** service.
  - `saxo_authen/`: Handles Saxo Bank OAuth 2.0 authentication flow.
  - `saxo_openapi/`: A library for interacting with the Saxo OpenAPI.
  - `trade/`: Contains the core trading logic, including `rules.py` and API action handlers.
  - `database/`: Manages the DuckDB database interactions.
  - `start_python_script.sh`: The main entrypoint script for the Docker containers.
- `etc/`: Configuration files.
  - `config.json`: The main configuration file for the entire application.
  - `config_example.json`: An example template for the configuration.
- `deploy/`: Contains deployment-related files.
  - `docker-compose.yml`: The main Docker Compose file for production.
  - `Dockerfile`: The Dockerfile used to build the `wata-base` image.
- `reporting/`: Scripts and tools for generating performance reports and visualizations.
- `tests/`: Contains unit and integration tests.
- `requirements.txt`: A complete list of all Python dependencies.
- `setup.py`: Defines project metadata and, importantly, the console script entry points.

## 4. Configuration (`etc/config.json`)

All services are configured via a single JSON file, typically located at `/app/etc/config.json` inside the containers.

- `authentication`:
  - `saxo`: Holds OAuth 2.0 credentials (`AppName`, `AppKey`, `AppSecret`) and endpoints for the Saxo API.
  - `persistant.token_path`: File path for storing the persistent Saxo authentication token.
- `webserver`:
  - `persistant.token_path`: File path for the webhook authentication token.
  - `app_secret`: A secret key used for securing the web server.
- `logging`: Configures logging level, path, and format.
- `rabbitmq`: Connection details for the RabbitMQ broker.
- `duckdb`:
  - `persistant.db_path`: The file path for the DuckDB database.
- `trade`:
  - `rules`: An array of rule objects that define the trading logic (see section 6).
  - `config`: General trading parameters like `turbo_preference`, `buying_power`, `position_management` (stop-loss/take-profit), API limits, and `timezone`.
- `telegram`:
  - `bot_token`, `chat_id`, `bot_name`: Credentials for the Telegram bot.

## 5. Key Operations and CLI Tools

The `setup.py` file creates console scripts for managing authentication.

### 5.1. Saxo Authentication (`watasaxoauth`)

This command is used to complete the OAuth 2.0 flow for Saxo Bank.
- **File**: `src/saxo_authen/cli.py`
- **Command**: `watasaxoauth`
- **Function**: When the application requires authentication, the user must run this command and paste the authorization `code` obtained from the Saxo login URL. The script securely reads the code using `getpass` and saves it to a file where the Trader service can pick it up to complete the authentication.

### 5.2. Webhook Token Management (`watawebtoken`)

This command manages the static token used to secure the webhook endpoint.
- **File**: `src/web_server/cli.py`
- **Command**: `watawebtoken`
- **Function**:
  - `watawebtoken` or `watawebtoken --display`: Displays the current token.
  - `watawebtoken --new`: Generates, saves, and displays a new token.
- **Usage**: The token must be included as a query parameter in webhook URLs (e.g., `?token=YOUR_TOKEN`).

### 5.3. Sending a Trading Signal

To trigger a trade, send a `POST` request to the webhook endpoint (`/webhook?token=...`) with a JSON payload like this:

```json
{
  "action": "long",
  "indice": "us100",
  "signal_timestamp": "2023-07-01T12:00:00Z",
  "alert_timestamp": "2023-07-01T12:00:01Z"
}
```

## 6. Trading Logic (`src/trade/rules.py`)

The `TradingRule` class validates incoming signals against a set of configurable rules before execution. Any modification to trading behavior should likely start here.

- **Signal Timestamp Validation**: Rejects signals that are older than `max_signal_age_minutes`.
- **Market Hours**: Ensures trades only occur within `trading_start_hour` and `trading_end_hour` and not on dates listed in `market_closed_dates`. It also avoids a "risky" period at the end of the day.
- **Profit/Loss Limits**: Prevents new trades if the `dont_enter_trade_if_day_profit_is_more_than` target is met or if the `max_day_loss_percent` is breached.
- **Allowed Indices**: Checks if the signal's `indice` is configured in `allowed_indices` and maps it to a Saxo `indice_id`.
- **Duplicate Position Check**: Prevents opening a new position if a position with the same action (`long` or `short`) is already open.

## 7. Key Dependencies

The project relies on several key libraries defined in `requirements.txt`:

- **`fastapi` & `uvicorn`**: For the asynchronous web server.
- **`pika`**: For communicating with RabbitMQ.
- **`duckdb`**: For the embedded analytical database.
- **`requests` & `httpx`**: For making HTTP requests to the Saxo API.
- **`schedule`**: For running periodic tasks in the scheduler service.
- **`jsonschema`**: For validating incoming webhook data.
- **`cryptography`**: For handling encryption of tokens.
- **`pydantic`**: For data validation and settings management.
