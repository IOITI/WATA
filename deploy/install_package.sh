#!/bin/bash
set -euo pipefail

# Define base directory and version file path
APP_BASE_DIR="/app/wata"
VERSION_FILE="$APP_BASE_DIR/VERSION"

# Check if VERSION file exists
if [ ! -f "$VERSION_FILE" ]; then
    echo "Error: Version file not found at $VERSION_FILE"
    exit 1
fi

# Get the version from the VERSION file
VERSION=$(cat "$VERSION_FILE")
VERSION_DIR="$APP_BASE_DIR/$VERSION"

# Stop existing application if running
if [ -d "$VERSION_DIR/deploy" ]; then
    # Only attempt to cd if the directory exists to avoid errors with set -e
    if cd "$VERSION_DIR/deploy/"; then
        docker compose down && echo "The application is stopped"
        cd - > /dev/null # Go back to previous directory
    else
        echo "Warning: Could not cd to $VERSION_DIR/deploy/, skipping docker compose down."
    fi
else
    echo "Version directory $VERSION_DIR/deploy not found, skipping docker compose down."
fi


# Create necessary directories
echo "Creating required directories..."
mkdir -p "$APP_BASE_DIR/var/lib/duckdb"
mkdir -p "$APP_BASE_DIR/var/lib/saxo_auth"
mkdir -p "$APP_BASE_DIR/var/lib/web_server"
mkdir -p "$APP_BASE_DIR/var/lib/rabbitmq"
mkdir -p "$APP_BASE_DIR/var/lib/trade"
mkdir -p "$APP_BASE_DIR/var/log/"
mkdir -p "$APP_BASE_DIR/var/log/rabbitmq"
mkdir -p "$APP_BASE_DIR/var/log/wata-api"
mkdir -p "$APP_BASE_DIR/var/log/wata-trader"
mkdir -p "$APP_BASE_DIR/var/log/wata-telegram"
mkdir -p "$APP_BASE_DIR/var/log/wata-scheduler"

# Create log files
echo "Touching log files..."
touch "$APP_BASE_DIR/var/log/wata-api.log"
touch "$APP_BASE_DIR/var/log/wata-trader.log"
touch "$APP_BASE_DIR/var/log/wata-telegram.log"
touch "$APP_BASE_DIR/var/log/wata-scheduler.log"

# Ensure docker service is enabled
echo "Ensuring Docker service is enabled..."
sudo systemctl enable docker

# Configure UFW firewall rules
# Define the list of allowed IPs for UFW
# https://www.tradingview.com/support/solutions/43000529348-about-webhooks/
# IP addresses of TradingView webhook servers
ALLOWED_IPS=(
    "127.0.0.1"
    "52.89.214.238"
    "34.212.75.30"
    "54.218.53.128"
    "52.32.178.7"
)

echo "Configuring UFW rules..."
# Allow all connections to the SSH server
sudo ufw allow ssh

# Loop through each IP in the ALLOWED_IPS array
for IP in "${ALLOWED_IPS[@]}"; do
    # Add a UFW rule to allow traffic on TCP port 80 from the current IP
    echo "Allowing traffic from $IP on port 80..."
    sudo ufw allow from "$IP" to any port 80 proto tcp
done

echo "UFW rules added successfully."
sudo ufw --force enable

# Set permissions
echo "Setting permissions..."
chmod 755 "$VERSION_DIR/docker_build.sh"
chmod 700 -R "$APP_BASE_DIR/var/lib/"

# Build the docker image
echo "Building Docker image..."
if [ -f "$VERSION_DIR/docker_build.sh" ]; then
    "$VERSION_DIR/docker_build.sh"
else
    echo "Error: Docker build script not found at $VERSION_DIR/docker_build.sh"
    exit 1
fi


# Create command wrappers
echo "Creating command wrappers..."

# Create the watasaxoauth command wrapper
cat << 'EOF' > /usr/local/bin/watasaxoauth
#!/bin/bash
set -euo pipefail
if [ -z "$1" ]; then
    echo "Error: Authorization code is required" >&2
    echo "Usage: watasaxoauth <AUTH_CODE>" >&2
    exit 1
fi

docker exec trader1 python -m src.saxo_authen.cli "$1"
EOF
chmod +x /usr/local/bin/watasaxoauth

# Create the watawebtoken command wrapper
cat << 'EOF' > /usr/local/bin/watawebtoken
#!/bin/bash
set -euo pipefail
OPTIONS=""
if [ "$#" -gt 0 ] && [ "$1" == "--new" ]; then
    OPTIONS="--new"
fi

docker exec web_server1 python -m src.web_server.cli $OPTIONS
EOF
chmod +x /usr/local/bin/watawebtoken

# Create the watastart command wrapper
cat << 'EOF' > /usr/local/bin/watastart
#!/bin/bash
set -euo pipefail

APP_BASE="/app/wata"
CONFIG_FILE="$APP_BASE/etc/config.json"
ENV_FILE="$APP_BASE/.env" # Define ENV_FILE here

wata_get_version_dir() {
    local app_base_func="/app/wata" # Use a different name to avoid conflict or just use APP_BASE
    local version_file_func="$app_base_func/VERSION"
    if [ ! -f "$version_file_func" ]; then
        echo "Error: Version file not found at $version_file_func" >&2
        return 1
    fi
    local version
    version=$(cat "$version_file_func" 2>/dev/null)
    if [ -z "$version" ]; then
        echo "Error: Could not determine current version from $version_file_func" >&2
        return 1
    fi
    echo "$app_base_func/$version"
    return 0
}

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: $CONFIG_FILE not found. Please create the config file before starting." >&2
    exit 1
