#!/bin/bash
set -euo pipefail

# The release installed is the folder this script is in (/app/wata/<version>/deploy/..): the playbook runs
# the new release's. Nothing is stopped: its images are built beside the ones that run, checked, and only
# then is it named the newest (/app/wata/VERSION). watadeploy then recreates, container by container, what
# it changes, when each one's guards allow (src/release/).
APP_BASE_DIR="/app/wata"
VERSION_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(cat "$VERSION_DIR/VERSION")"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: $VERSION_DIR/VERSION does not name a release: $VERSION" >&2
    exit 1
fi
if [ "$VERSION_DIR" != "$APP_BASE_DIR/$VERSION" ]; then
    echo "Error: the release $VERSION is installed from $APP_BASE_DIR/$VERSION, not from $VERSION_DIR" >&2
    exit 1
fi
# watadeploy runs on the server's own python3, with nothing WATA installs.
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "Error: watadeploy needs python3 3.10 or newer on the server" >&2
    exit 1
fi
# The deploy's commands, from the release being installed (by its folder: it is not the newest yet).
deploy() {
    (cd "$VERSION_DIR" && PYTHONPATH="$VERSION_DIR" python3 -m src.release.cli "$@")
}

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
# What the admin asks of a deploy (in its own folder), and the deploy's own: pins, history, status.
mkdir -p "$APP_BASE_DIR/var/lib/admin/deploy/requests"
mkdir -p "$APP_BASE_DIR/release/status" "$APP_BASE_DIR/release/manifests" "$APP_BASE_DIR/release/requests"

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
chmod 700 "$APP_BASE_DIR/release" "$APP_BASE_DIR/release/requests"
# The status alone is read by the admin (mounted read-only into it).
chmod 755 "$APP_BASE_DIR/release/status"

# The release's images, built beside the ones that run: nothing is stopped.
echo "Building the images of $VERSION..."
"$VERSION_DIR/docker_build.sh"

# Checked before any container runs on it: when it fails, the release is not named the newest and nothing
# changed. The checks of src/release/check.py, in the new image, on config.json as it is, without network.
echo "Checking wata-base:$VERSION..."
if ! deploy check-image "$VERSION"; then
    echo "Error: wata-base:$VERSION did not pass its checks: nothing was changed, the running release stays." >&2
    exit 1
fi

