import datetime
import os
from contextlib import contextmanager

import pytest
from unittest.mock import MagicMock, patch

from src.saxo_authen import SaxoAuth, TOKEN_REQUEST_TIMEOUT_SECONDS


def _token_data(access_token: str, *, age_seconds: int = 0) -> dict:
    return {
        "access_token": access_token,
        "refresh_token": f"refresh-for-{access_token}",
        "expires_in": 1200,
        "refresh_token_expires_in": 3600,
        "date_saved": (datetime.datetime.now() - datetime.timedelta(seconds=age_seconds)).isoformat(),
    }


def _store(saxo_auth: SaxoAuth, token_data: dict):
    """Write token data as-is (save_token_data would stamp it with the current time)."""
    saxo_auth._write_encrypted_token_file(saxo_auth._encrypt_data(token_data))


def _refresh_response(access_token: str):
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {
        "access_token": access_token,
        "refresh_token": f"refresh-for-{access_token}",
        "expires_in": 1200,
        "refresh_token_expires_in": 3600,
    }
    return response


@pytest.fixture
def saxo_auth(tmp_path):
    config_manager = MagicMock()

    def get_config_value(key, default=None):
        configs = {
            "authentication.saxo.app_config_object": {
                "AppKey": "test_key",
                "AppSecret": "test_secret",
                "TokenEndpoint": "https://sim.logonvalidation.net/token",
                "AuthorizationEndpoint": "https://sim.logonvalidation.net/authorize",
                "RedirectUrls": ["http://localhost:8080/redirect"],
            },
            "authentication.persistant.token_path": str(tmp_path / "saxo_auth" / "persist_token.json"),
        }
        return configs.get(key, default)

    config_manager.get_config_value.side_effect = get_config_value
    return SaxoAuth(config_manager)


def test_valid_token_is_returned_without_refreshing_or_locking(saxo_auth):
    _store(saxo_auth, _token_data("current"))

    with patch("src.saxo_authen.requests.post") as mock_post:
        token = saxo_auth.get_token()

    assert token == "current"
    mock_post.assert_not_called()
    assert not os.path.exists(saxo_auth.token_lock_path)


def test_expired_token_is_refreshed_with_a_request_timeout_and_saved(saxo_auth):
    _store(saxo_auth, _token_data("expired", age_seconds=1500))

    with patch("src.saxo_authen.requests.post", return_value=_refresh_response("renewed")) as mock_post:
        token = saxo_auth.get_token()

    assert token == "renewed"
    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["timeout"] == TOKEN_REQUEST_TIMEOUT_SECONDS
    assert mock_post.call_args.kwargs["data"]["refresh_token"] == "refresh-for-expired"
    assert saxo_auth._load_token_data()["access_token"] == "renewed"


def test_token_renewed_by_another_service_while_waiting_for_the_lock_is_reused(saxo_auth):
    _store(saxo_auth, _token_data("expired", age_seconds=1500))

    @contextmanager
    def lock_released_after_another_service_refreshed():
        _store(saxo_auth, _token_data("renewed-elsewhere"))
        yield

    with patch.object(saxo_auth, "_token_refresh_lock", lock_released_after_another_service_refreshed):
        with patch("src.saxo_authen.requests.post") as mock_post:
            token = saxo_auth.get_token()

    assert token == "renewed-elsewhere"
    mock_post.assert_not_called()


def test_failed_refresh_raises_and_releases_the_lock(saxo_auth):
    _store(saxo_auth, _token_data("expired", age_seconds=1500))
    rejected = MagicMock(status_code=401)

    with patch("src.saxo_authen.requests.post", return_value=rejected):
        with pytest.raises(Exception, match="Failed to renew token"):
            saxo_auth.get_token()

    # The lock is free again: a later attempt can refresh
    with patch("src.saxo_authen.requests.post", return_value=_refresh_response("renewed")):
        assert saxo_auth.get_token() == "renewed"


def test_token_file_is_replaced_in_one_step(saxo_auth):
    _store(saxo_auth, _token_data("old"))
    replaced = []
    real_replace = os.replace

    def checking_replace(source, destination):
        # Until the rename, readers still get the complete previous token...
        assert saxo_auth._load_token_data()["access_token"] == "old"
        # ...and the new one is already complete on the side.
        with open(source, "rb") as new_file:
            assert saxo_auth._decrypt_data(new_file.read())["access_token"] == "new"
        replaced.append(destination)
        real_replace(source, destination)

    with patch("src.saxo_authen.os.replace", side_effect=checking_replace):
        saxo_auth.save_token_data(_token_data("new"))

    assert replaced == [saxo_auth.token_file_path]
    assert saxo_auth._load_token_data()["access_token"] == "new"
    # No temporary file left behind
    assert sorted(os.listdir(os.path.dirname(saxo_auth.token_file_path))) == [".key_salt", "persist_token.json"]
