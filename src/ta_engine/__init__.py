"""
TA engine: WATA's own trading signals, from the Pine scripts of the admin's
signal rules run on the bars the Capital.com feed records (src/ta_engine/engine.py).

Opt-in (compose profile ``ta`` and ``trade.config.ta_engine.enabled``) and on its
own: no trading service waits for it. A rule's signals are only journaled until
it is switched to live in the admin; then they go to the web server as any
signal does, and the trader applies its rules to them.

The scripts run in a Node process of their own (src/ta_engine/runner.py), which
gets no file but its own, no environment and no network module.
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone

from src import event_loop

logger = logging.getLogger(__name__)

# How often an idle engine says again why it is idle: the admin takes an old state row for an engine that is down.
IDLE_STATE_EVERY_SECONDS = 60.0


def get_version() -> str:
    try:
        version_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VERSION")
        with open(version_file, "r") as file_handle:
            return file_handle.read().strip()
    except Exception:
        return "unknown"


async def _idle(journal, version: str, state: str, detail: str) -> None:
    """Stay up saying why nothing runs: exiting would only make Docker restart the container in a loop."""
    logger.warning("%s The TA engine stays idle.", detail)
    started_at = datetime.now(timezone.utc)
    while True:
        try:
            await journal.write_state({
                "updated_at": datetime.now(timezone.utc), "started_at": started_at, "version": version,
                "state": state, "detail": detail, "figures": {},
            })
        except Exception as e:
            logger.debug("The engine's state could not be written: %s", e)
        await asyncio.sleep(IDLE_STATE_EVERY_SECONDS)


async def main():
    import httpx

    from src.configuration import ConfigurationManager
    from src.database.postgres import AsyncDbTaManager, PostgresConnectionManager, init_ta_schema
    from src.logging_helper import setup_logging
    from src.mq_telegram.async_tools import AsyncTelegramSender
    from src.ta_engine.engine import Engine
    from src.ta_engine.rules import RulesFile
    from src.ta_engine.runner import RUNNER_PATH, NodeRunner, find_node, runner_command
    from src.ta_engine.sender import SignalSender
    from src.ta_engine.settings import SECTION, TaEngineSettings
    from src.ta_engine.source import CapitalTables
    from src.web_server_token import WebServerToken

    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        print("FATAL: WATA_CONFIG_PATH not set", file=sys.stderr)
        sys.exit(10)

    config_manager = ConfigurationManager(config_path)
    setup_logging(config_manager, "wata-ta-engine")
    version = get_version()
    logger.info("--- Starting WATA TA Engine v%s ---", version)

    telegram = None
    pg = None
    http = None
    runner = None
    try:
        postgres_config = config_manager.get_config_value("postgresql", {})
        # Its own small pool: reads of the feed's bars, writes of its journal.
        pg = PostgresConnectionManager(
            dsn=postgres_config.get("dsn", "postgresql://wata:wata@localhost:5432/wata"), min_size=1, max_size=2,
        )
        await pg.connect()
        await init_ta_schema(pg)
        journal = AsyncDbTaManager(pg)

        try:
            settings = TaEngineSettings.from_config(config_manager)
        except ValueError as e:
            await _idle(journal, version, "invalid_config", f"Invalid configuration of the TA engine: {e}.")
            return
        if not settings.enabled:
            await _idle(journal, version, "disabled", f"The TA engine is switched off ({SECTION}.enabled).")
            return
        node = find_node(settings.node_path)
        if node is None or not os.path.isfile(RUNNER_PATH):
            missing = "Node" if node is None else "the Pine runner (built by package.sh)"
            await _idle(journal, version, "no_runner", f"No {missing} in this image: nothing can run.")
            return

        telegram = AsyncTelegramSender(config_manager)
        http = httpx.AsyncClient()
        token = WebServerToken(config_manager)
        sender = SignalSender(settings.service_url, token.get_token, http, settings.request_timeout_seconds)
        rules_file = RulesFile(settings.rules_path)
        engine: Engine | None = None

        async def answer(ask: dict) -> list:
            return await engine.answer_series(ask)

        runner = NodeRunner(
            runner_command(node, RUNNER_PATH, settings.runner_memory_mb), answer,
            timeout_seconds=settings.run_timeout_seconds,
        )
        engine = Engine(
            settings=settings,
            rules=rules_file.snapshot,
            source=CapitalTables(pg),
            runner=runner,
            journal=journal,
            sender=sender,
            notify=telegram.send,
            version=version,
        )

        loop = asyncio.get_running_loop()
        for stop_signal in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(stop_signal, engine.stop)
        logger.info(
            "TA Engine startup complete: rules from %s, %d day(s) of history, restart at %s UTC.",
            settings.rules_path, settings.history_days, settings.restart_time,
        )
        await engine.run()

    except Exception as e:
        logger.critical("Unhandled startup error: %s", e, exc_info=True)
        if telegram is not None:
            try:
                await asyncio.wait_for(telegram.send(f"TA Engine CRITICAL FAILURE: {e}"), 5)
            except Exception:
                pass
        sys.exit(1)
    finally:
        logger.info("--- Shutting down WATA TA Engine ---")
        if runner is not None:
            await runner.stop()
        if http is not None:
            await http.aclose()
        if pg is not None:
            await pg.close()
        if telegram is not None:
            try:
                await telegram.close()
            except Exception:
                pass


if __name__ == "__main__":
    event_loop.run(main())
