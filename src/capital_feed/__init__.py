"""
Capital.com feed: the Nasdaq 100 (and any other instrument named) streamed
from Capital.com, recorded as ten-second bars, sent live to the admin's charts,
and kept as one file a day for backtests.

Opt-in (compose profile ``capital`` and ``trade.config.capital_feed.enabled``)
and on its own: no trading service calls it or waits for it. Its login is the
credential ``capital.login``, sealed for this service alone in PostgreSQL
(src/vault/), read at its start; until it has its row, the environment of its
container (``CAPITAL_API_KEY``, ``CAPITAL_IDENTIFIER``, ``CAPITAL_PASSWORD``
in ``.env``). Never the configuration file.
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone

from src import event_loop

logger = logging.getLogger(__name__)

# How often an idle feed says again why it is idle: the admin takes a state row older than a few minutes for a feed that is down.
IDLE_STATE_EVERY_SECONDS = 60.0


def get_version() -> str:
    try:
        version_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VERSION")
        with open(version_file, "r") as file_handle:
            return file_handle.read().strip()
    except Exception:
        return "unknown"


async def _idle(store, version: str, environment: str | None, state: str, detail: str) -> None:
    """
    Stay up doing nothing but saying why: exiting would only make Docker
    restart the container in a loop.
    """
    logger.warning("%s The feed stays idle.", detail)
    started_at = datetime.now(timezone.utc)
    while True:
        try:
            await store.write_state({
                "updated_at": datetime.now(timezone.utc), "started_at": started_at, "version": version,
                "environment": environment, "state": state, "detail": detail, "figures": {},
            })
        except Exception as e:
            logger.debug("The feed's state could not be written: %s", e)
        await asyncio.sleep(IDLE_STATE_EVERY_SECONDS)


def _rabbitmq_url(config_manager) -> str:
    rabbitmq = config_manager.get_rabbitmq_config()
    authentication = rabbitmq["authentication"]
    return f"amqp://{authentication['username']}:{authentication['password']}@{rabbitmq['hostname']}/"


async def main():
    from src.capital_feed.client import CapitalRest, CapitalStream
    from src.capital_feed.day_files import DayFiles
    from src.capital_feed.live import LivePublisher, RabbitExchange
    from src.capital_feed.recorder import CapitalRecorder
    from src.capital_feed.settings import SECTION, CapitalSettings, Credentials
    from src.configuration import ConfigurationManager
    from src.database.postgres import AsyncDbCapitalManager, PostgresConnectionManager, init_capital_schema
    from src.logging_helper import setup_logging
    from src.mq_telegram.async_tools import AsyncTelegramSender
    from src.vault import loader as vault

    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        print("FATAL: WATA_CONFIG_PATH not set", file=sys.stderr)
        sys.exit(10)

    config_manager = ConfigurationManager(config_path)
    setup_logging(config_manager, "wata-capital-feed")
    version = get_version()
    logger.info("--- Starting WATA Capital.com Feed v%s ---", version)

    telegram = None
    pg = None
    rest = None
    exchange = None
    tasks: list[asyncio.Task] = []
    try:
        postgres_config = config_manager.get_config_value("postgresql", {})
        # Its own small pool: one writer, and room for the upkeep beside it.
        pg = PostgresConnectionManager(
            dsn=postgres_config.get("dsn", "postgresql://wata:wata@localhost:5432/wata"), min_size=1, max_size=2,
        )
        await pg.connect()
        await init_capital_schema(pg)
        store = AsyncDbCapitalManager(pg)

        try:
            settings = CapitalSettings.from_config(config_manager)
        except ValueError as e:
            await _idle(store, version, None, "invalid_config", f"Invalid configuration of the Capital.com feed: {e}.")
            return
        if not settings.enabled:
            await _idle(store, version, settings.environment, "disabled", f"The Capital.com feed is switched off ({SECTION}.enabled).")
            return
        await vault.load(config_manager, "capital_feed", db=pg)
        credentials = Credentials.from_value(vault.credential("capital.login"))
        if credentials is None:
            why = (
                "its row in PostgreSQL cannot be opened by this service (see its log)"
                if vault.source("capital.login") == vault.UNREADABLE else "set it from the admin's Credentials"
            )
            await _idle(store, version, settings.environment, "no_credentials", f"No Capital.com login: {why}.")
            return

        telegram = AsyncTelegramSender(config_manager)
        exchange = RabbitExchange(_rabbitmq_url(config_manager))
        publisher = LivePublisher(exchange.open)
        rest = CapitalRest(settings.rest_url, credentials)
        recorder = CapitalRecorder(
            settings=settings,
            store=store,
            rest=rest,
            publisher=publisher,
            notify=telegram.send,
            day_files=DayFiles(settings.prices_root, store) if settings.day_files else None,
            version=version,
            secrets=credentials.secrets(),
        )
        stream = CapitalStream(rest, settings.epics, recorder.on_quote, expects_quotes=recorder.expects_quotes)
        recorder.stream = stream

        loop = asyncio.get_running_loop()

        def stop():
            recorder.stop()
            stream.stop()

        for stop_signal in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(stop_signal, stop)

        tasks = [asyncio.create_task(publisher.run()), asyncio.create_task(stream.run())]
        logger.info(
            "Capital.com Feed startup complete: %s account, %s.",
            settings.environment, ", ".join(f"{i.epic} as {i.label}" for i in settings.instruments),
        )
        await recorder.run()

    except Exception as e:
        logger.critical("Unhandled startup error: %s", e, exc_info=True)
        if telegram is not None:
            try:
                await asyncio.wait_for(telegram.send(f"Capital.com Feed CRITICAL FAILURE: {e}"), 5)
            except Exception:
                pass
        sys.exit(1)
    finally:
        logger.info("--- Shutting down WATA Capital.com Feed ---")
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if rest is not None:
            try:
                # Ends the session at Capital.com.
                await asyncio.wait_for(rest.close(), 5)
            except Exception as e:
                logger.warning("Error while ending the Capital.com session: %s", e)
        if exchange is not None:
            try:
                await exchange.close()
            except Exception:
                pass
        if pg is not None:
            await pg.close()
        if telegram is not None:
            try:
                await telegram.close()
            except Exception:
                pass


if __name__ == "__main__":
    event_loop.run(main())
