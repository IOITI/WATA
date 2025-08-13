import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import json
from fastapi import HTTPException

# Set the config path before importing the app
os.environ["WATA_CONFIG_PATH"] = "tests/test_config.json"

from src.web_server import app, web_server_token

@pytest.fixture
def client():
    # By default, TestClient raises exceptions. We can disable this.
    # However, for this test, we want to assert the exception is raised.
    with patch("fastapi.Request.client") as mock_client:
        mock_client.host = "127.0.0.1"
        with patch("src.web_server.verify_token", return_value=None):
            # Create a dummy token file
            with open("/tmp/token.json", "w") as f:
                json.dump({"token": "test_token"}, f)
            # We will not use raise_server_exceptions=False, so that middleware exceptions propagate
            yield TestClient(app)
            os.remove("/tmp/token.json")

def test_allowed_ip(client):
    response = client.get("/webhook", headers={"X-Forwarded-For": "127.0.0.1"})
    # This endpoint does not support GET, so we expect 405, not 403,
    # proving the request passed the IP filter.
    assert response.status_code == 405

def test_forbidden_ip(client):
    with patch("src.web_server.ALLOWED_IPS", new=["192.168.1.1"]):
        # Here, we expect the middleware to raise an HTTPException,
        # which pytest can catch and assert on.
        with pytest.raises(HTTPException) as exc_info:
            client.get("/webhook?token=test_token", headers={"X-Forwarded-For": "1.2.3.4"})
        assert exc_info.value.status_code == 403

@patch("src.web_server.send_message_to_trading")
def test_webhook_success_with_allowed_ip(mock_send_message, client):
    mock_send_message.return_value = "signal_id_123"
    response = client.post(
        "/webhook?token=test_token",
        json={
            "action": "long",
            "indice": "us100",
            "signal_timestamp": "2023-07-01T12:00:00Z",
            "alert_timestamp": "2023-07-01T12:00:01Z",
        },
        headers={"X-Forwarded-For": "127.0.0.1"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "success", "signal_id": "signal_id_123"}