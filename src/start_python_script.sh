#!/bin/bash

# Check if the environment variable is set
if [ -z "$WATA_APP_ROLE" ]; then
    echo "Environment variable WATA_APP_ROLE is not set."
    exit 1
fi

# Based on the environment variable value, decide which Python script to run
case $WATA_APP_ROLE in
    "web_server")
        python -u src/web_server/__init__.py
        ;;
    "trader")
        python -u /app/src/main.py
        ;;
    "scheduler")
        python -u /app/src/scheduler/__init__.py
        ;;
    "telegram")
        python -u /app/src/mq_telegram/__init__.py
        ;;
    *)
        echo "Unknown environment variable value for WATA_APP_ROLE: $WATA_APP_ROLE"
        exit 1
        ;;
esac
