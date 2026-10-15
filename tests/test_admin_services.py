import asyncio
import base64
import datetime
import json

import httpx
import pytest

from src.admin.errors import ServiceUnavailable
from src.admin.logs import TAIL_BYTES, parse_entries, read_tail
from src.admin.services import WataServices
from src.configuration import ConfigurationManager


@pytest.fixture
def config_manager(tmp_path):
    with open("tests/test_config.json") as source:
        config = json.load(source)
    config["trade"]["config"]["signal_polling"] = {"service_url": "http://web_server1:80/"}
    config["trade"]["config"]["watchlist_manager"] = {"enabled": True, "service_url": "http://watchlist_manager1:8081"}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return ConfigurationManager(str(path))


def make_services(config_manager, handler, tokens=("token-1",)):
    remaining = list(tokens)
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WataServices(config_manager, http_client, web_server_token_getter=lambda: remaining.pop(0))


def test_latest_signals_are_read_with_the_bearer_token(config_manager):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[{"indice": "us100"}])

    services = make_services(config_manager, handler)

    assert asyncio.run(services.latest_signals()) == [{"indice": "us100"}]
    assert (requests[0].url.host, requests[0].url.path) == ("web_server1", "/latest-signals")
    assert requests[0].headers["authorization"] == "Bearer token-1"


def test_signal_is_posted_to_the_internal_endpoint(config_manager):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "success", "signal_uuid": "abc"})

    services = make_services(config_manager, handler)

    assert asyncio.run(services.send_signal({"action": "close-position"})) == "abc"
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/internal/signal"
    assert json.loads(requests[0].content) == {"action": "close-position"}


def test_rotated_web_server_token_is_read_again_once(config_manager):
    seen_tokens = []

    def handler(request):
        seen_tokens.append(request.headers["authorization"])
        if request.headers["authorization"] == "Bearer old":
            return httpx.Response(401, json={"detail": "Unauthorized"})
        return httpx.Response(200, json=[])

    services = make_services(config_manager, handler, tokens=("old", "new"))

    assert asyncio.run(services.latest_signals()) == []
    assert seen_tokens == ["Bearer old", "Bearer new"]


def test_refused_token_is_not_retried_forever(config_manager):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"detail": "Unauthorized"})

    services = make_services(config_manager, handler, tokens=("a", "b", "c"))

    with pytest.raises(ServiceUnavailable):
        asyncio.run(services.latest_signals())
    assert len(calls) == 2


def test_unreachable_web_server_is_reported(config_manager):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    services = make_services(config_manager, handler)

    with pytest.raises(ServiceUnavailable, match="web_server"):
        asyncio.run(services.send_signal({"action": "close-position"}))


def test_watchlist_snapshot_is_returned_even_while_warming_up(config_manager):
    def handler(request):
        assert str(request.url) == "http://watchlist_manager1:8081/healthz"
        return httpx.Response(503, json={"ready": False, "warm_entries": 0})

    services = make_services(config_manager, handler)

    assert asyncio.run(services.watchlist_health()) == {"ready": False, "warm_entries": 0}


def test_watchlist_answer_that_is_not_a_snapshot_is_an_error(config_manager):
    def handler(request):
        return httpx.Response(503, json={"detail": "Watchlist manager is not ready"})

    services = make_services(config_manager, handler)

    with pytest.raises(ServiceUnavailable, match="watchlist_manager"):
        asyncio.run(services.watchlist_health())


def test_queue_stats_come_from_the_management_api(config_manager):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("trading-ops"):
            return httpx.Response(200, json={"name": "trading-ops", "messages": 3, "consumers": 1})
        return httpx.Response(404, json={"error": "Object Not Found"})

    services = make_services(config_manager, handler)

    assert asyncio.run(services.queue_stats()) == [
        {"name": "trading-ops", "declared": True, "messages": 3, "consumers": 1},
        {"name": "telegram_channel", "declared": False, "messages": 0, "consumers": 0},
    ]
    assert requests[0].url.host == "localhost"
    assert requests[0].url.port == 15672
    assert requests[0].url.raw_path == b"/api/queues/%2F/trading-ops"
    assert requests[0].headers["authorization"] == "Basic " + base64.b64encode(b"user:password").decode()


def test_queue_stats_report_a_refusing_management_api(config_manager):
    def handler(request):
        return httpx.Response(401)

    services = make_services(config_manager, handler)

    with pytest.raises(ServiceUnavailable, match="rabbitmq"):
        asyncio.run(services.queue_stats())


def test_log_tail_drops_the_partial_first_line(tmp_path):
    path = tmp_path / "service.log"
    path.write_text("first line\nsecond line\nthird line\n")

    assert read_tail(str(path)) == "first line\nsecond line\nthird line\n"
    assert read_tail(str(path), max_bytes=len("nd line\nthird line\n")) == "third line\n"


def test_log_tail_is_bounded(tmp_path):
    path = tmp_path / "service.log"
    path.write_text("x" * 99 + "\n" + ("y" * 99 + "\n") * 20000)

    assert len(read_tail(str(path))) <= TAIL_BYTES


def test_unparsed_leading_lines_are_kept_as_entries():
    entries = parse_entries("  File \"x.py\", line 1\n2026-10-11 08:00:00,000 - INFO - root - ok\n")

    assert entries[0]["level"] is None
    assert entries[1] == {"timestamp": "2026-10-11 08:00:00,000", "level": "INFO", "logger": "root", "message": "ok"}


def test_saxo_token_status_exposes_expiry_but_no_token(tmp_path, config_manager):
    from src.saxo_authen import SaxoAuth

    config_manager.config_data["authentication"]["persistant"]["token_path"] = str(tmp_path / "saxo" / "token.json")
    saxo_auth = SaxoAuth(config_manager)

    empty = saxo_auth.get_token_status()
    assert empty["has_token"] is False
    assert empty["access_token_expires_at"] is None
    assert empty["access_token_expired"] is True
    assert empty["refresh_token_expired"] is True

    saxo_auth.save_token_data({
        "access_token": "secret-access-token",
        "refresh_token": "secret-refresh-token",
        "expires_in": 1200,
        "refresh_token_expires_in": 3600,
    })
    status = saxo_auth.get_token_status()

    assert status["has_token"] is True
    assert status["access_token_expired"] is False
    assert status["refresh_token_expired"] is False
    assert "secret" not in json.dumps(status)
    saved_at = datetime.datetime.fromisoformat(status["saved_at"])
    assert saved_at.tzinfo is not None
    assert datetime.datetime.fromisoformat(status["access_token_expires_at"]) - saved_at == datetime.timedelta(seconds=1200)
    assert datetime.datetime.fromisoformat(status["refresh_token_expires_at"]) - saved_at == datetime.timedelta(seconds=3600)
    assert abs((datetime.datetime.now(datetime.timezone.utc) - saved_at).total_seconds()) < 60
