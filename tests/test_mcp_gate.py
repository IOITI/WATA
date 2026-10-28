"""The gate in front of /mcp, through the whole admin application."""

import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from src.admin.app import create_app
from src.admin.mcp_server.calls import CallLog
from src.admin.mcp_server.tokens import TokenStore
from tests.test_admin_api import (  # noqa: F401 (fixtures)
    OTHER_CONTAINER,
    PASSWORD,
    PROXY,
    SAME_ORIGIN,
    built_interface,
    config_path,
    make_ctx,
)

ORIGIN = "https://admin.test"
INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def mcp_ctx(config_path, tmp_path, enabled=True):
    ctx = make_ctx(config_path, tmp_path, mcp_enabled=enabled, backtests_path=str(tmp_path / "backtests"))
    ctx.mcp_tokens = TokenStore(str(tmp_path / "admin" / "mcp_tokens.json"))
    ctx.mcp_calls = CallLog(str(tmp_path / "log" / "wata-admin" / "mcp.jsonl"))
    return ctx


@pytest.fixture
def ctx(config_path, tmp_path):
    return mcp_ctx(config_path, tmp_path)


@pytest.fixture
def token(ctx):
    token, _ = ctx.mcp_tokens.create("claude-mac")
    return token


def client_for(ctx, static_dir, peer=PROXY):
    return TestClient(create_app(ctx, static_dir=str(static_dir)), base_url=ORIGIN, client=(peer, 50000))


@pytest.fixture
def client(ctx, tmp_path):
    with client_for(ctx, tmp_path / "no-interface") as test_client:
        yield test_client


def bearer(token):
    return {**ACCEPT, "Authorization": f"Bearer {token}"}


def test_a_valid_token_opens_the_endpoint(client, token):
    response = client.post("/mcp", json=INITIALIZE, headers=bearer(token))

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["serverInfo"]["name"] == "wata"
    assert result["serverInfo"]["version"] == "9.9.9"
    assert "Start with `status`" in result["instructions"]
    assert response.headers["cache-control"] == "no-store"


def test_the_endpoint_with_a_trailing_slash_is_the_same(client, token):
    response = client.post("/mcp/", json=INITIALIZE, headers=bearer(token))

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "wata"


@pytest.mark.parametrize("authorization", [
    None,
    "Bearer wata_mcp_00000000_" + "A" * 43,
    "Bearer not-a-token",
    "Basic Y2xhdWRlOm1jcA==",
])
def test_no_valid_token_is_refused_without_naming_an_authorisation_server(client, token, authorization):
    headers = {**ACCEPT} if authorization is None else {**ACCEPT, "Authorization": authorization}

    response = client.post("/mcp", json=INITIALIZE, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="wata"'
    assert "resource_metadata" not in response.text


def test_the_token_without_its_scheme_is_refused(client, token):
    response = client.post("/mcp", json=INITIALIZE, headers={**ACCEPT, "Authorization": token})

    assert response.status_code == 401


def test_a_revoked_token_is_refused_at_once(client, ctx, token):
    assert client.post("/mcp", json=INITIALIZE, headers=bearer(token)).status_code == 200

    ctx.mcp_tokens.revoke("claude-mac")

    assert client.post("/mcp", json=INITIALIZE, headers=bearer(token)).status_code == 401


@pytest.mark.parametrize("origin", [ORIGIN, "https://evil.example", "null"])
def test_a_request_a_web_page_could_send_is_refused_whatever_its_origin(client, token, origin):
    response = client.post("/mcp", json=INITIALIZE, headers={**bearer(token), "Origin": origin})

    assert response.status_code == 403


@pytest.mark.parametrize("method", ["GET", "DELETE", "PUT", "PATCH"])
def test_only_post_is_answered(client, token, method):
    response = client.request(method, "/mcp", headers=bearer(token))

    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


def test_wrong_tokens_lock_their_address_out_and_no_other(ctx, tmp_path, token):
    with client_for(ctx, tmp_path / "no-interface") as proxy_client:
        for _ in range(10):
            proxy_client.post("/mcp", json=INITIALIZE, headers=bearer("wata_mcp_00000000_" + "B" * 43))
        locked = proxy_client.post("/mcp", json=INITIALIZE, headers=bearer(token))
        assert locked.status_code == 429
        assert int(locked.headers["retry-after"]) > 0
        # Behind the proxy, the address is the one it appended: another caller still gets in.
        other = proxy_client.post(
            "/mcp", json=INITIALIZE, headers={**bearer(token), "X-Forwarded-For": "10.77.0.3"},
        )
        assert other.status_code == 200


def test_a_container_of_the_network_cannot_pass_for_someone_else_to_escape_the_lock(ctx, tmp_path, token):
    with client_for(ctx, tmp_path / "no-interface", peer=OTHER_CONTAINER) as direct:
        for number in range(10):
            direct.post(
                "/mcp", json=INITIALIZE,
                headers={**bearer("wata_mcp_00000000_" + "B" * 43), "X-Forwarded-For": f"10.0.0.{number}"},
            )
        locked = direct.post("/mcp", json=INITIALIZE, headers={**bearer(token), "X-Forwarded-For": "10.0.0.99"})

    assert locked.status_code == 429


def test_the_operator_login_is_not_locked_by_wrong_tokens(ctx, tmp_path):
    with client_for(ctx, tmp_path / "no-interface") as test_client:
        for _ in range(12):
            test_client.post("/mcp", json=INITIALIZE, headers=bearer("wata_mcp_00000000_" + "C" * 43))
        login = test_client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)

    assert login.status_code == 204


