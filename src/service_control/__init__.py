"""
The control service: the one WATA container that holds Docker's socket, so
that the admin can restart WATA's services without holding it itself. It
reads no configuration and no database: only its token (a compose secret,
shared with the admin) and Docker. It listens on a network only the admin
joins (control-net).
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

TOKEN_FILE = os.getenv("WATA_CONTROL_TOKEN_FILE", "/run/secrets/service_control_token")
LOG_FILE = "/app/var/log/wata-service-control/wata-service-control.log"
PORT = 8090
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"


def read_token(path: str = TOKEN_FILE) -> str:
    with open(path, encoding="utf-8") as token_file:
        token = token_file.read().strip()
    if len(token) < 32:
        raise ValueError(f"The token in {path} is too short: at least 32 characters")
    return token


def setup_logging(path: str = LOG_FILE) -> None:
    from src.logging_helper import QUIET_LOGGERS
    from src.logging_helper.redaction import RedactingFilter

    os.makedirs(os.path.dirname(path), exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=2097152, backupCount=31)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


if __name__ == "__main__":
    import uvicorn

    from src.logging_helper.redaction import register_secret, uvicorn_log_config
    from src.service_control.app import create_app
    from src.service_control.docker import Docker

    setup_logging()
    try:
        token = read_token()
    except (OSError, ValueError) as e:
        logging.critical("No token to open the control service with: %s", e)
        sys.exit(1)
    register_secret("service_control.token", token)
    logging.info("--- Starting WATA service control ---")
    uvicorn.run(
        create_app(Docker(), token),
        host="0.0.0.0",
        port=PORT,
        log_config=uvicorn_log_config(),
        timeout_graceful_shutdown=5,
    )
