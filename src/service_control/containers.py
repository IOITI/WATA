"""
The containers the control service may restart: WATA's own, by the id of
each service's health entry. Never PostgreSQL, RabbitMQ, Traefik, the
backups or itself: none of them reads config.json, and restarting one from a
web page would stop everything at once. tests/test_service_control.py holds
this list to the compose file.
"""

CONTAINERS = {
    "web_server": "web_server1",
    "trader": "trader1",
    "position_monitor": "position_monitor1",
    "scheduler": "scheduler1",
    "telegram": "telegram1",
    "watchlist_manager": "watchlist_manager1",
    "admin": "admin1",
    "price_recorder": "price_recorder1",
    "capital_feed": "capital_feed1",
    "ta_engine": "ta_engine1",
}

# What trades, or decides when to: restarted at most once in RESTART_GAP_SECONDS and
# MAX_RESTARTS_AN_HOUR times an hour, whatever asks. A loop of restarts of the position
# monitor is a loop of minutes without exits.
TRADING_PATH = frozenset({"trader", "position_monitor", "watchlist_manager", "web_server", "scheduler"})
RESTART_GAP_SECONDS = 300
MAX_RESTARTS_AN_HOUR = 3
# The others too, but more freely.
OTHER_MAX_RESTARTS_AN_HOUR = 12
