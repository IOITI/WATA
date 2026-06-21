import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from src.configuration import ConfigurationManager
from src.logging_helper import setup_logging
from src.saxo_authen import SaxoAuth
from src.trade.async_services import AsyncInstrumentService, AsyncSaxoApiClient
from src.saxo_streaming.client import SaxoStreamClient
import src.saxo_openapi.endpoints.portfolio as pf

from src.watchlist_manager.manager import TurboWatchlistManager, load_allowed_indices

logger = logging.getLogger(__name__)

APP_VERSION = "unknown"
app_state = {
    "config_manager": None,
    "api_client": None,
    "watchlist_manager": None,
}


def get_version() -> str:
    try:
        version_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VERSION")
        with open(version_file, "r") as file_handle:
            return file_handle.read().strip()
    except Exception:
        return "unknown"


async def get_account_key_async(api_client: AsyncSaxoApiClient) -> str:
    request = pf.accounts.AccountsMe()
    response = await api_client.request(request)
    accounts = response.get("Data", []) if response else []
    if not accounts:
        raise RuntimeError("No Saxo accounts returned for watchlist manager startup")
    return accounts[0]["AccountKey"]


@asynccontextmanager
async def lifespan(_: FastAPI):
    global APP_VERSION
    APP_VERSION = get_version()

    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        raise RuntimeError("WATA_CONFIG_PATH not set")

    config_manager = ConfigurationManager(config_path)
    setup_logging(config_manager, "wata-watchlist-manager")
    logger.info("--- Starting WATA Watchlist Manager v%s ---", APP_VERSION)

    exchange_id = config_manager.get_config_value("trade.config.turbo_preference.exchange_id")
    allowed_indices = load_allowed_indices(config_manager)
    environment = config_manager.get_config_value("saxo_auth.env", "live")
    streaming_config = config_manager.get_config_value("trade.config.general.streaming", {})

    saxo_auth = SaxoAuth(config_manager)
    api_client = AsyncSaxoApiClient(config_manager, saxo_auth)
    await api_client.ensure_ready()
    account_key = await get_account_key_async(api_client)

    instrument_service = AsyncInstrumentService(api_client, config_manager, account_key)
    watchlist_manager = TurboWatchlistManager(
        instrument_service=instrument_service,
        config_manager=config_manager,
        exchange_id=exchange_id,
        allowed_indices=allowed_indices,
    )
    watchlist_stream_client = SaxoStreamClient(
        api_client=api_client,
        account_key=account_key,
        client_key="",
        on_positions_update=None,
        environment=environment,
        access_token_getter=lambda: saxo_auth.get_token(),
        refresh_rate_ms=streaming_config.get("refresh_rate_ms", 1000),
        reconnect_delay=streaming_config.get("reconnect_delay_seconds", 1.0),
        max_reconnect_delay=streaming_config.get("max_reconnect_delay_seconds", 30.0),
        reauth_interval_seconds=streaming_config.get("reauth_interval_seconds", 900),
        on_info_price_subscription_update=watchlist_manager.handle_info_price_subscription_update,
    )
    watchlist_manager.attach_stream_client(watchlist_stream_client)
    await watchlist_manager.start()

    app_state["config_manager"] = config_manager
    app_state["api_client"] = api_client
    app_state["watchlist_manager"] = watchlist_manager
    try:
        yield
    finally:
        logger.info("--- Shutting down WATA Watchlist Manager ---")
        if watchlist_manager is not None:
            await watchlist_manager.stop()
        if api_client is not None:
            await api_client.close()
        app_state["watchlist_manager"] = None
        app_state["api_client"] = None
        app_state["config_manager"] = None


app = FastAPI(lifespan=lifespan)


def get_watchlist_manager() -> TurboWatchlistManager:
    watchlist_manager = app_state.get("watchlist_manager")
    if watchlist_manager is None:
        raise HTTPException(status_code=503, detail="Watchlist manager is not ready")
    return watchlist_manager


@app.get("/healthz")
async def healthz():
    watchlist_manager = get_watchlist_manager()
    snapshot = watchlist_manager.get_health_snapshot()
    status_code = 200 if snapshot["ready"] else 503
    return JSONResponse(content=snapshot, status_code=status_code)


@app.get("/watchlist/best")
async def get_best_watchlist_entry(
    indice: str | None = None,
    underlying_uic: str | None = None,
    direction: str = Query(..., pattern="^(long|short)$"),
):
    if not indice and not underlying_uic:
        raise HTTPException(status_code=400, detail="Either 'indice' or 'underlying_uic' must be provided")

    watchlist_manager = get_watchlist_manager()
    payload = watchlist_manager.get_cached_turbo(
        indice=indice,
        underlying_uic=underlying_uic,
        direction=direction,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="No cached turbo available for the requested key")
    return payload


def get_server_bind() -> tuple[str, int]:
    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        return "0.0.0.0", 8081

    try:
        config_manager = ConfigurationManager(config_path)
        watchlist_config = config_manager.get_config_value("trade.config.watchlist_manager", {})
        return watchlist_config.get("api_host", "0.0.0.0"), watchlist_config.get("api_port", 8081)
    except Exception:
        return "0.0.0.0", 8081


if __name__ == "__main__":
    import uvicorn

    host, port = get_server_bind()
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    uvicorn.run(app, host=host, port=port)