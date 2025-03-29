#!/bin/bash

# Set default password if not provided
RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD:-pykvys-9nixqo-cuqvYt}
CONFIG_FILE="/app/etc/config.json"

# Update the RabbitMQ password in config.json
if [[ -f "$CONFIG_FILE" ]]; then
  # Use jq to update the JSON file (install if needed: apt-get install -y jq)
  jq --arg pw "$RABBITMQ_PASSWORD" '.rabbitmq.authentication.password = $pw' "$CONFIG_FILE" > "$CONFIG_FILE.tmp"
  mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"
  echo "Updated RabbitMQ password in $CONFIG_FILE"
else
  echo "Error: Config file $CONFIG_FILE not found"
  exit 1
fi 