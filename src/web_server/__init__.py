from fastapi import FastAPI, Request, HTTPException, Depends, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from src.web_server_token import WebServerToken
from src.web_server.signal_store import SignalStore
import logging
from src.configuration import ConfigurationManager
from src.schema import SchemaLoader
import jsonschema
from datetime import datetime
import pytz
import os
import uuid
from src.logging_helper import setup_logging


config_path = os.getenv("WATA_CONFIG_PATH")

config_manager = ConfigurationManager(config_path)

# Use the logging utility to set up logging for the web server application
setup_logging(config_manager, "wata-api")

logging.info("WATA Web-server API is running")

app = FastAPI()

web_server_token = WebServerToken(config_manager)
SECRET_TOKEN = web_server_token.get_token()

# In-memory "latest signal per indice" store, polled by the async trader and fed
# by both the public /webhook and the internal /internal/signal endpoints.
SIGNAL_STORE = SignalStore()

# Paths that are only ever called by other WATA containers on the internal Docker
# network (trader polling, scheduler EOD close). They are exempt from the IP
# allowlist below but still require the Bearer token (see verify_bearer_token).
INTERNAL_ONLY_PATHS = {"/latest-signals", "/internal/signal"}

# List of allowed IP addresses
ALLOWED_IPS = [
    "127.0.0.1",
    "192.168.65.1",
    "83.195.218.196",
    "52.89.214.238",
    "34.212.75.30",
    "54.218.53.128",
    "52.32.178.7",
]


@app.middleware("http")
async def check_ip(request: Request, call_next):
    # Internal-only endpoints are never routed from the internet (Traefik's own
    # IP allowlist covers the whole domain already) and are protected by the
    # Bearer token instead, so skip the IP check for them.
    if request.url.path in INTERNAL_ONLY_PATHS:
        return await call_next(request)

    # Traefik will pass the real IP in the X-Forwarded-For header
    x_forwarded_for = request.headers.get("x-forwarded-for")
    
    if x_forwarded_for:
        # X-Forwarded-For can be a comma-separated list of IPs. The first one is the original client.
        client_ip = x_forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.client.host

    print(f"Incoming request from IP: {client_ip}")
    
    if client_ip not in ALLOWED_IPS:
        logging.warning(f"Forbidden access attempt from IP: {client_ip}")
        raise HTTPException(status_code=403, detail="Forbidden")
    
    return await call_next(request)

# Define a dependency for HTTP Bearer Authentication
bearer_scheme = HTTPBearer()


async def verify_token(token: str):
    # Assuming SECRET_TOKEN is the expected token value
    if token != SECRET_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return token


async def verify_bearer_token(credentials: HTTPAuthorizationCredentials = Security(bearer_scheme)):
    """Auth dependency for internal-only endpoints (trader polling, scheduler EOD close)."""
    await verify_token(credentials.credentials)


def _format_timestamp_ms(dt: datetime) -> str:
    """Format a UTC datetime with millisecond precision, e.g. 2026-07-16T08:08:53.821Z."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


async def _ingest_and_store_signal(data: dict, received_timestamp: str) -> str:
    """Validate-agnostic signal ingestion shared by /webhook and /internal/signal.

    Builds the record stored in SIGNAL_STORE (keyed by indice, latest
    signal_timestamp wins) and returns the generated signal_uuid.
    """
    signal_uuid = str(uuid.uuid4())
    record = {
        "action": data["action"],
        "indice": data["indice"],
        "signal_uuid": signal_uuid,
        "signal_timestamp": data["signal_timestamp"],
        "alert_timestamp": data["alert_timestamp"],
        "received_timestamp": received_timestamp,
    }
    if data.get("confidence") is not None:
        record["confidence"] = data["confidence"]

    await SIGNAL_STORE.upsert(record)
    logging.info(f"Stored signal for indice={data['indice']}: {record}")
    return signal_uuid


@app.post("/webhook")
async def webhook(request: Request):
    # Capture the real time this request reached the API, as early as possible.
    received_timestamp = _format_timestamp_ms(datetime.now(pytz.utc))

    # Extract the token from the query parameters
    token = request.query_params.get('token')

    if not token:
        logging.warning("No token provided in the query parameters.")
        return JSONResponse(content={"error": "Missing token parameter"}, status_code=400)

    # Verify the token
    try:
        await verify_token(token)
    except HTTPException as e:
        logging.warning(f"Token verification failed: {e.detail}")
        return JSONResponse(content={"error": e.detail}, status_code=e.status_code)

    data = await request.json()
    try:
        # Validate the data against the schema
        jsonschema.validate(instance=data, schema=SchemaLoader.get_webhook_schema())
    except jsonschema.exceptions.ValidationError as e:
        logging.warning(f"Invalid data received from from {request.client.host}: {e}")
        return JSONResponse(content={"error": "Bad Request"}, status_code=400)
    # TODO : Error handling error 500
    signal_uuid = await _ingest_and_store_signal(data, received_timestamp)
    logging.info(f"Received data from {request.client.host} : {data}")
    return JSONResponse(content={"status": "success", "signal_uuid": signal_uuid}, status_code=200)


@app.post("/internal/signal", include_in_schema=False)
async def internal_signal(request: Request, _: None = Depends(verify_bearer_token)):
    """Internal-only signal ingestion (e.g. scheduler's EOD close-position).

    Same payload shape and storage as /webhook, but authenticated with a Bearer
    token instead of a query-param token, and exempt from the IP allowlist.
    """
    received_timestamp = _format_timestamp_ms(datetime.now(pytz.utc))
    data = await request.json()
    try:
        jsonschema.validate(instance=data, schema=SchemaLoader.get_webhook_schema())
    except jsonschema.exceptions.ValidationError as e:
        logging.warning(f"Invalid internal signal payload: {e}")
        return JSONResponse(content={"error": "Bad Request"}, status_code=400)
    signal_uuid = await _ingest_and_store_signal(data, received_timestamp)
    logging.info(f"Received internal signal: {data}")
    return JSONResponse(content={"status": "success", "signal_uuid": signal_uuid}, status_code=200)


@app.get("/latest-signals", include_in_schema=False)
async def latest_signals(_: None = Depends(verify_bearer_token)):
    """Polled by the async trader every trade.config.signal_polling.interval_ms.

    Returns the latest stored signal per indice (list, one entry per indice).
    """
    return JSONResponse(content=await SIGNAL_STORE.get_all(), status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=80)
