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
    cd "$VERSION_DIR/deploy/" && docker compose down && echo "The application is stopped"
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
if [ "$1" == "--new" ]; then
    OPTIONS="--new"
fi

docker exec web_server1 python -m src.web_server.cli $OPTIONS
EOF
chmod +x /usr/local/bin/watawebtoken

# Create Bash aliases
echo "Setting up Bash aliases..."
BASH_ALIASES_FILE="$HOME/.bash_aliases"
MARKER="# WATA Aliases - Do not modify this line"

# Check if aliases already exist
if ! grep -q "$MARKER" "$BASH_ALIASES_FILE" 2>/dev/null; then
    echo "Adding Wata aliases to $BASH_ALIASES_FILE..."
    cat << 'EOF' >> "$BASH_ALIASES_FILE"

# WATA Aliases - Do not modify this line
# Dynamic version path resolution
wata_get_version_dir() {
    local app_base="/app/wata"
    local version=$(cat "$app_base/VERSION" 2>/dev/null)
    if [ -z "$version" ]; then
        echo "Error: Could not determine current version" >&2
        return 1
    fi
    echo "$app_base/$version"
}

alias watastart='app_base="/app/wata"; if [ -f "$app_base/etc/config.json" ]; then version_dir=$(wata_get_version_dir) && cd "$version_dir/deploy/" && docker compose --env-file="$app_base/.env" up -d && echo "The application is started, get status with watastatus"; else echo "Error: $app_base/etc/config.json not found. Please create the config file before starting." >&2; fi'
alias watastop='version_dir=$(wata_get_version_dir) && cd "$version_dir/deploy/" && docker compose down && echo "The application is stopped"'
alias watalogs='tail -f -n 50 "/app/wata/var/log/*/*.log"'
alias watastatus='version_dir=$(wata_get_version_dir) && cd "$version_dir/deploy/" && docker compose ps --all'
# End WATA Aliases
EOF
    echo "Aliases added. Please run 'source ~/.bash_aliases' or open a new terminal for changes to take effect."
else
    echo "Wata aliases already exist in $BASH_ALIASES_FILE. Skipping alias creation."
fi


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
echo "1. If aliases were added/updated, run: source ~/.bash_aliases"
echo "   (Or open a new terminal session)"
echo "2. Ensure '$APP_BASE_DIR/.env' exists and is configured."
echo "3. Ensure '$APP_BASE_DIR/etc/config.json' exists and is configured."
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