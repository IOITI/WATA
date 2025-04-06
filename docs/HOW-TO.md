# How-To Guide: Setting Up and Using WATA

This guide provides step-by-step instructions to get WATA (Warrants Automated Trading Assistant) up and running, from
acquiring necessary prerequisites to executing your first automated trade.

## Prerequisites

Before you begin, ensure you have the following:

1. **Ubuntu Server VPS**: You need a Virtual Private Server (VPS) running Ubuntu. Popular providers include OVH,
   Hostinger, DigitalOcean, Linode, Vultr, AWS EC2, Google Cloud Compute Engine, etc. Choose a plan suitable for running
   multiple Docker containers.

- Minimum requirements: 2GB RAM, 50GB SSD, 2 core CPU.
- Recommended: 4GB RAM, 100GB SSD, 4 core CPU.

2. **Saxo Bank Account**: A live trading account with Saxo Bank is required to execute real trades.
3. **Telegram Account**: Needed for creating a bot and receiving notifications.
4. **TradingView Account (Optional but Recommended)**: To generate trading signals via webhooks. Other signal sources
   can be adapted.
5. **Basic Linux/Command-Line Familiarity**: You should be comfortable using SSH and running commands on your server.

## Estimated Annual Costs

While WATA itself is free and open-source, running the system involves some recurring costs:

* **VPS Hosting**: This is the primary cost. Depending on the provider and the server specifications (CPU, RAM, SSD),
  expect to pay anywhere from €5 to €20+ per month (approximately €60 - €240+ annually). Costs vary significantly based
  on your chosen provider and plan.
* **TradingView Subscription (Optional)**: If you use TradingView for generating signals via webhooks (recommended),
  you'll need a paid plan (Pro, Pro+, or Premium) to use webhooks effectively. Costs vary depending on the plan and
  billing cycle (check the TradingView website for current pricing). Free plans do not support webhooks reliably for
  automation.
    * Black Friday/Cyber Monday deals are available every year.
* **Saxo Bank Account**: There are typically no annual fees for holding a Saxo Bank account itself, but you will incur
  trading costs (commissions, spreads, financing costs for holding positions ...) when trades are executed. These depend
  heavily on your trading activity and the instruments traded.
    * Saxo Bank Turbo warrants are commission free, other warrants providers might have a commission fee.
* **Domain Name (Optional)**: If you prefer using a domain name instead of an IP address for your server (e.g., for the
  TradingView webhook URL), you'll need to pay an annual registration fee (typically €10-€20).

Therefore, the minimum essential annual cost is primarily for the VPS, ranging roughly from €60 upwards, plus any
TradingView subscription costs if used, and trading fees incurred via Saxo.

## Step 1: Prepare Your Server

