import logging
import os

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    import uvicorn

    from src.admin.app import SHUTDOWN_GRACE_SECONDS, create_app
    from src.admin.context import build_context
    from src.configuration import ConfigurationManager
    from src.logging_helper import setup_logging
    from src.logging_helper.redaction import uvicorn_log_config

    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        raise RuntimeError("WATA_CONFIG_PATH not set")

    config_manager = ConfigurationManager(config_path)
    setup_logging(config_manager, "wata-admin")

    ctx = build_context(config_manager)
    logger.info("--- Starting WATA Admin v%s ---", ctx.version)
    if not ctx.credentials.is_configured():
        logger.warning("No admin password is set: logins are refused until wataadminpasswd has been run")

    uvicorn.run(
        create_app(ctx),
        host=ctx.settings.api_host,
        port=ctx.settings.api_port,
        timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS,
        log_config=uvicorn_log_config(),
    )
