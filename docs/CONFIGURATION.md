# WATA Configuration Guide

This document provides details on how to configure WATA (Warrants Automated Trading Assistant).

## Configuration File Structure

WATA uses a JSON configuration file (`config.json`) located in the `etc/` directory. Below is a detailed explanation of each section.

## Authentication

### Saxo Bank API Authentication

```json
"authentication": {
  "saxo": {
    "username": "xxxx",
    "password": "xxxx",
    "app_config_object": {
      "AppName": "xxxx",
      "AppKey": "xxxx",
      "AuthorizationEndpoint": "https://live.logonvalidation.net/authorize",
      "TokenEndpoint": "https://live.logonvalidation.net/token",
      "GrantType": "Code",
      "OpenApiBaseUrl": "https://gateway.saxobank.com/openapi/",
      "RedirectUrls": [
        "https://localhost"
      ],
      "AppSecret": "xxxx"
    }
  },
  "persistant": {
    "token_path": "/app/var/lib/saxo_auth/persist_token.json"
  }
}
```

- **username**: Your Saxo Bank account username
- **password**: Your Saxo Bank account password
- **app_config_object**: OAuth 2.0 configuration for the Saxo API
  - **AppName**: Your registered application name
  - **AppKey**: Your API key from Saxo Bank Developer Portal
  - **AuthorizationEndpoint**: OAuth authorization URL
  - **TokenEndpoint**: OAuth token URL
  - **GrantType**: OAuth grant type (should be "Code")
  - **OpenApiBaseUrl**: Base URL for Saxo Open API
  - **RedirectUrls**: Callback URLs for OAuth flow
  - **AppSecret**: Your API secret from Saxo Bank Developer Portal
- **persistant.token_path**: File path to store authentication tokens

## Webserver

```json
"webserver": {
  "persistant": {
    "token_path": "/app/var/lib/web_server/persist_token.json"
  }
}
```

- **persistant.token_path**: Path to store webhook authentication tokens

## Logging

```json
"logging": {
  "persistant": {
    "log_path": "/app/var/log/"
  },
  "level": "INFO",
  "format": "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
}
```

- **persistant.log_path**: Directory for storing log files
- **level**: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- **format**: Log message format

## RabbitMQ

```json
"rabbitmq": {
  "hostname": "rabbitmq1",
  "authentication": {
    "username": "trade-app",
    "password": "DONT_TOUCH_IT_IS_SET_BY_DOCKER_COMPOSE"
  }
}
```

- **hostname**: RabbitMQ server hostname
- **authentication**: Credentials for RabbitMQ
  - **username**: RabbitMQ username
  - **password**: RabbitMQ password (automatically set by Docker Compose)

## DuckDB

```json
"duckdb": {
  "persistant": {
    "db_path": "/app/var/lib/duckdb/trading_data.duckdb"
  }
}
```

- **persistant.db_path**: File path for the DuckDB database

## Trading Rules

```json
"trade": {
  "rules": [
    {
      "rule_name": "allowed_indices",
      "rule_type": "allowed_indices",
      "rule_config": {
        "indice_ids": {
          "us100": "1909050"
        }
      }
    },
    {
      "rule_name": "market_closed_dates",
      "rule_type": "market_closed_dates",
      "rule_config": {
        "market_closed_dates": [
          "04/07/2024",
          "02/09/2024",
          "28/11/2024",
          "25/12/2024",
          // Additional dates...
        ]
      }
    },
    {
      "rule_name": "day_trading",
      "rule_type": "day_trading",
      "rule_config": {
        "percent_profit_wanted_per_days": 1.7,
        "dont_enter_trade_if_day_profit_is_more_than": 1.25,
        "close_position_time": "21:55"
      }
    },
    {
      "rule_name": "signal_validation",
      "rule_type": "signal_validation",
      "rule_config": {
        "max_signal_age_minutes": 5
      }
    },
    {
      "rule_name": "market_hours",
      "rule_type": "market_hours",
      "rule_config": {
        "trading_start_hour": 8,
        "trading_end_hour": 22,
        "risky_trading_start_hour": 21,
        "risky_trading_start_minute": 54
      }
    }
  ],
  "config": {
    "turbo": {
      "exchange_id": "CATS_SAXO",
      "price_range": {
        "min": 4,
        "max": 15
      },
      "performance_thresholds": {
          "min": -20,
          "max": 60
        },
        "api_limits": {
          "top_instruments": 200,
          "top_positions": 200,
          "top_closed_positions": 500
        },
        "retry_config": {
          "max_retries": 10,
          "retry_sleep_seconds": 1
        },
        "position_check": {
          "check_interval_seconds": 7,
          "timeout_seconds": 20
        },
        "safety_margins": {
          "bid_calculation": 1
        },
        "websocket": {
          "refresh_rate_ms": 10000
        },
        "trading_mode": "day_trading"
      }
    }
  },
  "persistant": {
    "last_action_file": "/app/var/lib/trade/last_action.json"
  }
}
```

