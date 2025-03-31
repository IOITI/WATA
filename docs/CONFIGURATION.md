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
      "rule_name": "allowed_indice",
      "rule_type": "indice",
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
      "rule_name": "profit_per_days",
      "rule_type": "profit_per_days",
      "rule_config": {
        "percent_profit_wanted_per_days": 1.7,
        "dont_enter_trade_if_day_profit_is_more_than": 1.25
      }
    }
  ],
  "config": {
    "turbo": {
      "exchange_id": "CATS_SAXO"
    }
  },
  "persistant": {
    "last_action_file": "/app/var/lib/trade/last_action.json"
  }
}
```

### Trading Rules Explained

1. **allowed_indice**:
   - Defines which indices can be traded
   - Maps friendly names used in the webhook signal to Saxo Bank instrument IDs (As `UnderlyingUics`)
   - You can use https://www.saxoinvestor.fr/investor/page/turbos to select all the available indices, and you will find their corresponding `UnderlyingUics` in parameters URL.
   - Example: "us100" mapped to Saxo ID "1909050" (https://www.saxoinvestor.fr/investor/page/turbos-list?assettypes=WarrantKnockOut%2CWarrantOpenEndKnockOut%2CMiniFuture%2CWarrantDoubleKnockOut&includenontradable=false&instrumentlimit=100&isNavigatedThroughDedicatedAutoInvest=false&issuers=Vontobel%20Financial%20Products%20GM&orderby=ThreeMonthsPopularity%20asc&productGroup=Turbos&size=100&underlyingassettypes=StockIndex&underlyinguics=1909050)

2. **market_closed_dates**:
   - Lists dates when markets are closed (holidays)
   - Format: "MM/DD/YYYY"
   - Trading will not occur on these dates

3. **profit_per_days**:
   - Sets daily profit targets and limits
   - **percent_profit_wanted_per_days**: Target daily profit percentage (1.7%)
   - **dont_enter_trade_if_day_profit_is_more_than**: Don't open new positions if daily profit exceeds this threshold (1.25%)

### Trading Configuration

- **turbo.exchange_id**: Exchange ID for turbo warrant instruments
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