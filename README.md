# WATA - Warrant Automated Trading Assistant

An automated Python-based trading system for Knock-out warrants (Turbos) on Saxo Bank, executing trades via webhook signals.

## ⚠️ Disclaimer

**This is a personal learning project and not production-ready software.**

**Risk Warning: WATA can lose all your money due to:**
- Insufficient testing
- Limited security measures
- Lack of fail-safe mechanisms
- No comprehensive monitoring
- Absence of fail-over systems
- Limited user experience

*This software is provided "as is" without warranty. The authors accept no liability for any damages arising from its use.*

## 🎯 Purpose

WATA automates high-risk day-trading of Turbos (leveraged products) on Saxo Bank. It eliminates emotional decision-making by following predefined rules without human intervention.

> Target: 1% daily profit through algorithmic trading

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

OAuth 2.0 integration with Saxo Bank API:
- Selenium-based browser authentication
- Token management with automatic refresh
- Secure storage of credentials

**Known Issue**: Currently incompatible with Saxo's 2FA system

## 🚀 Setup & Deployment

### Prerequisites
- Ubuntu server
- Docker and Docker Compose
- Python 3.12+

### Quick Start
1. Configure `etc/config.json` with your credentials
2. Build package: `./package.sh`
3. Deploy using Ansible or manual installation
4. Manage with aliases: `watastart`, `watastop`, `watalogs`, `watastatus`

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

Launch with: `./reporting/hello-framework/start_report_server.sh`

## 👏 Acknowledgements

- [@hootnot](https://github.com/hootnot): [Saxo OpenAPI library](https://github.com/hootnot/saxo_openapi)
- [Observable Framework](https://observablehq.com/framework), [DuckDB](https://duckdb.org/), [FastAPI](https://fastapi.tiangolo.com/), [RabbitMQ](https://www.rabbitmq.com/), [Ansible](https://www.ansible.com/)

## 🛠️ Contributors

- [@ioiti](https://github.com/IOITI): Project author
- [@hootnot](https://github.com/hootnot): [Saxo OpenAPI library](https://github.com/hootnot/saxo_openapi)

## 📄 License

MIT License

Copyright (c) 2025 IOITI