### Trading Rules Type Explained

1. **allowed_indices**:
   - Defines which indices can be traded
   - Maps friendly names used in the webhook signal to Saxo Bank instrument IDs (As `UnderlyingUics`)
   - You can use https://www.saxoinvestor.fr/investor/page/turbos to select all the available indices, and you will find their corresponding `UnderlyingUics` in parameters URL.
   - Example: "us100" mapped to Saxo ID "1909050" (https://www.saxoinvestor.fr/investor/page/turbos-list?assettypes=WarrantKnockOut%2CWarrantOpenEndKnockOut%2CMiniFuture%2CWarrantDoubleKnockOut&includenontradable=false&instrumentlimit=100&isNavigatedThroughDedicatedAutoInvest=false&issuers=Vontobel%20Financial%20Products%20GM&orderby=ThreeMonthsPopularity%20asc&productGroup=Turbos&size=100&underlyingassettypes=StockIndex&underlyinguics=1909050)

2. **market_closed_dates**:
   - Lists dates when markets are closed (holidays)
   - Format: "MM/DD/YYYY"
   - Trading will not occur on these dates

3. **day_trading**:
   - Sets day trading multiple conditions:
     - **percent_profit_wanted_per_days**: Target daily profit percentage (1.7%)
     - **dont_enter_trade_if_day_profit_is_more_than**: Don't open new positions if daily profit exceeds this threshold (1.25%)
     - **close_position_time**: Time when all positions should be automatically closed (format: "HH:MM", default: "21:55")

4. **signal_validation**:
   - Controls validation of trading signals:
     - **max_signal_age_minutes**: Maximum age of a signal in minutes before it's considered too old (default: 5)
     - Signals older than this value will be rejected with a "Signal timestamp is too old" error

5. **market_hours**:
   - Defines the trading hours and restrictions:
     - **trading_start_hour**: Hour when trading begins (24-hour format, default: 8)
     - **trading_end_hour**: Hour when trading ends (24-hour format, default: 22)
     - **risky_trading_start_hour**: Hour when risky trading period begins (24-hour format, default: 21)
     - **risky_trading_start_minute**: Minute when risky trading period begins (default: 54)
     - Trading is not allowed outside the trading hours
     - Trading is considered risky during the period from risky_trading_start_hour:risky_trading_start_minute to trading_end_hour:00

### Trading Configuration

- **turbo.exchange_id**: Exchange ID for turbo warrant instruments
- **turbo.price_range**: Price range for filtering turbo instruments
  - **min**: Minimum price for turbo instruments (default: 4)
  - **max**: Maximum price for turbo instruments (default: 15)
- **turbo.performance_thresholds**: Min/max performance percentages to trigger closing a position
  - **min**: Minimum performance percentage (e.g., -20)
  - **max**: Maximum performance percentage (e.g., 60)
- **turbo.api_limits**: Limits for API requests
  - **top_instruments**: Max instruments to fetch (default: 200)
  - **top_positions**: Max open positions to fetch (default: 200)
  - **top_closed_positions**: Max closed positions to fetch (default: 500)
- **turbo.retry_config**: Settings for retrying actions
  - **max_retries**: Maximum number of retries (e.g., finding position after order) (default: 10)
  - **retry_sleep_seconds**: Seconds to wait between retries (default: 1)
- **turbo.position_check**: Settings for periodic position performance checks
  - **check_interval_seconds**: How often to check performance (default: 7)
  - **timeout_seconds**: How long the check process should run before stopping (default: 20)
- **turbo.safety_margins**: Safety margins for calculations
  - **bid_calculation**: Margin subtracted when calculating bid amount (default: 1)
- **turbo.websocket**: WebSocket configuration
  - **refresh_rate_ms**: Refresh rate for WebSocket subscriptions in milliseconds (default: 10000)
- **persistant.last_action_file**: File path to store the last trading action

## Telegram Notifications

```json
"telegram": {
  "bot_token": "xxxx",
  "chat_id": "xxxx",
  "bot_name": "xxxx"
}
```

- **bot_token**: Telegram Bot API token
- **chat_id**: Telegram chat ID to send notifications to
- **bot_name**: Name of your Telegram bot

## Setting Up Your Configuration

1. Copy the example configuration file:
   ```bash
   cp /app/etc/config_example.json /app/etc/config.json
   ```

2. Edit the configuration file:
   ```bash
   nano /app/etc/config.json
   ```

3. Update with your specific settings:
   - Saxo Bank credentials and API details
   - Telegram bot information
   - Trading rules as needed

4. The RabbitMQ password is automatically set by Docker Compose from the `.env` file in the `deploy` directory.

5. Restart the application to apply changes:
   ```bash
   watastop
   watastart
   ```

## Important Notes

- Store your configuration file securely, as it contains sensitive information
- Backup your configuration before making significant changes
- Some paths are pre-configured for the Docker deployment and should not be changed without updating the Docker Compose configuration
- The RabbitMQ password is managed automatically and should not be edited directly in the config file 