1. **Connect to your Server**: Use SSH to log in to your newly acquired Ubuntu VPS.
2. **Install Prerequisites**: Follow the prerequisites section in the main [README.md](../README.md#prerequisites) to
   install necessary software like Docker, Docker Compose, Python 3.12+, and optionally Ansible if you plan to use the
   automated deployment scripts.

## Step 2: Set Up Saxo Bank Integration

1. **Open a Saxo Bank Account**: If you don't have one, sign up for a live trading account
   at [Saxo Bank](https://www.home.saxo/).

- In order to be able to trade on Turbo warrants, you need to pass the test for these intruments on your Saxo Bank
  account for having the "Turbo" permission.
- Add money to your account to be able to trade. (Minimum 30€)

2. **Create a Saxo API Application**:
    * Go to the [Saxo Developer Portal](https://www.developer.saxo), and create an developer account if you don't have
      one yet.
    * Create an application in the "Live" section. `Apps` -> `Live Apps` ->
      `Create Application` [https://www.developer.saxo/openapi/appmanagement#/liveoverview](https://www.developer.saxo/openapi/appmanagement#/liveoverview)
    * Log in with your Saxo Bank credentials.
    * Register a new application. You will typically need to provide:
        * An Application Name (e.g., "WATA Trading Bot").
        * Redirect URL(s): You can often use `http://localhost/` or a placeholder if the application handles OAuth
          redirection internally (like WATA does via command line).
        * Grant Type: Select `Code`.
        * Access Control: Select `Allow this app to be enabled for trading`.
    * Once registered, chose `Copy App Object` and save it in a secure location. These are crucial for WATA's
      configuration.

## Step 3: Set Up Telegram Notifications

You can follow the instructions [here](https://gist.github.com/nafiesl/4ad622f344cd1dc3bb1ecbe468ff9f8a) to create a bot
and get the `bot_token`, and `chat_id`.

## Step 4: Deploy and Configure WATA

1. **Get the WATA Code**: Clone or download the WATA repository to your computer.
2. **Follow Deployment Instructions**: Refer to
   the [Setup & Deployment section in the README.md](../README.md#setup--deployment) for detailed steps. This typically
   involves:
    * **Configuring Inventory** (if using Ansible): Edit `deploy/tools/ansible/inventory/inventory.ini`.
    * **Building the Package**: Run `./package.sh`.
    * **Deploying**: Run `./deploy/tools/deploy_app_to_your_server.sh`.
3. **Configure WATA**:
    * Navigate to the deployment directory on your server (e.g., `/app/deploy`).
    * Set up the `.env` file for the RabbitMQ password as described in
      the [Configuration section of the README](../README.md#configuration).
    * Copy the example configuration: `cp /app/etc/config_example.json /app/etc/config.json`.
    * Edit `/app/etc/config.json` with your details:
        * Saxo app object inside `app_config_object`
        * Telegram `bot_token` and `chat_id`.
        * Trading rules (`allowed_indices`, etc.).
        * Review other settings as needed. Refer to the [CONFIGURATION Guide](CONFIGURATION.md) for full details.
4. **Authenticate with Saxo**:
    * Start the application (`watastart`).
    * Monitor the logs (`watalogs`) or check your Telegram bot for the Saxo authentication URL.
    * Open the URL in your browser, log in to Saxo, and authorize the application.
    * Copy the `code` parameter from the redirect URL.
    * Run `watasaxoauth <PASTE_THE_CODE_HERE>` on your server.

## Step 5: Configure TradingView (or other signal source)

1. **Set Up Webhooks in TradingView**:
    * In TradingView, create alerts based on your trading strategy (e.g., indicator crossovers, price levels).
    * In the alert settings, enable the "Webhook URL" option.
    * Enter the URL of your WATA web server:
      `http://<your_server_ip_or_domain>:<port>/webhook?token=YOUR_SECRET_TOKEN` (replace placeholders).
        * The `port` can be found in the `docker-compose.yml` file for the `web-server` service (usually 80).
        * The `YOUR_SECRET_TOKEN` is **automatically generated** by WATA the first time the web server starts. You need
          to retrieve it from the persistent token file.
        * Look inside your `/app/etc/config.json` file for the `webserver.persistant.token_path` value (default is
          `/app/var/lib/web_server/persist_token.json`).
        * After starting WATA at least once (`watastart`), view the contents of that token file on your server to get
          the actual token value (e.g., using `cat /app/var/lib/web_server/persist_token.json`). Replace
          `YOUR_SECRET_TOKEN` in the TradingView URL with this value.
    * In the "Message" box of the alert, provide the JSON payload WATA expects. Refer to
      the [Usage section in the README.md](../README.md#usage) for the exact format:
      ```json
      {
        "action": "long", // or "short", "close-position"
        "indice": "us100", // Matches an allowed indice in your config
        "signal_timestamp": "{{timenow}}", // TradingView variable for current time
        "alert_timestamp": "{{timenow}}"
      }
      ```
      *Ensure `indice` matches one configured in `allowed_indices` in your `config.json`.*
      *Use `"{{timenow}}"` for TradingView to insert the alert trigger time dynamically.*

## Step 6: Start Trading

1. **Start the Application**: Ensure all services are running:
   ```bash
   watastart
   watastatus # Check status
   ```
2. **Monitor**:
    * Keep an eye on your Telegram channel for notifications about received signals, trade executions, errors, and daily
      summaries.
    * Check logs if needed: `watalogs`.
3. **Reporting**: Use the [Reporting tools](../README.md#📈-reporting) to analyze performance over time.

You have now successfully set up WATA! It will listen for incoming webhook signals and attempt to execute trades on your
Saxo Bank account according to your configuration and rules. Remember to monitor the system closely, especially
initially. 