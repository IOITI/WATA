#!/bin/bash
set -euo pipefail

# Define base directory and version file path
APP_BASE_DIR="/app/wata"
VERSION_FILE="$APP_BASE_DIR/VERSION"

# Check if VERSION file exists
if [ ! -f "$VERSION_FILE" ]; then
    echo "Error: Version file not found at $VERSION_FILE" >&2
    exit 1
fi

# Get the version from the VERSION file
VERSION=$(cat "$VERSION_FILE")
VERSION_DIR="$APP_BASE_DIR/$VERSION"

# Stop existing application if running
if [ -d "$VERSION_DIR/deploy" ]; then
    # Only attempt to cd if the directory exists to avoid errors with set -e
    if cd "$VERSION_DIR/deploy/"; then
        echo "Stopping application in $PWD..."
        docker compose down && echo "The application is stopped."
        cd - > /dev/null # Go back to previous directory
    else
        echo "Warning: Could not cd to $VERSION_DIR/deploy/, skipping docker compose down." >&2
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
mkdir -p "$APP_BASE_DIR/var/lib/admin"
mkdir -p "$APP_BASE_DIR/var/lib/admin/ta"
mkdir -p "$APP_BASE_DIR/var/lib/price_export"
mkdir -p "$APP_BASE_DIR/var/lib/price_history"
mkdir -p "$APP_BASE_DIR/var/lib/price_history/capital"
mkdir -p "$APP_BASE_DIR/var/lib/backtests"
mkdir -p "$APP_BASE_DIR/var/lib/backups"
mkdir -p "$APP_BASE_DIR/var/lib/traefik"
mkdir -p "$APP_BASE_DIR/var/lib/traefik/letsencrypt"
mkdir -p "$APP_BASE_DIR/var/log/"
mkdir -p "$APP_BASE_DIR/var/log/rabbitmq"
mkdir -p "$APP_BASE_DIR/var/log/wata-api"
mkdir -p "$APP_BASE_DIR/var/log/wata-trader"
mkdir -p "$APP_BASE_DIR/var/log/wata-telegram"
mkdir -p "$APP_BASE_DIR/var/log/wata-scheduler"
mkdir -p "$APP_BASE_DIR/var/log/wata-admin"
mkdir -p "$APP_BASE_DIR/var/log/wata-price-recorder"
mkdir -p "$APP_BASE_DIR/var/log/wata-capital-feed"
mkdir -p "$APP_BASE_DIR/var/log/wata-ta-engine"
mkdir -p "$APP_BASE_DIR/var/log/wata-service-control"
mkdir -p "$APP_BASE_DIR/var/lib/service_control"

# Ensure docker service is enabled
echo "Ensuring Docker service is enabled..."
sudo systemctl enable docker

# The port Traefik is published on for HTTP or HTTPS: HTTP_PORT or HTTPS_PORT in .env,
# read without running the file, or the default when it does not set one.
env_port() {
    local name="$1" default="$2" value=""
    if [ -f "$APP_BASE_DIR/.env" ]; then
        value=$(sed -n "s/^[[:space:]]*$name=[\"']\{0,1\}\([^\"'[:space:]]*\)[\"']\{0,1\}[[:space:]]*$/\1/p" "$APP_BASE_DIR/.env" | tail -n 1)
    fi
    if [ -z "$value" ]; then
        echo "$default"
    elif [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -ge 1 ] && [ "$value" -le 65535 ]; then
        echo "$value"
    else
        echo "WARNING: $name=$value in $APP_BASE_DIR/.env is not a port number (see .env.example): the firewall opens $default instead." >&2
        echo "$default"
    fi
}
HTTP_PORT=$(env_port HTTP_PORT 80)
HTTPS_PORT=$(env_port HTTPS_PORT 443)

# Configure UFW firewall rules
echo "Configuring UFW rules..."
# Allow all connections to the SSH server
sudo ufw allow ssh
sudo ufw allow "$HTTP_PORT/tcp"
sudo ufw allow "$HTTPS_PORT/tcp"

echo "UFW rules added successfully."
sudo ufw --force enable

# Reload UFW to apply changes
sudo ufw reload

# The token the admin opens the control service with (a compose secret of both): made at the
# first install, never overwritten, owner-only (the playbook loosens /app/wata at each deploy).
CONTROL_TOKEN="$APP_BASE_DIR/var/lib/service_control/token"
if [ ! -s "$CONTROL_TOKEN" ]; then
    (umask 077 && openssl rand -hex 32 > "$CONTROL_TOKEN")
    echo "Made the control service's token."
fi

# Set permissions
echo "Setting permissions..."
chmod 755 "$VERSION_DIR/docker_build.sh"
chmod 700 -R "$APP_BASE_DIR/var/lib/"
chmod 600 "$CONTROL_TOKEN"

