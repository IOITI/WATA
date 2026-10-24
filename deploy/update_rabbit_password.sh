#!/bin/bash

# Set default password if not provided
RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD:-pykvys-9nixqo-cuqvYt}
WATA_CONFIG_FILE="/app/etc/config.json"

# Update the RabbitMQ password in config.json
if [[ -f "$WATA_CONFIG_FILE" ]]; then
  # Left as it is when it already holds the password: the admin tells a change of the file it did not make
  # (one made on the server) by the file's time, and a container still running keeps the file it started with.
  if [[ "$(jq -r '.rabbitmq.authentication.password // empty' "$WATA_CONFIG_FILE")" == "$RABBITMQ_PASSWORD" ]]; then
    echo "The RabbitMQ password in $WATA_CONFIG_FILE is already the one given"
    exit 0
  fi
  # Use jq to update the JSON file (install if needed: apt-get install -y jq)
  jq --arg pw "$RABBITMQ_PASSWORD" '.rabbitmq.authentication.password = $pw' "$WATA_CONFIG_FILE" > "$WATA_CONFIG_FILE.tmp"
  mv "$WATA_CONFIG_FILE.tmp" "$WATA_CONFIG_FILE"
  echo "Updated RabbitMQ password in $WATA_CONFIG_FILE"
else
  echo "Error: Config file $WATA_CONFIG_FILE not found"
  exit 1
fi 