# The services' key pairs, which seal the credentials kept in PostgreSQL (src/vault/): the missing ones
# made by the image just built, none ever replaced (what is sealed for a key opens with it alone). Kept
# apart from var/lib, which five services mount whole, and owner-only: the playbook loosened /app/wata.
# Keep a copy of /app/wata/keys: without it, every credential must be entered again.
mkdir -p "$APP_BASE_DIR/keys"
docker run --rm -v "$APP_BASE_DIR/keys:/keys" "wata-base:$VERSION" python -m src.vault keygen --root /keys
chmod 700 "$APP_BASE_DIR/keys" "$APP_BASE_DIR/keys"/*
find "$APP_BASE_DIR/keys" -name private.key -exec chmod 600 {} +
find "$APP_BASE_DIR/keys/public" -name '*.pub' -exec chmod 644 {} +

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

# The newest release: the one whose compose files every command uses from now on. Each container keeps
# the image its pin names until watadeploy moves it.
printf '%s' "$VERSION" > "$APP_BASE_DIR/VERSION"
echo "$VERSION is the newest release installed."
# The pins of a server that has none yet: each running container pinned to the image it runs, so that
# switching to pins recreates nothing; what does not run yet, and the tools, on this release.
deploy bootstrap > /dev/null

# Create command wrappers
echo "Creating command wrappers..."

# The deploy itself (src/release/cli.py), from the newest release, on the server's own python3.
cat << 'EOF' > /usr/local/bin/watadeploy
#!/bin/bash
set -euo pipefail
# watadeploy status | plan | apply [--services a,b] [--force] | rollback [container...] | full | check-config | prune
APP_BASE="/app/wata"
VERSION_DIR="$APP_BASE/$(cat "$APP_BASE/VERSION")"
cd "$VERSION_DIR"
PYTHONPATH="$VERSION_DIR" exec python3 -m src.release.cli "$@"
EOF
chmod +x /usr/local/bin/watadeploy

# One of WATA's commands, run in a container of its own made from a service's (its mounts, its key, its
# networks) on the newest release's image: a command run with docker exec would run the image its service
# was last recreated on. Never a part of Traefik's routes: the webhook's and the admin's go to the services.
cat << 'EOF' > /usr/local/bin/watatool
#!/bin/bash
set -euo pipefail
# watatool <container> <command...>
APP_BASE="/app/wata"
VERSION="$(cat "$APP_BASE/VERSION")"
SERVICE="${1:?the container whose service the command runs in}"
shift
VARIABLE="WATA_IMAGE_$(printf '%s' "$SERVICE" | tr '[:lower:]' '[:upper:]' | tr -c 'A-Z0-9' '_')"
cd "$APP_BASE/$VERSION/deploy"
exec env "$VARIABLE=wata-base:$VERSION" docker compose --env-file "$APP_BASE/.env" \
    --env-file "$APP_BASE/release/images.env" run --rm --no-deps --label traefik.enable=false "$SERVICE" "$@"
EOF
chmod +x /usr/local/bin/watatool

# Create the watasaxoauth command wrapper
cat << 'EOF' > /usr/local/bin/watasaxoauth
#!/bin/bash
set -euo pipefail

exec watatool trader1 python -m src.saxo_authen.cli
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

exec watatool web_server1 python -m src.web_server.cli $OPTIONS
EOF
chmod +x /usr/local/bin/watawebtoken

# Create the wataadminpasswd command wrapper
cat << 'EOF' > /usr/local/bin/wataadminpasswd
#!/bin/bash
set -euo pipefail

exec watatool admin1 python -m src.admin.cli --set-password
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

exec watatool admin1 python -m src.admin.cli $OPTIONS
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

exec watatool admin1 python -m src.admin.cli --mcp-token "$@"
EOF
chmod +x /usr/local/bin/watamcptoken

# Create the watavault command wrapper (the credentials sealed in PostgreSQL, src/vault/)
cat << 'EOF' > /usr/local/bin/watavault
#!/bin/bash
set -euo pipefail
# watavault migrate --dry-run     what would be moved into PostgreSQL, and from where
# watavault migrate               moved in, every service stopped: only what has no row yet
# watavault migrate --purge-old   the old places cleared, once each credential has its row
# watavault export-saxo           Saxo's login written back to its file, to go back to v0.43
APP_BASE="/app/wata"
if [ "${1:-}" == "export-saxo" ]; then
    # In a container of the trader's: it can open Saxo's login, vault_tools cannot.
    exec watatool trader1 python -m src.vault export-saxo
fi
cd "$APP_BASE/$(cat "$APP_BASE/VERSION")/deploy"
exec docker compose --env-file "$APP_BASE/.env" --env-file "$APP_BASE/release/images.env" --profile tools run --rm vault_tools "$@"
EOF
chmod +x /usr/local/bin/watavault

# Create the wataprices command wrapper (price recorder: export a period, or check an instrument)
cat << 'EOF' > /usr/local/bin/wataprices
#!/bin/bash
set -euo pipefail
# wataprices --from 2026-10-12 --to 2026-10-16     export to /app/wata/var/lib/price_export/
# wataprices probe --keywords "US Tech 100"        does Saxo price this instrument for the account?
if [ "$#" -gt 0 ] && [ "$1" == "probe" ]; then
    shift
    exec watatool price_recorder1 python -m src.price_recorder.probe "$@"
else
    exec watatool price_recorder1 python -m src.price_recorder.export "$@"
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

# Create the watastart command wrapper: what is not running is started, on the image its pin names; a
# container that exists is never recreated (watadeploy does that, one container at a time).
cat << EOF > /usr/local/bin/watastart
#!/bin/bash
set -euo pipefail

APP_BASE="/app/wata"
CONFIG_FILE="\$APP_BASE/etc/config.json"
ENV_FILE="\$APP_BASE/.env"
PINS_FILE="\$APP_BASE/release/images.env"

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
docker compose --env-file="\$ENV_FILE" --env-file="\$PINS_FILE" up -d --no-recreate
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
docker compose --env-file="\$APP_BASE/.env" --env-file="\$APP_BASE/release/images.env" down
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

# Create the watastatus command wrapper: each container, the image (the release) it runs, its state
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
echo "Getting application status from \$PWD (newest release: \$(basename "\$_VERSION_DIR"); watadeploy status says what waits for it)..."
docker compose --env-file="\$APP_BASE/.env" --env-file="\$APP_BASE/release/images.env" ps --all \
    --format 'table {{.Name}}\t{{.Image}}\t{{.Status}}'
EOF
chmod +x /usr/local/bin/watastatus

# The admin's requests to apply a release (System page): it writes one into its own folder, and this path
# unit has watadeploy carry it out, with the guards of any apply. The admin never holds Docker or the pins.
cat << 'EOF' > /etc/systemd/system/watadeploy-requests.path
[Unit]
Description=WATA: the admin asks for a release to be applied

[Path]
DirectoryNotEmpty=/app/wata/var/lib/admin/deploy/requests
MakeDirectory=yes

[Install]
WantedBy=multi-user.target
EOF
cat << 'EOF' > /etc/systemd/system/watadeploy-requests.service
[Unit]
Description=WATA: apply what the admin asked
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/watadeploy serve-requests
EOF
# config.json changed (a save from the admin, an edit by hand): does every image in use still start on it?
cat << 'EOF' > /etc/systemd/system/watadeploy-config.path
[Unit]
Description=WATA: config.json changed

[Path]
PathChanged=/app/wata/etc/config.json

[Install]
WantedBy=multi-user.target
EOF
cat << 'EOF' > /etc/systemd/system/watadeploy-config.service
[Unit]
Description=WATA: does every image in use still start on config.json?
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/watadeploy check-config
SuccessExitStatus=1
EOF
systemctl daemon-reload
systemctl enable --now watadeploy-requests.path watadeploy-config.path

# Older releases' folders and images, keeping the newest three and every one a container still names.
watadeploy prune || echo "WARNING: the older releases could not all be removed (watadeploy prune)." >&2


echo ""
echo "---------------------------------------------------------------------"
echo "WATA $VERSION is installed: nothing was stopped."
echo "---------------------------------------------------------------------"
echo "Next Steps:"
echo "1. Ensure '$APP_BASE_DIR/.env' exists and is configured."
echo "2. Ensure '$APP_BASE_DIR/etc/config.json' exists and is configured."
echo "3. watadeploy plan     what $VERSION changes, container by container, and what may go now"
echo "   watadeploy apply    recreate what changed and may go now (the System page can ask for it too)"
echo ""
echo "You can manage the application using the following commands (available in new terminal sessions):"
echo "  - watastart:   Start what is not running (each container on the image its pin names)"
echo "  - watastop:    Stop the application"
echo "  - watastatus:  Show each container, its image and its state"
echo "  - watalogs:    Tail the application logs"
echo "  - watadeploy:  status, plan, apply, rollback, full restart, check-config, prune"
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
echo "To move the credentials into PostgreSQL, or clear their old places once they are there:"
echo "  - watavault migrate [--dry-run | --purge-old] | export-saxo"
echo "To let Claude Code on your Mac use the admin's MCP endpoint (when admin.mcp_enabled is true):"
echo "  - watamcptoken create <name> [--days N] | list | revoke <name>"
echo "---------------------------------------------------------------------"
