#!/bin/bash
cd /app/deploy/ && docker compose down && echo "The application is stopped"

mkdir -p /app/var/lib/duckdb
mkdir -p /app/var/lib/saxo_auth
mkdir -p /app/var/lib/web_server
mkdir -p /app/var/lib/rabbitmq
mkdir -p /app/var/lib/trade
mkdir -p /app/var/log/
mkdir -p /app/var/log/rabbitmq
mkdir -p /app/var/log/wata-api
mkdir -p /app/var/log/wata-trader
mkdir -p /app/var/log/wata-telegram
mkdir -p /app/var/log/wata-scheduler

touch /app/var/log/wata-api.log
touch /app/var/log/wata-trader.log
touch /app/var/log/wata-telegram.log
touch /app/var/log/wata-scheduler.log

sudo systemctl enable docker

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

# Allow all connections to the SSH server
sudo ufw allow ssh

# Loop through each IP in the ALLOWED_IPS array
for IP in "${ALLOWED_IPS[@]}"; do
    # Add a UFW rule to allow traffic on TCP port 80 from the current IP
    sudo ufw allow from $IP to any port 80 proto tcp
done

echo "UFW rules added successfully."

sudo ufw --force enable

chmod 755 /app/docker_build.sh
chmod 700 -R /app/var/lib/

#Build the docker image
/app/docker_build.sh

# Create the watasaxoauth command wrapper
cat << 'EOF' > /usr/local/bin/watasaxoauth
#!/bin/bash
if [ -z "$1" ]; then
    echo "Error: Authorization code is required"
    echo "Usage: watasaxoauth <AUTH_CODE>"
    exit 1
fi

docker exec trader1 python -m src.saxo_authen.cli "$1"
EOF

chmod +x /usr/local/bin/watasaxoauth

# Create the watawebtoken command wrapper
cat << 'EOF' > /usr/local/bin/watawebtoken
#!/bin/bash
OPTIONS=""
if [ "$1" == "--new" ]; then
    OPTIONS="--new"
fi

docker exec web_server1 python -m src.web_server.cli $OPTIONS
EOF

chmod +x /usr/local/bin/watawebtoken

cat << EOF > ~/.bash_aliases
alias watastart='if [ -f /app/etc/config.json ]; then cd /app/deploy/ && docker compose up -d && echo "The application is started, get status with watastatus"; else echo "Error: /app/etc/config.json not found. Please create the config file before starting."; fi'
alias watastop='cd /app/deploy/ && docker compose down && echo "The application is stopped"'
alias watalogs='tail -f -n 50 /app/var/log/*/*.log'
alias watastatus='cd /app/deploy/ && docker compose ps --all'

EOF

source ~/.bash_aliases

echo "Wata is ready to use! You can lunch it with watastart, show logs with watalogs, show status with watastatus, stop it with watastop"
echo "To authenticate with Saxo, use the watasaxoauth command with your authorization code: watasaxoauth <AUTH_CODE>"
echo "To view or generate a new WebServer token, use the watawebtoken command: watawebtoken [--new]"