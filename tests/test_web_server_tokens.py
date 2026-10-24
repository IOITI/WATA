"""
The web server's two tokens: TradingView's opens the webhook alone, the
internal one the endpoints of the other services alone; the token before a
rotation is accepted for a while; none set, everything is refused.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ["WATA_CONFIG_PATH"] = "tests/test_config.json"

from src.vault import loader  # noqa: E402
from src.web_server import app, web_server_token  # noqa: E402

WEBHOOK = "webhook-token-0123456789abcdefghij"
INTERNAL = "internal-token-0123456789abcdefghi"
SIGNAL = {"action": "long", "indice": "us100", "signal_timestamp": "2026-10-24T12:00:00Z",
          "alert_timestamp": "2026-10-24T12:00:01Z"}


@pytest.fixture
def client():
    loader.forget()
    loader._loaded.update({"webhook.token": {"token": WEBHOOK}, "internal.token": {"token": INTERNAL}})
    loader._sources.update({"webhook.token": loader.DATABASE, "internal.token": loader.DATABASE})
    with patch("fastapi.Request.client") as peer:
        peer.host = "127.0.0.1"
        yield TestClient(app)
    loader.forget()


def test_tradingviews_token_opens_the_webhook_alone(client):
    assert client.post(f"/webhook?token={WEBHOOK}", json=SIGNAL).status_code == 200
    assert client.post(f"/webhook?token={INTERNAL}", json=SIGNAL).status_code == 401
    assert client.get("/latest-signals", headers={"Authorization": f"Bearer {WEBHOOK}"}).status_code == 401


def test_the_internal_token_opens_the_other_services_endpoints_alone(client):
    assert client.get("/latest-signals", headers={"Authorization": f"Bearer {INTERNAL}"}).status_code == 200
    assert client.post("/internal/signal", json=SIGNAL, headers={"Authorization": f"Bearer {INTERNAL}"}).status_code == 200
    assert client.post("/internal/signal", json=SIGNAL, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_the_token_before_a_rotation_is_accepted_for_a_while(client):
    now = datetime.now(timezone.utc)
    loader._loaded["webhook.token"] = {"token": WEBHOOK, "previous": "the-one-before-0123456789abcdefgh",
                                       "rotated_at": (now - timedelta(minutes=10)).isoformat()}
    assert client.post("/webhook?token=the-one-before-0123456789abcdefgh", json=SIGNAL).status_code == 200

    loader._loaded["webhook.token"]["rotated_at"] = (now - timedelta(hours=2)).isoformat()
    assert client.post("/webhook?token=the-one-before-0123456789abcdefgh", json=SIGNAL).status_code == 401
    assert client.post(f"/webhook?token={WEBHOOK}", json=SIGNAL).status_code == 200


def test_none_set_refuses_everything(client, monkeypatch):
    loader.forget()
    monkeypatch.setattr(web_server_token, "stored_token", lambda: None)

    assert client.post(f"/webhook?token={WEBHOOK}", json=SIGNAL).status_code == 401
    assert client.post("/webhook?token=", json=SIGNAL).status_code in (400, 401)
    assert client.get("/latest-signals", headers={"Authorization": f"Bearer {INTERNAL}"}).status_code == 401


def test_a_service_of_before_reads_the_token_file_while_the_token_has_no_row(client, monkeypatch):
    loader.forget()
    monkeypatch.setattr(web_server_token, "stored_token", lambda: "the-file-token-0123456789abcdefghi")

    # The one token both were until v0.43.0.
    assert client.post("/webhook?token=the-file-token-0123456789abcdefghi", json=SIGNAL).status_code == 200
    assert client.get("/latest-signals", headers={"Authorization": "Bearer the-file-token-0123456789abcdefghi"}).status_code == 200
