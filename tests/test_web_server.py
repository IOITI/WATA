import os
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import json

# Set the config path before importing the app
os.environ["WATA_CONFIG_PATH"] = "tests/test_config.json"

from src.web_server import app, web_server_token

@pytest.fixture
def client():
    with patch("fastapi.Request.client") as mock_client:
        mock_client.host = "127.0.0.1"
        with patch("src.web_server.verify_token", return_value=None):
            # Create a dummy token file
            with open("/tmp/token.json", "w") as f:
                json.dump({"token": "test_token"}, f)
            yield TestClient(app)
            os.remove("/tmp/token.json")


def test_webhook_unauthorized(client):
    with patch("src.web_server.verify_token", side_effect=HTTPException(status_code=401, detail="Unauthorized")):
        response = client.post("/webhook?token=wrong_token", json={"key": "value"})
        assert response.status_code == 401
        assert response.json() == {"error": "Unauthorized"}

@patch("src.web_server.send_message_to_trading")
def test_webhook_success(mock_send_message, client):
    mock_send_message.return_value = "signal_id_123"
    response = client.post(
        "/webhook?token=test_token",
        json={
            "action": "long",
            "indice": "us100",
            "signal_timestamp": "2023-07-01T12:00:00Z",
            "alert_timestamp": "2023-07-01T12:00:01Z",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "success", "signal_id": "signal_id_123"}

@patch("src.web_server.send_message_to_trading")
def test_webhook_ip_filtering(mock_send_message, client):
    mock_send_message.return_value = "signal_id_123"
    # The TestClient's default host is "testclient" which is not in the allowed list
    with patch("src.web_server.ALLOWED_IPS", new=["1.2.3.4"]):
        response = client.post(
            "/webhook?token=test_token",
            headers={"X-Forwarded-For": "1.2.3.4"},
            json={
                "action": "long",
                "indice": "us100",
                "signal_timestamp": "2023-07-01T12:00:00Z",
                "alert_timestamp": "2023-07-01T12:00:01Z",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"status": "success", "signal_id": "signal_id_123"}