# Build the docker image
echo "Building Docker image..."
if [ -f "$VERSION_DIR/docker_build.sh" ]; then
    "$VERSION_DIR/docker_build.sh"
else
    echo "Error: Docker build script not found at $VERSION_DIR/docker_build.sh" >&2
    exit 1
fi

# The services' key pairs, which seal the credentials kept in PostgreSQL (src/vault/): the missing ones
# made by the image just built, none ever replaced (what is sealed for a key opens with it alone). Kept
# apart from var/lib, which five services mount whole, and owner-only: the playbook loosened /app/wata.
# Keep a copy of /app/wata/keys: without it, every credential must be entered again.
mkdir -p "$APP_BASE_DIR/keys"
docker run --rm -v "$APP_BASE_DIR/keys:/keys" wata-base:latest python -m src.vault keygen --root /keys
chmod 700 "$APP_BASE_DIR/keys" "$APP_BASE_DIR/keys"/*
find "$APP_BASE_DIR/keys" -name private.key -exec chmod 600 {} +
find "$APP_BASE_DIR/keys/public" -name '*.pub' -exec chmod 644 {} +


# Create command wrappers
echo "Creating command wrappers..."

# Create the watasaxoauth command wrapper
cat << 'EOF' > /usr/local/bin/watasaxoauth
#!/bin/bash
set -euo pipefail

docker exec -it trader1 python -m src.saxo_authen.cli
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

# Create the wataadminpasswd command wrapper
cat << 'EOF' > /usr/local/bin/wataadminpasswd
#!/bin/bash
set -euo pipefail

docker exec -it admin1 python -m src.admin.cli --set-password
EOF
chmod +x /usr/local/bin/wataadminpasswd

# Create the wataadmintotp command wrapper (second factor of the admin login)
cat << 'EOF' > /usr/local/bin/wataadmintotp
#!/bin/bash
set -euo pipefail
OPTIONS="--enable-totp"
if [ "$#" -gt 0 ] && [ "$1" == "--disable" ]; then
    OPTIONS="--disable-totp"
fi

docker exec -it admin1 python -m src.admin.cli $OPTIONS
EOF
chmod +x /usr/local/bin/wataadmintotp

# Create the watamcptoken command wrapper (tokens of the admin's MCP endpoint for Claude)
cat << 'EOF' > /usr/local/bin/watamcptoken
#!/bin/bash
set -euo pipefail
# watamcptoken create claude-mac [--days 90]   a new token, printed once
# watamcptoken list                            the tokens, never their value
# watamcptoken revoke claude-mac               no longer opens the endpoint
if [ "$#" -eq 0 ]; then
    set -- list
fi

docker exec -it admin1 python -m src.admin.cli --mcp-token "$@"
EOF
chmod +x /usr/local/bin/watamcptoken

# Create the wataprices command wrapper (price recorder: export a period, or check an instrument)
cat << 'EOF' > /usr/local/bin/wataprices
#!/bin/bash
set -euo pipefail
# wataprices --from 2026-10-12 --to 2026-10-16     export to /app/wata/var/lib/price_export/
# wataprices probe --keywords "US Tech 100"        does Saxo price this instrument for the account?
if [ "$#" -gt 0 ] && [ "$1" == "probe" ]; then
    shift
    docker exec price_recorder1 python -m src.price_recorder.probe "$@"
else
    docker exec price_recorder1 python -m src.price_recorder.export "$@"
fi
EOF
chmod +x /usr/local/bin/wataprices

# --- Helper function definition for other scripts ---
# Note: This is a string that will be embedded into other scripts.
# It needs careful escaping if it contains ' or backticks itself.
# For simple cases, 'EOF' quoting is fine.
WATA_GET_VERSION_DIR_FUNC='
wata_get_version_dir() {
    # APP_BASE must be defined in the script calling this function
    local current_version_file="$APP_BASE/VERSION"
    if [ ! -f "$current_version_file" ]; then
        echo "Error: Version file not found at $current_version_file" >&2
        return 1
    fi
    local version_content
    version_content=$(cat "$current_version_file" 2>/dev/null)
    if [ -z "$version_content" ]; then
        echo "Error: Could not determine current version (empty or unreadable) from $current_version_file" >&2
        return 1
    fi
    echo "$APP_BASE/$version_content"
    return 0
}
'

# Create the watastart command wrapper
cat << EOF > /usr/local/bin/watastart
#!/bin/bash
set -euo pipefail

APP_BASE="/app/wata"
CONFIG_FILE="\$APP_BASE/etc/config.json"
ENV_FILE="\$APP_BASE/.env"

$WATA_GET_VERSION_DIR_FUNC

if [ ! -f "\$CONFIG_FILE" ]; then
    echo "Error: \$CONFIG_FILE not found. Please create the config file before starting." >&2
    exit 1
fi

# Call the function and capture its output
_VERSION_DIR=\$(wata_get_version_dir)
# Check the exit status of the function call
if [ \$? -ne 0 ] || [ -z "\$_VERSION_DIR" ]; then
    # Error message already printed by wata_get_version_dir
    exit 1
fi

DEPLOY_DIR="\$_VERSION_DIR/deploy"

if [ ! -d "\$DEPLOY_DIR" ]; then
    echo "Error: Deployment directory \$DEPLOY_DIR not found." >&2
    exit 1
fi

cd "\$DEPLOY_DIR"
echo "Starting application in \$PWD (version: \$(basename "\$_VERSION_DIR"))..."
docker compose --env-file="\$ENV_FILE" up -d
echo "The application is started. Get status with watastatus."
EOF
chmod +x /usr/local/bin/watastart

# Create the watastop command wrapper
cat << EOF > /usr/local/bin/watastop
#!/bin/bash
set -euo pipefail

APP_BASE="/app/wata"

$WATA_GET_VERSION_DIR_FUNC

_VERSION_DIR=\$(wata_get_version_dir)
if [ \$? -ne 0 ] || [ -z "\$_VERSION_DIR" ]; then
    exit 1
fi

DEPLOY_DIR="\$_VERSION_DIR/deploy"

if [ ! -d "\$DEPLOY_DIR" ]; then
    echo "Error: Deployment directory \$DEPLOY_DIR not found for version \$(basename "\$_VERSION_DIR")." >&2
    exit 1
fi

cd "\$DEPLOY_DIR"
echo "Stopping application in \$PWD (version: \$(basename "\$_VERSION_DIR"))..."
docker compose down
echo "The application is stopped."
EOF
chmod +x /usr/local/bin/watastop

# Create the watalogs command wrapper
cat << 'EOF' > /usr/local/bin/watalogs
#!/bin/bash
set -euo pipefail
LOG_DIR="/app/wata/var/log"

# Enable nullglob to make globs expand to nothing if no matches
shopt -s nullglob
log_files=("$LOG_DIR"/*.log "$LOG_DIR"/*/*.log)
shopt -u nullglob # Disable nullglob afterward