def test_a_session_cookie_does_not_open_the_endpoint(client):
    assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204

    response = client.post("/mcp", json=INITIALIZE, headers=ACCEPT)

    assert response.status_code == 401


@pytest.mark.parametrize("method,path", [("GET", "/api/overview"), ("GET", "/api/backtests"), ("GET", "/api/ta/scripts")])
def test_a_token_does_not_open_the_api(client, token, method, path):
    response = client.request(method, path, headers=bearer(token))

    assert response.status_code == 401


def test_with_the_endpoint_off_nothing_answers_there(config_path, tmp_path, built_interface):
    ctx = mcp_ctx(config_path, tmp_path, enabled=False)
    token, _ = ctx.mcp_tokens.create("claude-mac")

    with client_for(ctx, built_interface) as test_client:
        assert test_client.post("/mcp", json=INITIALIZE, headers=bearer(token)).status_code in (404, 405)
        assert test_client.get("/mcp", headers=bearer(token)).status_code == 404
    assert not any(getattr(route, "path", "").startswith("/mcp") for route in test_client.app.routes)


@pytest.mark.parametrize("path", ["/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server",
                                  "/.well-known", "/mcp/anything", "/mcp/.well-known/x"])
def test_the_interface_never_answers_where_a_client_looks_for_an_authorisation_server(ctx, tmp_path, built_interface,
                                                                                      path):
    with client_for(ctx, built_interface) as test_client:
        response = test_client.get(path)

    assert response.status_code in (404, 405)
    assert "WATA Admin" not in response.text


def test_the_interface_still_answers_its_own_routes(ctx, tmp_path, built_interface):
    with client_for(ctx, built_interface) as test_client:
        assert "WATA Admin" in test_client.get("/backtests/runs").text


def test_outside_the_api_only_the_interface_and_the_endpoint_are_routed(ctx, built_interface):
    app = create_app(ctx, static_dir=str(built_interface))

    outside = sorted(route.path for route in app.routes if not route.path.startswith("/api"))

    assert outside == ["/assets", "/mcp", "/mcp/", "/{path:path}"]


def test_the_admin_never_loads_the_sdk_while_the_endpoint_is_off():
    # The AI agent's modules too: they call the tools through the SDK only once the agent starts.
    probe = (
        "import sys; import src.admin.app, src.admin.context, src.admin.cli, src.admin.mcp_server.gate; "
        "import src.admin.agent.service, src.admin.agent.tools, src.admin.routers.agent; "
        "print(json.dumps(sorted(name for name in sys.modules if name.split('.')[0] in ('mcp', 'anthropic'))))"
    )
    ran = subprocess.run(
        [sys.executable, "-c", "import json; " + probe], capture_output=True, text=True,
        env={"PYTHONPATH": "./:src/", "WATA_CONFIG_PATH": "tests/test_config.json", "PATH": "/usr/bin:/bin"},
    )

    assert ran.returncode == 0, ran.stderr
    assert json.loads(ran.stdout) == []
