from fastapi import FastAPI, Request, HTTPException, Depends, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from auth_token import WebServerToken
import logging
from src.configuration import ConfigurationManager
from src.schema import SchemaLoader
import jsonschema
import pika
from pika.exceptions import AMQPConnectionError
import json
from datetime import datetime
import pytz
import os
from src.logging_helper import setup_logging


config_path = os.getenv("WATA_CONFIG_PATH")

config_manager = ConfigurationManager(config_path)

# Use the logging utility to set up logging for the web server application
setup_logging(config_manager, "wata-api")

logging.info("WATA Web-server API is running")

app = FastAPI()

web_server_token = WebServerToken(config_manager)
SECRET_TOKEN = web_server_token.get_token()

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
    client_ip = request.client.host
    print(f"HERE {client_ip}")
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


def send_message_to_trading(action, indice, signal_timestamp, alert_timestamp):
    try:
        # Retrieve RabbitMQ credentials from the configuration
        rabbitmq_config = config_manager.get_rabbitmq_config()
        rabbitmq_hostname = rabbitmq_config["hostname"]
        rabbitmq_username = rabbitmq_config["authentication"]["username"]
        rabbitmq_password = rabbitmq_config["authentication"]["password"]

        # Establish a connection to RabbitMQ with the provided credentials
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=rabbitmq_hostname,
                credentials=pika.PlainCredentials(rabbitmq_username, rabbitmq_password),
            )
        )
        channel = connection.channel()
        channel.queue_declare(queue="trading-action")

        # Get the current time in UTC
        now_utc = datetime.now(pytz.utc)

        message = json.dumps(
            {
                "action": action,
                "indice": indice,
                "signal_timestamp": signal_timestamp,
                "alert_timestamp": alert_timestamp,
                "mqsend_timestamp": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        channel.basic_publish(exchange="", routing_key="trading-action", body=message)
        logging.info(f"Send message to channel trading-action, message {message}")
    except pika.exceptions.AMQPConnectionError as e:
        logging.error(f"Failed to connect to RabbitMQ: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred while sending the message: {e}")
    finally:
        # Ensure the connection is closed even if an error occurs
        if "connection" in locals():
            connection.close()


@app.post("/webhook")
async def webhook(request: Request):
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
    send_message_to_trading(
        data["action"],
        data["indice"],
        data["signal_timestamp"],
        data["alert_timestamp"],
    )
    logging.info(f"Received data from {request.client.host} : {data}")
    return JSONResponse(content={"status": "success"}, status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=80)
