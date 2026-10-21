import os
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import json

# Set the config path before importing the app
os.environ["WATA_CONFIG_PATH"] = "tests/test_config.json"

from src.web_server import app, web_server_token
from src.web_server.signal_store import SignalStore

BEARER_HEADERS = {"Authorization": "Bearer test_token"}


def _post_signal(client, indice, signal_timestamp, action="long"):
    response = client.post(
        "/webhook?token=test_token",
        json={
            "action": action,
            "indice": indice,
            "signal_timestamp": signal_timestamp,
            "alert_timestamp": signal_timestamp,
        },
    )
    assert response.status_code == 200
    return response.json()["signal_uuid"]


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

def test_webhook_success(client):
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
    data = response.json()
    assert data["status"] == "success"
    assert "signal_uuid" in data
    assert isinstance(data["signal_uuid"], str)

def test_webhook_ip_filtering(client):
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
        data = response.json()
        assert data["status"] == "success"
        assert "signal_uuid" in data
        assert isinstance(data["signal_uuid"], str)


def test_latest_signals_without_cursor_returns_every_stored_signal(client):
    with patch("src.web_server.SIGNAL_STORE", new=SignalStore()):
        first_uuid = _post_signal(client, "us100", "2023-07-01T12:00:00Z")
        second_uuid = _post_signal(client, "dax", "2023-07-01T12:00:01Z")

        response = client.get("/latest-signals", headers=BEARER_HEADERS)

    assert response.status_code == 200
    signals = response.json()
    assert [signal["signal_uuid"] for signal in signals] == [first_uuid, second_uuid]
    assert [signal["seq"] for signal in signals] == [1, 2]


def test_latest_signals_with_cursor_returns_only_newer_signals(client):
    with patch("src.web_server.SIGNAL_STORE", new=SignalStore()):
        _post_signal(client, "us100", "2023-07-01T12:00:00Z")
        second_uuid = _post_signal(client, "dax", "2023-07-01T12:00:01Z")

        newer = client.get("/latest-signals?after=1", headers=BEARER_HEADERS)
        nothing_new = client.get("/latest-signals?after=2", headers=BEARER_HEADERS)

    assert [signal["signal_uuid"] for signal in newer.json()] == [second_uuid]
    assert nothing_new.json() == []


def test_a_close_sent_for_the_moment_of_an_entry_is_never_delivered(client):
    with patch("src.web_server.SIGNAL_STORE", new=SignalStore()):
        entry = _post_signal(client, "us100", "2023-07-01T12:00:00Z", action="long")
        _post_signal(client, "us100", "2023-07-01T12:00:00Z", action="close-position")

        response = client.get("/latest-signals", headers=BEARER_HEADERS)

    assert [(signal["signal_uuid"], signal["action"]) for signal in response.json()] == [(entry, "long")]


def test_latest_signals_long_poll_returns_empty_once_wait_elapses(client):
    with patch("src.web_server.SIGNAL_STORE", new=SignalStore()):
        _post_signal(client, "us100", "2023-07-01T12:00:00Z")

        response = client.get("/latest-signals?after=1&wait_ms=50", headers=BEARER_HEADERS)

    assert response.status_code == 200
    assert response.json() == []
