# WATA - Warrants Automated Trading Assistant

An automated Python-based trading system for Knock-out warrants (Turbos) on Saxo Bank, executing trades via webhook signals.

## ⚠️ **Disclaimer**

**This is a personal learning project and not production-ready software.**

**Risk Warning: WATA can lose all your money due to:**
- Insufficient code testing
- Limited security measures
- Lack of fail-safe mechanisms
- No comprehensive monitoring included
- Absence of fail-over systems
- Limited user experience

*This software is provided "as is" without warranty. The authors accept no liability for any damages arising from its use.*

## 🎯 Purpose

WATA (Warrants Automated Trading Assistant) is an algorithmic trading system compagnon, designed for automated execution of Knock-out warrants (Turbos) on Saxo Bank. It serves as a reliable bridge between trading signals and actual market execution, offering several key benefits:

- **Automated Execution**: Eliminates emotional bias and human error by executing trades based on predefined rules and signals (from TradingView, for example)
- **Risk Management**: Implements systematic position monitoring with stop-loss and take-profit mechanisms
- **Performance Tracking**: Provides comprehensive analytics and reporting for trade analysis
- **Real-time Monitoring**: Delivers instant notifications via Telegram for trade execution and system status
- **Scalability**: Built on a microservice architecture for reliable and maintainable operation

The system is particularly suited for traders who:
- Want to automate their trading strategies
- Need reliable execution of trading signals
- Require comprehensive trade tracking and analysis
- Value real-time monitoring and alerts
- Prefer systematic, rule-based trading over discretionary decisions

## 🏗️ Architecture

WATA uses a microservice architecture with:

| Component (roles) | Purpose                                                       |
|-------------------|---------------------------------------------------------------|
| **Web Server**    | Receives webhook signals from third party (like: TradingView) |
| **Trader**        | Executes Saxo Bank API operations                             |
| **Scheduler**     | Manages job orchestrations                                    |
| **Telegram**      | Delivers notifications and alerts                             |
| **RabbitMQ**      | Handles inter-component messaging                             |

## 📊 Trading Workflow

1. **Signal Reception**
   - Validate incoming webhooks (authentication, schema)
   - Parse action type (long, short, close)

2. **Rule Validation**
   - Verify market hours, timestamp freshness
   - Check allowed indices and position duplicates
   - Apply daily profit limits

3. **Trade Execution**
   - For new positions: instrument search, order calculation, position confirmation
   - For closing: position retrieval, order creation, performance reporting
   - Automatic position monitoring with stop-loss/take-profit handling

4. **Performance Tracking**
   - Daily statistics generation
   - Performance metrics reporting
   - Database storage for analysis

## 💾 Database System

WATA uses DuckDB for fast in-memory analytics:

- **Order tracking**: Complete order and positions history with execution details
- **Position management**: P&L calculations and performance metrics 
- **Performance analytics**: Daily statistics and trading history
- **Advantages**: High-speed analytics, corruption prevention, SQL support

## 🔐 Authentication

WATA uses OAuth 2.0 for Saxo Bank API integration:

1. When authenticating, the application send you a URL via Telegram (or logs of container)
2. Open this URL in your browser and log in with your Saxo credentials, and do all the steps to authorize the application (2FA, risk warning, etc.)
3. After authorization, you'll be redirected to a page with a URL containing a `code` parameter
4. Copy the code value and run `watasaxoauth <CODE>` on your server
5. The application completes authentication using this code

**Command Reference**:
- `watasaxoauth <CODE>`: Submit the authorization code
- The authorization code is valid only for a short time (typically a few minutes)

**Troubleshooting**:
- If you receive an invalid/expired code error, repeat the process
- A "Timeout waiting for authorization code" error means the application waited for 5 minutes without receiving the code

## 🚀 Setup & Deployment

### Prerequisites
- Ubuntu server
- Docker and Docker Compose
- Python 3.12+
- Ansible (for automated deployment)

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

### Docker Compose Setup

The application uses Docker Compose with an override file for enhanced configuration:

1. **Environment Variables**
   - The system uses a `.env` file in the `deploy` directory to manage sensitive configuration.
   - The primary use is for setting the RabbitMQ password which is then synchronized with your config.json.

2. **Service Dependencies**
   - A special `setup` service runs before other services to ensure configuration is properly synchronized.
   - This setup updates the RabbitMQ password in your config.json file to match the one set in your .env file.
   - All other services (web_server, trader, scheduler, telegram) depend on both the setup service and the RabbitMQ service.

### Configuration

After deployment, you need to set up your configuration:

1. **Configure RabbitMQ Password**
   ```bash
   # Navigate to the deploy directory
   cd /app/deploy
   
   # Copy the example .env file
   cp .env.example .env
   
   # Edit the .env file to set your custom RabbitMQ password
   nano .env
   ```
   
   This sets the password used by RabbitMQ and automatically updates your config.json file through the setup service in docker-compose.

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
         "username": "your_saxo_username",
         "password": "your_saxo_password",
         "app_config_object": {
           "AppName": "your_app_name",
           "AppKey": "your_app_key",
           "AppSecret": "your_app_secret"
         }
       }
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
     - `allowed_indice`: Configure allowed trading indices
     - `market_closed_dates`: Set market holidays
     - `profit_per_days`: Define profit targets and limits

   - **Other Settings**
     - Logging configuration
     - RabbitMQ connection details
     - DuckDB database path
     - Webhook authentication

4. **Restart Services**
   ```bash
   watastop
   watastart
   ```

### Usage

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

## 📈 Reporting

WATA includes a visualization dashboard built on Observable Framework:

- Daily/cumulative profit tracking
- Performance analysis by action type
- Win-rate and position duration metrics
- Interactive data exploration

### Requirements

To use the reporting system, you need:
- Node.js and npm installed
- DuckDB CLI installed
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
   - Fetches DuckDB data from your production server
   - Exports the database to Parquet format
   - Generates the necessary JSON files for visualization
   - Copies all data to the Observable Framework project

3. **Start the Dashboard Server**
   ```bash
   ./reporting/start_report_server.sh
   ```
   This launches the development server on port 4321.
   Access the dashboard at: http://localhost:4321

## 👏 Acknowledgements

- [@hootnot](https://github.com/hootnot): [Saxo OpenAPI library](https://github.com/hootnot/saxo_openapi)
- [Observable Framework](https://observablehq.com/framework), [DuckDB](https://duckdb.org/), [FastAPI](https://fastapi.tiangolo.com/), [RabbitMQ](https://www.rabbitmq.com/), [Ansible](https://www.ansible.com/)

## 🛠️ Contributors

- [@ioiti](https://github.com/IOITI): Project author
- [@hootnot](https://github.com/hootnot): [Saxo OpenAPI library](https://github.com/hootnot/saxo_openapi)

## 📄 License

MIT License

Copyright (c) 2025 IOITI