## WATA FAQ

**General**

*   **Q: What is WATA?**
    *   A: WATA (Warrants Automated Trading Assistant) is an automated Python-based trading system designed to execute trades for Knock-out warrants (Turbos) on Saxo Bank based on webhook signals (e.g., from TradingView).
*   **Q: Who is WATA intended for?**
    *   A: WATA is suited for traders who want to automate their strategies for Saxo Bank Knock-out warrants, need reliable signal execution, require trade tracking and analysis, and prefer systematic, rule-based trading.
*   **Q: Is WATA production-ready or safe?**
    *   A: **No.** WATA is explicitly stated as a personal learning project and **not production-ready**. It comes with significant risks, including the potential loss of all invested capital due to factors like insufficient testing, limited security, lack of fail-safes, and no comprehensive monitoring. Use it at your own risk.
*   **Q: What are the main features of WATA?**
    *   A: Automated trade execution via webhooks, risk management (stop-loss/take-profit handling based on configuration), performance tracking with analytics (using DuckDB), real-time Telegram notifications, and a microservice architecture.
*   **Q: What is the architecture of WATA?**
    *   A: WATA uses a microservice architecture consisting of a Web Server (receives signals), Trader (executes Saxo operations), Scheduler (orchestrates jobs), Telegram (sends notifications), and RabbitMQ (handles messaging between components).

**General Trading**

*   **Q: What type of trading can I do with WATA ?**
    *   A: WATA is primarily designed for short-term or daily trading strategies, aiming for consistent small gains, such as targeting 1% profit per trading day. However, the specific strategy depends on the signals you feed into it.
*   **Q: Can WATA manage multiple different positions simultaneously (e.g., long US100 and short FRA40)?**
    *   A: WATA current design focuses on processing one trade signal and managing the resulting position at a time for a specific underlying index. While it can technically handle receiving a new signal while a position is open (e.g., a 'close' signal), it is not designed for complex portfolio management involving multiple, independent, concurrent positions across different indices. Its core logic processes signals sequentially for the designated asset.
*   **Q: How does the application interact with my Saxo bank account/capital?**
    *   A: WATA operates on a single-asset strategy per trade signal. When a signal is received for an allowed index (e.g., US100), it uses a configurable percentage of your available Saxo account balance to purchase Turbo warrants. You can control this percentage using the `max_account_funds_to_use_percentage` parameter in the `trade.config.buying_power` section of your configuration (default: 100%). WATA does not support portfolio diversification or allocating specific percentages of capital to different assets simultaneously (e.g., you cannot have it manage 40% in CAC40 and 60% in NASDAQ concurrently). Each trade action focuses on one underlying asset at a time.
*   **Q: What is the maximum leverage WATA can use?**
    *   A: The maximum leverage is determined by the Saxo Bank platform and the specific Turbo warrants available. WATA does not impose additional leverage limits beyond what Saxo Bank allows for the selected instruments.
*   **Q: What specific financial instruments does WATA trade?**
    *   A: WATA is specifically designed to trade Knock-out warrants, also known as Turbos, available on the Saxo Bank platform. It identifies these instruments based on the underlying index specified in the trading signal (e.g., US100, FRA40) and your configuration.
*   **Q: Where do the trading signals come from? Does WATA generate its own signals?**
    *   A: No, WATA does not generate its own trading signals. It acts as an execution engine that relies on *external* signals sent via webhooks. You need to configure a separate service (like TradingView alerts or a custom script) to generate these signals based on your strategy and send them to WATA's webhook endpoint.
*   **Q: Does WATA include risk management features?**
    *   A: WATA has basic risk management capabilities configurable in `config.json`, such as automatic stop-loss and take-profit handling for positions based on predefined rules, and daily profit target limits. However, it's crucial to remember WATA is **not production-ready** and lacks sophisticated fail-safes. You are ultimately responsible for defining appropriate rules and monitoring its operation closely. **Significant financial loss is possible.**


**Setup & Costs**

*   **Q: What do I need to run WATA?**
    *   A: You need an Ubuntu Server VPS (minimum 2GB RAM, 50GB SSD, 2 core CPU recommended), Docker and Docker Compose, Python 3.12+, Ansible (optional, for automated deployment), a live Saxo Bank account enabled for Turbo trading, and a Telegram account. A TradingView account (paid plan) is recommended for signal generation.
*   **Q: What are the estimated costs of running WATA?**
    *   A: Costs include VPS hosting (€60 - €312+ annually), potentially a TradingView subscription for webhooks (€130 - €260+ annually), and Saxo Bank trading costs (commissions if applicable, spreads, financing costs). The WATA software itself is free.
*   **Q: How do I install and deploy WATA?**
    *   A: Deployment involves cloning the repository, configuring an Ansible inventory file with your server details, running the `./package.sh` script, and then executing `./deploy/tools/deploy_app_to_your_server.sh`. Detailed steps are in the [HOW-TO Guide](docs/HOW-TO.md) and [README.md](README.md).
*   **Q: How do I start, stop, or check the status of WATA?**
    *   A: Use the aliases set up on your server: `watastart` (start), `watastop` (stop), `watastatus` (check status), `watalogs` (view logs).

**Configuration**