if [ ${#log_files[@]} -eq 0 ]; then
    echo "No log files found in $LOG_DIR matching *.log or */*.log" >&2
    exit 1
fi

echo "Tailing logs: ${log_files[*]}"
# Using "${log_files[@]}" ensures that if a filename contains spaces, it's treated as a single argument.
tail -f -n 50 "${log_files[@]}"
EOF
chmod +x /usr/local/bin/watalogs

# Create the watastatus command wrapper
cat << EOF > /usr/local/bin/watastatus
#!/bin/bash
set -euo pipefail

APP_BASE="/app/wata"

$WATA_GET_VERSION_DIR_FUNC

_VERSION_DIR=\$(wata_get_version_dir)
if [ \$? -ne 0 ] || [ -z "\$_VERSION_DIR" ]; then
    exit 1
fi

DEPLOY_DIR="\$_VERSION_DIR/deploy"

if [ ! -d "\$DEPLOY_DIR" ]; then
    echo "Error: Deployment directory \$DEPLOY_DIR not found for version \$(basename "\$_VERSION_DIR")." >&2
    exit 1
fi

cd "\$DEPLOY_DIR"
echo "Getting application status from \$PWD (version: \$(basename "\$_VERSION_DIR"))..."
docker compose ps --all
EOF
chmod +x /usr/local/bin/watastatus

# Link .env file
echo "Checking for .env file..."
DEPLOY_ENV_FILE="$VERSION_DIR/deploy/.env"
ROOT_ENV_FILE="$APP_BASE_DIR/.env"

if [ ! -L "$DEPLOY_ENV_FILE" ] || [ "$(readlink -f "$DEPLOY_ENV_FILE")" != "$(readlink -f "$ROOT_ENV_FILE")" ]; then
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
echo "You can manage the application using the following commands (available in new terminal sessions):"
echo "  - watastart:   Start the application"
echo "  - watastop:    Stop the application"
echo "  - watastatus:  Show the status of application containers"
echo "  - watalogs:    Tail the application logs"
echo ""
echo "To authenticate with Saxo (or from the admin's System page):"
echo "  - watasaxoauth"
echo ""
echo "To manage the WebServer token:"
echo "  - watawebtoken [--new]"
echo ""
echo "To set the password of the admin web interface (when enabled in .env):"
echo "  - wataadminpasswd"
echo "To require a one-time code from an authenticator app as well:"
echo "  - wataadmintotp [--disable]"
echo "To let Claude Code on your Mac use the admin's MCP endpoint (when admin.mcp_enabled is true):"
echo "  - watamcptoken create <name> [--days N] | list | revoke <name>"
echo "---------------------------------------------------------------------"