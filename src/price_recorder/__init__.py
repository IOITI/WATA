"""
Price recorder service: keeps ten-second bars of the turbos the trader could
buy and of their underlying, for backtesting later.

Opt-in (compose profile ``recorder``) and on its own: it has its own Saxo
stream and its own database connections, and no trading service calls it.
"""

import asyncio
import logging
import os
import signal
import sys

from src import event_loop

logger = logging.getLogger(__name__)


def get_version() -> str:
    try:
        version_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VERSION")
        with open(version_file, "r") as file_handle:
            return file_handle.read().strip()
    except Exception:
        return "unknown"


async def _idle_forever(reason: str) -> None:
    """Stay up doing nothing: exiting would only make Docker restart the container in a loop."""
    logger.warning("%s The service stays idle.", reason)
    await asyncio.Event().wait()


async def main():
    from datetime import datetime

    import pytz

    from src.configuration import ConfigurationManager
    from src.database.postgres import AsyncDbPriceHistoryManager, PostgresConnectionManager, init_price_history_schema
    from src.logging_helper import setup_logging
    from src.mq_telegram.async_tools import AsyncTelegramSender
    from src.price_recorder.instruments import RecordingSet, discover_turbos, resolve_underlyings
    from src.price_recorder.recorder import PriceRecorder
    from src.price_recorder.settings import SECTION, RecorderSettings
    from src.saxo_authen import SaxoAuth
    from src.saxo_streaming.client import SaxoStreamClient
    from src.trade.async_services import AsyncInstrumentService, AsyncSaxoApiClient
    from src.trader import get_account_info_async
    from src.watchlist_manager.manager import load_allowed_indices

    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        print("FATAL: WATA_CONFIG_PATH not set", file=sys.stderr)
        sys.exit(10)

    config_manager = ConfigurationManager(config_path)
    setup_logging(config_manager, "wata-price-recorder")
    version = get_version()
    logger.info("--- Starting WATA Price Recorder v%s ---", version)

    try:
        settings = RecorderSettings.from_config(config_manager)
    except ValueError as e:
        await _idle_forever(f"Invalid price recorder configuration: {e}.")
        return
    if not settings.enabled:
        await _idle_forever(f"The price recorder is switched off ({SECTION}.enabled).")
        return

    telegram = AsyncTelegramSender(config_manager)
    pg = None
    api_client = None
    stream_client = None
    stream_task = None
    try:
        postgres_config = config_manager.get_config_value("postgresql", {})
        # Its own small pool: one writer, and room for the upkeep beside it.
        pg = PostgresConnectionManager(
            dsn=postgres_config.get("dsn", "postgresql://wata:wata@localhost:5432/wata"), min_size=1, max_size=2,
        )
        await pg.connect()
        await init_price_history_schema(pg)
        history = AsyncDbPriceHistoryManager(pg)

        saxo_auth = SaxoAuth(config_manager)
        api_client = AsyncSaxoApiClient(config_manager, saxo_auth)
        await api_client.ensure_ready()
        account_key = (await get_account_info_async(api_client)).AccountKey

        instrument_service = AsyncInstrumentService(api_client, config_manager, account_key)
        exchange_id = config_manager.get_config_value("trade.config.turbo_preference.exchange_id")
        allowed_indices = load_allowed_indices(config_manager)
        trading_timezone = pytz.timezone(settings.timezone)

        recording_set = RecordingSet(settings.max_instruments, today=lambda: datetime.now(trading_timezone).date())
        recording_set.set_underlyings(
            await resolve_underlyings(api_client, account_key, allowed_indices, settings.instruments)
        )

        recorder: PriceRecorder | None = None

        async def on_quotes(definition, snapshot_rows, context_id):
            if recorder is not None:
                await recorder.on_quotes(definition, snapshot_rows, context_id)

        streaming_config = config_manager.get_config_value("trade.config.general.streaming", {})
        stream_client = SaxoStreamClient(
            api_client=api_client,
            account_key=account_key,
            client_key="",
            on_positions_update=None,
            environment=config_manager.get_config_value("saxo_auth.env", "live"),
            access_token_getter=lambda: saxo_auth.get_token(),
            refresh_rate_ms=settings.refresh_rate_ms,
            reconnect_delay=streaming_config.get("reconnect_delay_seconds", 1.0),
            max_reconnect_delay=streaming_config.get("max_reconnect_delay_seconds", 30.0),
            reauth_interval_seconds=streaming_config.get("reauth_interval_seconds", 900),
            on_info_price_subscription_update=on_quotes,
        )

        async def discover():
            return await discover_turbos(instrument_service, exchange_id, allowed_indices)

        recorder = PriceRecorder(
            settings=settings,
            recording_set=recording_set,
            history=history,
            stream=stream_client,
            discover=discover,
            notify=telegram.send,
        )

        loop = asyncio.get_running_loop()
        for stop_signal in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(stop_signal, recorder.stop)

        stream_task = asyncio.create_task(stream_client.start())
        logger.info(
            "Price Recorder startup complete. Indices: %s. Underlyings recorded: %d.",
            ", ".join(allowed_indices) or "none", len(recording_set),
        )
        await recorder.run()

    except Exception as e:
        logger.critical("Unhandled startup error: %s", e, exc_info=True)
        try:
            await asyncio.wait_for(telegram.send(f"Price Recorder CRITICAL FAILURE: {e}"), 5)
        except Exception:
            pass
        sys.exit(1)
    finally:
        logger.info("--- Shutting down WATA Price Recorder ---")
        if stream_client is not None:
            try:
                await stream_client.stop()
            except Exception as e:
                logger.warning("Error while closing the stream: %s", e)
        if stream_task is not None:
            stream_task.cancel()
        if api_client is not None:
            await api_client.close()
        if pg is not None:
            await pg.close()
        try:
            await telegram.close()
        except Exception:
            pass


if __name__ == "__main__":
    event_loop.run(main())