*   **Q: How do I configure WATA?**
    *   A: After deployment, navigate to the `/app/deploy` directory on your server. Set the RabbitMQ password in the `.env` file (copied from `.env.example`). Then, copy `/app/etc/config_example.json` to `/app/etc/config.json` and edit it with your Saxo API details, Telegram bot info, trading rules, etc. Refer to the [CONFIGURATION Guide](docs/CONFIGURATION.md) for details.
*   **Q: What information do I need from Saxo Bank for configuration?**
    *   A: You need your Saxo account username and password, and the Application details (AppName, AppKey, AppSecret) obtained by registering an application in the Saxo Developer Portal (Live Apps section). This is configured in the `authentication.saxo` section of `config.json`.
*   **Q: Can I limit how much of my account balance WATA uses for trading?**
    *   A: Yes. Configure the `max_account_funds_to_use_percentage` parameter in the `trade.config.buying_power` section of your `config.json`. This parameter (value 1-100) controls what percentage of your available account balance can be used for a single position. For example, setting it to 50 means WATA will only use up to 50% of your available funds for any trade, providing a safety margin and limiting exposure.
*   **Q: How do I set up Telegram notifications?**
    *   A: Create a Telegram bot and get its `bot_token` and your `chat_id`. Add these details to the `telegram` section in `config.json`. Instructions for creating a bot can be found via links in the [HOW-TO Guide](docs/HOW-TO.md).
*   **Q: How do I define which instruments WATA can trade?**
    *   A: Use the `allowed_indices` rule within the `trade.rules` section of `config.json`. Map a friendly name (used in webhooks) to the Saxo Bank `UnderlyingUics` ID for the desired index.
*   **Q: Can I configure trading hours or holidays?**
    *   A: Yes. Use the `market_hours` rule to set `trading_start_hour` and `trading_end_hour`. Use the `market_closed_dates` rule to list dates (MM/DD/YYYY) when trading should not occur. Both are configured in the `trade.rules` section.
*   **Q: How do I configure daily profit targets or limits?**
    *   A: Use the `day_trading` rule in `trade.rules`. Configure `percent_profit_wanted_per_days` for the target and `dont_enter_trade_if_day_profit_is_more_than` to prevent new trades if a profit threshold is met. You can also set `close_position_time` to automatically close positions at a specific time.

**Saxo Bank Authentication**

*   **Q: How does Saxo Bank authentication work?**
    *   A: WATA uses OAuth 2.0. When authentication is needed (e.g., on first start or token expiry), the application will send an authorization URL via Telegram (or logs). You must open this URL, log in to Saxo, and authorize the application.
*   **Q: What do I do with the `code` parameter in the URL after authorizing Saxo?**
    *   A: After successful authorization in your browser, you'll be redirected to a URL containing a `code=...` parameter. Copy the value of this code. Then, on your server, run the command `watasaxoauth <COPIED_CODE>`.
*   **Q: What happens if the Saxo authentication code expires?**
    *   A: The code is short-lived (a few minutes). If it expires before you use `watasaxoauth`, you'll likely get a "Failed to obtain new tokens" error. You need to restart the process to get a new authorization URL and code.
*   **Q: What does the "Timeout waiting for authorization code" error mean?**
    *   A: It means the application waited (typically 5 minutes) for the code to be submitted via `watasaxoauth` but didn't receive it. You'll need to restart the authentication process.

**Trading & Webhooks**

*   **Q: How do I send trading signals to WATA?**
    *   A: Send a POST request to `/webhook?token=YOUR_SECRET_TOKEN` on your WATA server's IP/domain. The payload should be a JSON object containing `action` ("long", "short", or "close-position"), `indice` (matching an allowed index name from your config), `signal_timestamp`, and `alert_timestamp`.
*   **Q: Where do I find the `YOUR_SECRET_TOKEN` for the webhook URL?**
    *   A: The token is automatically generated by WATA the first time the web server starts. Find the path specified in `webserver.persistant.token_path` within your `/app/etc/config.json` (default: `/app/var/lib/web_server/persist_token.json`). View the contents of that file on your server (e.g., using `cat`) to get the token value.
*   **Q: Do I need TradingView? What plan is required?**
    *   A: TradingView is recommended but not strictly required if you have another signal source capable of sending webhooks in the correct format. If using TradingView for webhooks, you need a paid plan (Essentials, Plus, or Premium) as free plans don't support webhooks reliably for automation.
*   **Q: How do I configure a TradingView alert for WATA?**
    *   A: In the TradingView alert settings, enable the "Webhook URL" option and paste your WATA webhook URL (including the secret token). In the "Message" box, paste the required JSON payload format, using `"{{timenow}}"` for the timestamps and ensuring the `indice` matches one in your `config.json`.

**Reporting & Monitoring**

*   **Q: How can I track WATA's trading performance?**
    *   A: WATA stores trade data in a DuckDB database. An Observable Framework-based reporting dashboard can be set up locally to visualize performance (daily/cumulative P&L, win rate, etc.). Follow the steps in the [README.md#📈-reporting](../README.md#%F0%9F%93%88-reporting) section.
*   **Q: How do I monitor the application's activity?**
    *   A: Check the Telegram notifications for real-time updates on signals, trades, and errors. Use the `watalogs` command on the server to view detailed logs. Use `watastatus` to check the status of the Docker containers.

