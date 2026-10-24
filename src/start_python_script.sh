#!/bin/bash

# Check if the environment variable is set
if [ -z "$WATA_APP_ROLE" ]; then
    echo "Environment variable WATA_APP_ROLE is not set."
    exit 1
fi

# Based on the environment variable value, decide which Python script to run.
# exec: Python takes this shell's place, so that it receives Docker's stop (SIGTERM). A shell
# left in between never passes it on, and Docker kills the service after 10 s with nothing
# done of its stopping code. The compose file's init: true then forwards it to Python.
case $WATA_APP_ROLE in
    "web_server")
        exec python -u src/web_server/__init__.py
        ;;
    "trader")
        exec python -u /app/src/trader/__init__.py
        ;;
    "position_monitor")
        exec python -u /app/src/position_monitor/__init__.py
        ;;
    "watchlist_manager")
        exec python -u /app/src/watchlist_manager/__init__.py
        ;;
    "scheduler")
        exec python -u /app/src/scheduler/__init__.py
        ;;
    "telegram")
        exec python -u /app/src/mq_telegram/__init__.py
        ;;
    "admin")
        exec python -u /app/src/admin/__init__.py
        ;;
    "price_recorder")
        exec python -u /app/src/price_recorder/__init__.py
        ;;
    "capital_feed")
        exec python -u /app/src/capital_feed/__init__.py
        ;;
    "ta_engine")
        exec python -u /app/src/ta_engine/__init__.py
        ;;
    "service_control")
        exec python -u /app/src/service_control/__init__.py
        ;;
    *)
        echo "Unknown environment variable value for WATA_APP_ROLE: $WATA_APP_ROLE"
        exit 1
        ;;
esac