fi

VERSION_DIR=$(wata_get_version_dir)
if [ -z "$VERSION_DIR" ]; then
    # Error message already printed by wata_get_version_dir
    exit 1
fi

DEPLOY_DIR="$VERSION_DIR/deploy"

if [ ! -d "$DEPLOY_DIR" ]; then
    echo "Error: Deployment directory $DEPLOY_DIR not found." >&2
    exit 1
fi

cd "$DEPLOY_DIR"
echo "Starting application in $DEPLOY_DIR..."
docker compose --env-file="$ENV_FILE" up -d
echo "The application is started. Get status with watastatus."
EOF
chmod +x /usr/local/bin/watastart

# Create the watastop command wrapper
cat << 'EOF' > /usr/local/bin/watastop
#!/bin/bash
set -euo pipefail

APP_BASE="/app/wata" # Define APP_BASE for consistency

wata_get_version_dir() {
    local app_base_func="/app/wata"
    local version_file_func="$app_base_func/VERSION"
    if [ ! -f "$version_file_func" ]; then
        echo "Error: Version file not found at $version_file_func" >&2
        return 1
    fi
    local version
    version=$(cat "$version_file_func" 2>/dev/null)
    if [ -z "$version" ]; then
        echo "Error: Could not determine current version from $version_file_func" >&2
        return 1
    fi
    echo "$app_base_func/$version"
    return 0
}

VERSION_DIR=$(wata_get_version_dir)
if [ -z "$VERSION_DIR" ]; then
    exit 1
fi

DEPLOY_DIR="$VERSION_DIR/deploy"

if [ ! -d "$DEPLOY_DIR" ]; then
    echo "Error: Deployment directory $DEPLOY_DIR not found." >&2
    exit 1
fi

cd "$DEPLOY_DIR"
echo "Stopping application in $DEPLOY_DIR..."
docker compose down
echo "The application is stopped."
EOF
chmod +x /usr/local/bin/watastop

# Create the watalogs command wrapper
cat << 'EOF' > /usr/local/bin/watalogs
#!/bin/bash
set -euo pipefail
LOG_DIR="/app/wata/var/log"
# Check if any log files exist to avoid error with tail if glob doesn't match
if ! ls "$LOG_DIR"/*/*.log &> /dev/null; then
    echo "No log files found in $LOG_DIR/*/*.log" >&2
    exit 1
fi
tail -f -n 50 "$LOG_DIR"/*/*.log
EOF
chmod +x /usr/local/bin/watalogs

# Create the watastatus command wrapper
cat << 'EOF' > /usr/local/bin/watastatus
#!/bin/bash
set -euo pipefail

APP_BASE="/app/wata" # Define APP_BASE for consistency

wata_get_version_dir() {
    local app_base_func="/app/wata"
    local version_file_func="$app_base_func/VERSION"
    if [ ! -f "$version_file_func" ]; then
        echo "Error: Version file not found at $version_file_func" >&2
        return 1
    fi
    local version
    version=$(cat "$version_file_func" 2>/dev/null)
    if [ -z "$version" ]; then
        echo "Error: Could not determine current version from $version_file_func" >&2
        return 1
    fi
    echo "$app_base_func/$version"
    return 0
}

VERSION_DIR=$(wata_get_version_dir)
if [ -z "$VERSION_DIR" ]; then
    exit 1
fi

DEPLOY_DIR="$VERSION_DIR/deploy"

if [ ! -d "$DEPLOY_DIR" ]; then
    echo "Error: Deployment directory $DEPLOY_DIR not found." >&2
    exit 1
fi

cd "$DEPLOY_DIR"
echo "Getting application status from $DEPLOY_DIR..."
docker compose ps --all
EOF
chmod +x /usr/local/bin/watastatus

# Link .env file
echo "Checking for .env file..."
DEPLOY_ENV_FILE="$VERSION_DIR/deploy/.env"
ROOT_ENV_FILE="$APP_BASE_DIR/.env"

if [ ! -L "$DEPLOY_ENV_FILE" ] || [ "$(readlink "$DEPLOY_ENV_FILE")" != "$ROOT_ENV_FILE" ]; then
    if [ -f "$ROOT_ENV_FILE" ]; then
        echo "Linking $ROOT_ENV_FILE to $DEPLOY_ENV_FILE..."
        ln -sf "$ROOT_ENV_FILE" "$DEPLOY_ENV_FILE"
    else
        echo "WARNING: $ROOT_ENV_FILE not found. Please create the .env file before starting." >&2
    fi
else
     echo "$DEPLOY_ENV_FILE already linked correctly."
fi


echo ""
echo "---------------------------------------------------------------------"
echo "WATA installation/update complete!"
echo "---------------------------------------------------------------------"
echo "Next Steps:"
echo "1. Ensure '$APP_BASE_DIR/.env' exists and is configured."
echo "2. Ensure '$APP_BASE_DIR/etc/config.json' exists and is configured."
echo ""
echo "You can manage the application using the following commands:"
echo "  - watastart:   Start the application"
echo "  - watastop:    Stop the application"
echo "  - watastatus:  Show the status of application containers"
echo "  - watalogs:    Tail the application logs"
echo ""
echo "To authenticate with Saxo:"
echo "  - watasaxoauth <AUTH_CODE>"
echo ""
echo "To manage the WebServer token:"
echo "  - watawebtoken [--new]"
echo "---------------------------------------------------------------------"