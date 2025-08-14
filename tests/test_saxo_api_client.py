import pytest
from unittest.mock import patch, MagicMock
import json
import requests

from src.trade.api_actions import SaxoApiClient
from src.trade.exceptions import (
    SaxoApiError,
    InsufficientFundsException,
    OrderPlacementError,
    TokenAuthenticationException,
    ApiRequestException,
)
from src.saxo_authen import SaxoAuth
from src.saxo_openapi.exceptions import OpenAPIError as SaxoOpenApiLibError

# Fixtures will be in conftest.py, but for now, let's assume they are available.
# We will create conftest.py later.

@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_init_and_token_refresh(mock_saxo_lib, mock_config_manager):
    """Test that the client initializes and refreshes the token correctly."""
    mock_auth = MagicMock(spec=SaxoAuth)
    mock_auth.get_token.side_effect = ["token1", "token1", "token2"]

    client = SaxoApiClient(mock_config_manager, mock_auth)
    mock_saxo_lib.assert_called_once_with(access_token="token1", environment="simulation", request_params={"timeout": 30})

    client._ensure_valid_token_and_api_instance()
    mock_saxo_lib.assert_called_once()

    client._ensure_valid_token_and_api_instance()
    mock_saxo_lib.assert_called_with(access_token="token2", environment="simulation", request_params={"timeout": 30})
    assert mock_saxo_lib.call_count == 2

@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_request_success(mock_saxo_lib, mock_config_manager, mock_saxo_auth):
    mock_api_instance = mock_saxo_lib.return_value
    mock_api_instance.request.return_value = {"status": "success"}

    client = SaxoApiClient(mock_config_manager, mock_saxo_auth)
    response = client.request("some_endpoint_request_obj")

    assert response == {"status": "success"}
    mock_api_instance.request.assert_called_once_with("some_endpoint_request_obj")

@patch('src.trade.api_actions.SaxoOpenApiLib')
@pytest.mark.parametrize("status_code, error_code, error_content_str, expected_exception, is_order_endpoint", [
    (400, "InsufficientFunds", '{"Message": "Not enough money"}', InsufficientFundsException, False),
    (400, "SomeError", '{"Message": "Bad request"}', OrderPlacementError, True),
    (401, "AuthError", '{"Message": "Unauthorized"}', TokenAuthenticationException, False),
    (429, "RateLimit", '{"Message": "Too many requests"}', SaxoApiError, False),
    (500, "ServerError", 'Internal Server Error', SaxoApiError, False),
])
def test_saxo_api_client_request_saxo_error_mapping(mock_saxo_lib, mock_config_manager, mock_saxo_auth, status_code, error_code, error_content_str, expected_exception, is_order_endpoint):
    """Test that SaxoOpenApiLibError is correctly mapped to custom exceptions."""
    try:
        content_json = json.loads(error_content_str)
        if 'ErrorCode' not in content_json:
            content_json['ErrorCode'] = error_code
        final_content = json.dumps(content_json)
    except json.JSONDecodeError:
        final_content = error_content_str

    mock_api_instance = mock_saxo_lib.return_value
    mock_api_instance.request.side_effect = SaxoOpenApiLibError(code=status_code, content=final_content, reason="Some Reason")

    client = SaxoApiClient(mock_config_manager, mock_saxo_auth)

    mock_endpoint = MagicMock()
    mock_endpoint.path = "/trade/v2/orders" if is_order_endpoint else "/some/other/endpoint"
    mock_endpoint.data = {"some": "data"}
    mock_endpoint.params = {"some": "params"}


    with pytest.raises(expected_exception) as excinfo:
        client.request(mock_endpoint)

    # Improved validation
    if hasattr(excinfo.value, 'status_code'):
        assert excinfo.value.status_code == status_code

    if expected_exception in [InsufficientFundsException, OrderPlacementError, TokenAuthenticationException, SaxoApiError]:
        if final_content.startswith('{'):
            assert excinfo.value.saxo_error_details == json.loads(final_content)
        else:
            assert excinfo.value.saxo_error_details == final_content


@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_request_connection_error(mock_saxo_lib, mock_config_manager, mock_saxo_auth):
    mock_api_instance = mock_saxo_lib.return_value
    mock_api_instance.request.side_effect = requests.RequestException("Connection failed")
    client = SaxoApiClient(mock_config_manager, mock_saxo_auth)
    with pytest.raises(ApiRequestException, match="Underlying request failed: Connection failed"):
        client.request("some_endpoint")

@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_request_non_string_error_content(mock_saxo_lib, mock_config_manager, mock_saxo_auth):
    """Test error handling when error content is not a string."""
    mock_api_instance = mock_saxo_lib.return_value
    mock_api_instance.request.side_effect = SaxoOpenApiLibError(code=500, content={"error": "detail"}, reason="Server Error")
    client = SaxoApiClient(mock_config_manager, mock_saxo_auth)
    with pytest.raises(SaxoApiError) as excinfo:
        client.request("some_endpoint")
    assert str({"error": "detail"}) in str(excinfo.value)

@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_request_invalid_json_error(mock_saxo_lib, mock_config_manager, mock_saxo_auth):
    """Test error handling with invalid JSON content."""
    mock_api_instance = mock_saxo_lib.return_value
    mock_api_instance.request.side_effect = SaxoOpenApiLibError(code=500, content="Not a valid JSON", reason="Server Error")
    client = SaxoApiClient(mock_config_manager, mock_saxo_auth)
    with pytest.raises(SaxoApiError, match="Not a valid JSON"):
        client.request("some_endpoint")

@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_request_token_auth_exception_reraised(mock_saxo_lib, mock_config_manager, mock_saxo_auth):
    """Test that TokenAuthenticationException is re-raised."""
    mock_saxo_auth.get_token.side_effect = TokenAuthenticationException("Token machine broke")
    with pytest.raises(TokenAuthenticationException):
        SaxoApiClient(mock_config_manager, mock_saxo_auth)

@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_unexpected_exception(mock_saxo_lib, mock_config_manager, mock_saxo_auth):
    """Test wrapping of unexpected exceptions."""
    mock_api_instance = mock_saxo_lib.return_value
    mock_api_instance.request.side_effect = Exception("Something totally unexpected")
    client = SaxoApiClient(mock_config_manager, mock_saxo_auth)
    with pytest.raises(ApiRequestException, match="Unexpected wrapper error: Something totally unexpected"):
        client.request("some_endpoint")
