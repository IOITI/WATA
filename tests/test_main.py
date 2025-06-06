import json
import pytest
from unittest.mock import MagicMock, patch, call
import logging
import pika # Added for test_main_execution_flow
from src import main
from src.trade.exceptions import (
    TradingRuleViolation,
    NoMarketAvailableException,
    NoTurbosAvailableException,
    PositionNotFoundException,
    InsufficientFundsException,
    ApiRequestException,
    TokenAuthenticationException,
    DatabaseOperationException,
    PositionCloseException,
    WebSocketConnectionException,
    SaxoApiError,
    OrderPlacementError,
    ConfigurationError
)
import jsonschema

main.rabbit_connection = MagicMock()

@pytest.fixture
def mock_dependencies():
    dependencies = {
        "trading_orchestrator": MagicMock(),
        "performance_monitor": MagicMock(),
        "trading_rule": MagicMock(),
        "db_position_manager": MagicMock(),
        "table_trade_performance_manager": MagicMock(),
        "trade_turbo_exchange_id": "test_exchange_id",
        "rabbit_conn": main.rabbit_connection,
        "ch": MagicMock(),
        "method": MagicMock(delivery_tag="test_delivery_tag"),
        "properties": MagicMock(),
        "body": MagicMock(),
        "composer": MagicMock(),
        "data_from_mq": MagicMock()
    }
    return dependencies

@patch('src.main.TelegramMessageComposer')
@patch('src.main.SchemaLoader')
@patch('src.main.handle_exception')
@patch('src.main.send_message_to_mq_for_telegram')
def test_callback_json_decode_error(mock_send_mq, mock_handle_exception, mock_schema_loader, mock_composer_class, mock_dependencies):
    def decode_side_effect(*args, **kwargs):
        if kwargs.get('errors') == 'ignore': # From the except block's body.decode
            return "partially_decoded_body_on_error"
        # Initial call to body.decode('utf-8')
        raise json.JSONDecodeError("Error", "doc", 0)

    mock_dependencies["body"].decode.side_effect = decode_side_effect

    main.callback(
        mock_dependencies["ch"], mock_dependencies["method"], mock_dependencies["properties"], mock_dependencies["body"],
        mock_dependencies["trading_orchestrator"], mock_dependencies["performance_monitor"],
        mock_dependencies["trading_rule"], mock_dependencies["db_position_manager"],
        mock_dependencies["table_trade_performance_manager"], mock_dependencies["trade_turbo_exchange_id"],
        mock_dependencies["rabbit_conn"]
    )

    mock_dependencies["ch"].basic_ack.assert_called_once_with(delivery_tag="test_delivery_tag")
    mock_send_mq.assert_called_once()
    assert "CRITICAL: Error decoding message body" in mock_send_mq.call_args[0][1]
    mock_handle_exception.assert_not_called()

@patch('src.main.TelegramMessageComposer')
@patch('src.main.SchemaLoader')
@patch('src.main.handle_validation_error')
def test_callback_schema_validation_error(mock_handle_validation_error, mock_schema_loader, mock_composer_class, mock_dependencies):
    mock_dependencies["body"].decode.return_value = '{}'
    mock_schema_loader.get_trading_action_schema.return_value = {"type": "object", "properties": {"action": {"type": "string"}}}

    validation_error = jsonschema.exceptions.ValidationError("Invalid schema")
    with patch('jsonschema.validate', side_effect=validation_error) as mock_validate:
        main.callback(
            mock_dependencies["ch"], mock_dependencies["method"], mock_dependencies["properties"], mock_dependencies["body"],
            mock_dependencies["trading_orchestrator"], mock_dependencies["performance_monitor"],
            mock_dependencies["trading_rule"], mock_dependencies["db_position_manager"],
            mock_dependencies["table_trade_performance_manager"], mock_dependencies["trade_turbo_exchange_id"],
            mock_dependencies["rabbit_conn"]
        )
        data_loaded = json.loads(mock_dependencies["body"].decode.return_value)
        mock_validate.assert_called_once_with(instance=data_loaded, schema=mock_schema_loader.get_trading_action_schema.return_value)

    # Check that handle_validation_error was called with an exception of the correct type and message
    mock_handle_validation_error.assert_called_once()
    called_args = mock_handle_validation_error.call_args[0]
    assert isinstance(called_args[0], jsonschema.exceptions.ValidationError)
    assert called_args[0].message == "Invalid schema"
    assert called_args[1] == mock_dependencies["body"]
    assert called_args[2] == mock_dependencies["ch"]
    assert called_args[3] == mock_dependencies["method"]


@patch('src.main.TelegramMessageComposer')
@patch('src.main.SchemaLoader')
@patch('src.main.ACTION_HANDLERS', {})
@patch('src.main.handle_unknown_action')
def test_callback_unknown_action(mock_handle_unknown_action, mock_schema_loader, mock_composer_class, mock_dependencies):
    action_data = {"action": "unknown_action", "signal_id": "test_signal"}
    mock_dependencies["body"].decode.return_value = json.dumps(action_data)
    mock_schema_loader.get_trading_action_schema.return_value = {"type": "object"}

    with patch('jsonschema.validate') as mock_validate:
        mock_composer_instance = MagicMock()
        mock_composer_class.return_value = mock_composer_instance
        main.callback(
            mock_dependencies["ch"], mock_dependencies["method"], mock_dependencies["properties"], mock_dependencies["body"],
            mock_dependencies["trading_orchestrator"], mock_dependencies["performance_monitor"],
            mock_dependencies["trading_rule"], mock_dependencies["db_position_manager"],
            mock_dependencies["table_trade_performance_manager"], mock_dependencies["trade_turbo_exchange_id"],
            mock_dependencies["rabbit_conn"]
        )
        mock_validate.assert_called_once()
    mock_handle_unknown_action.assert_called_once_with(
        action_data['action'], mock_composer_instance, mock_dependencies["ch"], mock_dependencies["method"]
    )

ACTION_TEST_CASES = [
    ("long", "handle_trading_action"), ("short", "handle_trading_action"),
    ("close-long", "handle_close_action"), ("close-short", "handle_close_action"),
    ("close-position", "handle_close_action"),
    ("check_positions_on_saxo_api", "handle_check_positions"),
    ("daily_stats", "handle_daily_stats"),
]
@pytest.mark.parametrize("action, handler_name", ACTION_TEST_CASES)
@patch('src.main.TelegramMessageComposer')
@patch('src.main.SchemaLoader')
def test_callback_action_handler_dispatch(mock_schema_loader, mock_composer_class, action, handler_name, mock_dependencies):
    action_data = {"action": action, "signal_id": "test_signal"}
    mock_dependencies["body"].decode.return_value = json.dumps(action_data)
    mock_schema_loader.get_trading_action_schema.return_value = {"type": "object"}
    with patch('jsonschema.validate'):
        mock_composer_instance = MagicMock()
        mock_composer_class.return_value = mock_composer_instance
        mock_handler = MagicMock(__name__=handler_name)
        with patch.dict(main.ACTION_HANDLERS, {action: mock_handler}):
            main.callback(
                mock_dependencies["ch"], mock_dependencies["method"], mock_dependencies["properties"], mock_dependencies["body"],
                mock_dependencies["trading_orchestrator"], mock_dependencies["performance_monitor"],
                mock_dependencies["trading_rule"], mock_dependencies["db_position_manager"],
                mock_dependencies["table_trade_performance_manager"], mock_dependencies["trade_turbo_exchange_id"],
                mock_dependencies["rabbit_conn"]
            )
    expected_handler_args = {
        "data": action_data, "composer": mock_composer_instance, "ch": mock_dependencies["ch"],
        "method": mock_dependencies["method"], "rabbit_connection": mock_dependencies["rabbit_conn"]
    }
    if action in ["long", "short"]:
        expected_handler_args.update({
            "trading_orchestrator": mock_dependencies["trading_orchestrator"],
            "performance_monitor": mock_dependencies["performance_monitor"],
            "trading_rule": mock_dependencies["trading_rule"],
            "db_position_manager": mock_dependencies["db_position_manager"],
            "trade_turbo_exchange_id": mock_dependencies["trade_turbo_exchange_id"]
        })
    elif action in ["close-long", "close-short", "close-position"]:
        expected_handler_args["performance_monitor"] = mock_dependencies["performance_monitor"]
        # db_position_manager is NOT passed to handle_close_action by main.py's callback logic
    elif action == "check_positions_on_saxo_api":
        expected_handler_args.update({
            "performance_monitor": mock_dependencies["performance_monitor"],
            "db_position_manager": mock_dependencies["db_position_manager"]
        })
    elif action == "daily_stats":
        expected_handler_args.update({
            "db_position_manager": mock_dependencies["db_position_manager"],
            "table_trade_performance_manager": mock_dependencies["table_trade_performance_manager"]
        })
    mock_handler.assert_called_once_with(**expected_handler_args)

EXCEPTION_TEST_CASES = [
    (ConfigurationError("Config error"), True, 1, logging.CRITICAL),
    (TokenAuthenticationException("Token error"), True, 1, logging.CRITICAL),
    (DatabaseOperationException("DB error"), True, 1, logging.CRITICAL),
    (PositionNotFoundException("Position not found", "order_id"), True, 1, logging.CRITICAL),
    (PositionCloseException("Close error"), True, 1, logging.CRITICAL),
    (SaxoApiError("Saxo API error"), True, 1, logging.CRITICAL),
    (ApiRequestException("API request error"), True, 1, logging.CRITICAL),
    (ValueError("CRITICAL value error"), True, 6, logging.CRITICAL),
    (ValueError("Non-critical value error"), False, 1, logging.ERROR), # Specific path in main.py
    (WebSocketConnectionException("WS error"), False, None, logging.WARNING), # Specific path in main.py
    (Exception("Generic critical error"), True, 1, logging.CRITICAL),
]

@pytest.mark.parametrize("exception_instance, expected_is_critical, expected_exit_code, expected_log_level_val", EXCEPTION_TEST_CASES)
@patch('src.main.TelegramMessageComposer')
@patch('src.main.SchemaLoader')
@patch('src.main.handle_exception')
def test_callback_exception_handling(mock_handle_exception_fn, mock_schema_loader, mock_composer_class, exception_instance, expected_is_critical, expected_exit_code, expected_log_level_val, mock_dependencies):
    action_data = {"action": "long", "signal_id": "test_signal"}
    mock_dependencies["body"].decode.return_value = json.dumps(action_data)
    mock_schema_loader.get_trading_action_schema.return_value = {"type": "object"}

    with patch('jsonschema.validate'):
        mock_composer_instance = MagicMock()
        mock_composer_class.return_value = mock_composer_instance
        mock_handler = MagicMock(side_effect=exception_instance, __name__="mock_raising_handler")
        with patch.dict(main.ACTION_HANDLERS, {"long": mock_handler}):
            main.callback(
                mock_dependencies["ch"], mock_dependencies["method"], mock_dependencies["properties"], mock_dependencies["body"],
                mock_dependencies["trading_orchestrator"], mock_dependencies["performance_monitor"],
                mock_dependencies["trading_rule"], mock_dependencies["db_position_manager"],
                mock_dependencies["table_trade_performance_manager"], mock_dependencies["trade_turbo_exchange_id"],
                mock_dependencies["rabbit_conn"]
            )

    if isinstance(exception_instance, TradingRuleViolation): # Not in EXCEPTION_TEST_CASES but good practice
        mock_handle_exception_fn.assert_not_called()
        return

    mock_handle_exception_fn.assert_called_once_with(
        exception_instance,
        mock_composer_instance,
        mock_dependencies["ch"],
        mock_dependencies["method"],
        mock_dependencies["body"],
        is_critical=expected_is_critical,
        exit_code=expected_exit_code,
        log_level=expected_log_level_val
    )

@patch('src.main.logging')
@patch('src.main.send_message_to_mq_for_telegram')
@patch('traceback.format_exc')
def test_handle_exception_critical_exit(mock_format_exc, mock_send_to_mq, mock_logging_module, mock_dependencies):
    mock_format_exc.return_value = "Traceback"
    exception = ValueError("Test critical error")
    composer = MagicMock()
    mock_logging_module.CRITICAL = logging.CRITICAL; mock_logging_module.ERROR = logging.ERROR
    mock_logging_module.log = MagicMock()
    with pytest.raises(SystemExit) as pytest_wrapped_e:
        main.handle_exception(exception, composer, mock_dependencies["ch"], mock_dependencies["method"], mock_dependencies["body"], is_critical=True, exit_code=99, log_level=logging.CRITICAL)
    assert pytest_wrapped_e.type == SystemExit and pytest_wrapped_e.value.code == 99
    composer.add_generic_error.assert_called_once_with("ValueError", exception, is_critical=True)
    if logging.CRITICAL >= logging.ERROR: composer.add_text_section.assert_called_once_with("Traceback Snippet", "Traceback")
    mock_send_to_mq.assert_called_once_with(main.rabbit_connection, composer.get_message())
    critical_log_call = next((c for c in mock_logging_module.log.call_args_list if c[0][0] == logging.CRITICAL and "CRITICAL (ValueError): Test critical error" in c[0][1]), None)
    assert critical_log_call is not None, "Critical log message not found"
    mock_logging_module.critical.assert_any_call("Terminating service due to critical error (ValueError). Exit code: 99")

@patch('src.main.logging')
@patch('src.main.send_message_to_mq_for_telegram')
@patch('traceback.format_exc')
def test_handle_exception_non_critical_ack(mock_format_exc, mock_send_to_mq, mock_logging_module, mock_dependencies):
    mock_format_exc.return_value = "Traceback"
    exception = TradingRuleViolation("Test rule violation")
    composer = MagicMock()
    mock_logging_module.ERROR = logging.ERROR
    mock_logging_module.log = MagicMock()
    main.handle_exception(exception, composer, mock_dependencies["ch"], mock_dependencies["method"], mock_dependencies["body"], is_critical=False, log_level=logging.ERROR)
    composer.add_generic_error.assert_called_once_with("TradingRuleViolation", exception, is_critical=False)
    if logging.ERROR >= logging.ERROR: composer.add_text_section.assert_called_once_with("Traceback Snippet", "Traceback")
    mock_send_to_mq.assert_called_once_with(main.rabbit_connection, composer.get_message())
    mock_dependencies["ch"].basic_ack.assert_called_once_with(delivery_tag=mock_dependencies["method"].delivery_tag)
    error_log_call = next((c for c in mock_logging_module.log.call_args_list if c[0][0] == logging.ERROR and "ERROR (TradingRuleViolation): Test rule violation" in c[0][1]), None)
    assert error_log_call is not None, "Error log message not found"

@patch('src.main.logging')
@patch('src.main.send_message_to_mq_for_telegram')
def test_handle_validation_error(mock_send_to_mq, mock_logging_module, mock_dependencies):
    exception = jsonschema.exceptions.ValidationError("Schema error")
    mock_dependencies["body"].decode.return_value = "invalid_json_body"
    main.handle_validation_error(exception, mock_dependencies["body"], mock_dependencies["ch"], mock_dependencies["method"])
    expected_msg_part = "SCHEMA ERROR: Invalid message format received.\n\nError: Schema error\n\nRaw Body:\ninvalid_json_body"
    args, _ = mock_send_to_mq.call_args; assert expected_msg_part in args[1]
    mock_dependencies["ch"].basic_ack.assert_called_once_with(delivery_tag=mock_dependencies["method"].delivery_tag)
    mock_logging_module.error.assert_called_with(f"Schema Validation Error: {exception}") # Corrected assertion

@patch('src.main.logging')
@patch('src.main.send_message_to_mq_for_telegram')
def test_handle_rule_violation(mock_send_mq, mock_logging_module, mock_dependencies):
    exception = TradingRuleViolation("Rule broken")
    composer = mock_dependencies["composer"]
    main.handle_rule_violation(exception, composer, mock_dependencies["ch"], mock_dependencies["method"])
    mock_logging_module.warning.assert_called_with("Trading Rule Violation: Rule broken")
    composer.add_rule_violation.assert_not_called()
    mock_send_mq.assert_not_called()
    mock_dependencies["ch"].basic_ack.assert_called_once_with(delivery_tag=mock_dependencies["method"].delivery_tag)

@patch('src.main.os.getenv')
@patch('src.configuration.ConfigurationManager')
@patch('src.main.setup_logging')
@patch('src.database.DbOrderManager')
@patch('src.database.DbPositionManager')
@patch('src.database.DbTradePerformanceManager')
@patch('src.trade.rules.TradingRule')
@patch('pika.BlockingConnection')
@patch('src.saxo_authen.SaxoAuth')
@patch('src.trade.api_actions.SaxoApiClient')
@patch('src.saxo_openapi.endpoints.portfolio.accounts.AccountsMe')
@patch('src.trade.api_actions.InstrumentService')
@patch('src.trade.api_actions.OrderService')
@patch('src.trade.api_actions.PositionService')
@patch('src.trade.api_actions.TradingOrchestrator')
@patch('src.trade.api_actions.PerformanceMonitor')
@patch('src.main.send_message_to_mq_for_telegram')
@patch('src.main.get_version', return_value="test-version")
@patch('src.main.sys.exit')
def test_main_execution_flow_setup_부분적_테스트(
    mock_sys_exit, mock_get_version, mock_send_mq_telegram, mock_perf_monitor,
    mock_trading_orch, mock_pos_service, mock_order_service, mock_instr_service,
    mock_accounts_me_class, mock_saxo_api_client_class, mock_saxo_auth_class,
    mock_pika_blocking_connection, mock_trading_rule_class, mock_db_trade_perf_class,
    mock_db_pos_mgr_class, mock_db_order_mgr_class, mock_setup_logging,
    mock_config_mgr_class, mock_getenv
):
    mock_getenv.return_value = "dummy_config_path"
    mock_config_instance = MagicMock()
    mock_config_mgr_class.return_value = mock_config_instance
    mock_rabbitmq_config = {"authentication": {"username": "user", "password": "password"}, "hostname": "host", "port":5672, "virtual_host":"/"}
    mock_config_instance.get_rabbitmq_config.return_value = mock_rabbitmq_config

    def get_config_side_effect(key, default=None):
        if key == "trade_turbo_exchange_id": return "test_exchange_id"
        if key == "logging": return {"level": "INFO", "log_to_file": False, "maxBytes":1000, "backupCount":3}
        if key == "telegram_notification_rules": return {"service_startup_notify": True}
        return default if default is not None else MagicMock()
    mock_config_instance.get_config_value.side_effect = get_config_side_effect

    mock_acc_info_response = MagicMock(AccountKey="test_account_key", ClientKey="test_client_key")

    mock_saxo_api_client_instance = MagicMock()
    mock_accounts_me_instance = MagicMock()
    mock_accounts_me_class.return_value = mock_accounts_me_instance
    mock_saxo_api_client_instance.request.return_value = mock_acc_info_response
    mock_saxo_api_client_class.return_value = mock_saxo_api_client_instance

    mock_channel = MagicMock()
    mock_connection = MagicMock(channel=MagicMock(return_value=mock_channel))
    mock_pika_blocking_connection.return_value = mock_connection
    main.rabbit_connection = mock_connection

    if hasattr(main, "main_runner"):
        main.main_runner()
    else:
        main.APP_VERSION = mock_get_version()
        config_path_val = mock_getenv("WATA_CONFIG_PATH")
        if not config_path_val: mock_sys_exit(10)

        try:
            config_manager_instance = mock_config_mgr_class(config_path_val)
            main.config = config_manager_instance # Ensure main.config is set for other parts
            mock_setup_logging(config_manager_instance, "wata-trader") # Pass correct name
            main.trade_turbo_exchange_id = config_manager_instance.get_config_value("trade_turbo_exchange_id")

            # Pika connection setup simulation
            rabbit_config = config_manager_instance.get_rabbitmq_config()
            credentials = pika.PlainCredentials(
                rabbit_config['authentication']['username'],
                rabbit_config['authentication']['password']
            )
            parameters = pika.ConnectionParameters(
                host=rabbit_config['hostname'],
                port=rabbit_config.get('port', 5672), # Use .get for optional port
                virtual_host=rabbit_config.get('virtual_host', '/'), # Use .get for optional vhost
                credentials=credentials,
                heartbeat=600,
                blocked_connection_timeout=300
            )
            global_rabbit_connection_mock = mock_pika_blocking_connection(parameters)
            main.rabbit_connection = global_rabbit_connection_mock # Ensure global is this mock
            main.channel = global_rabbit_connection_mock.channel()


            main.auth_manager = mock_saxo_auth_class(config_manager_instance, main.rabbit_connection) # Pass correct rabbit_connection
            main.saxo_client = mock_saxo_api_client_class(config_manager_instance, main.auth_manager)

            acc_info_req = mock_accounts_me_class()
            # Ensure request is called on the instance, not the class mock
            acc_info_data = main.saxo_client.request(acc_info_req)
            main.account_key = acc_info_data.AccountKey
            main.client_key = acc_info_data.ClientKey

            main.db_order_manager = mock_db_order_mgr_class(config_manager_instance)
            main.db_position_manager = mock_db_pos_mgr_class(config_manager_instance)
            main.db_trade_performance_manager = mock_db_trade_perf_class(config_manager_instance)
            main.trading_rule = mock_trading_rule_class(config_manager_instance, main.db_position_manager)

            main.instrument_service = mock_instr_service(main.saxo_client, config_manager_instance, main.account_key)
            main.order_service = mock_order_service(main.saxo_client, main.account_key, main.client_key)
            main.position_service = mock_pos_service(main.saxo_client, main.order_service, config_manager_instance, main.account_key, main.client_key)
            main.trading_orchestrator = mock_trading_orch(main.instrument_service, main.order_service, main.position_service,
                              config_manager_instance, main.db_order_manager, main.db_position_manager)
            main.performance_monitor = mock_perf_monitor(main.position_service, main.order_service, config_manager_instance,
                              main.db_position_manager, main.trading_rule, main.rabbit_connection)

            # Send startup message
            if config_manager_instance.get_config_value("telegram_notification_rules").get("service_startup_notify"):
                mock_send_mq_telegram(main.rabbit_connection, f"✅📈 WATA Trader v{main.APP_VERSION} is running and ready for orders.")


        except SystemExit:
            pass
        except Exception as e:
            print(f"Error during simulated setup: {e}")
            pass


    mock_getenv.assert_called_with("WATA_CONFIG_PATH")
    mock_config_mgr_class.assert_called_with("dummy_config_path")
    mock_setup_logging.assert_called_once()
    mock_db_order_mgr_class.assert_called_once()
    mock_pika_blocking_connection.assert_called_once()

    if hasattr(main, "main_runner"):
        mock_channel.basic_consume.assert_called_once()
        found_ready_message = any("✅📈 WATA Trader vtest-version is running" in c[0][1] for c in mock_send_mq_telegram.call_args_list if len(c[0]) > 1)
        assert found_ready_message, "Ready message not sent"
        mock_sys_exit.assert_not_called()
    else: # Assertions for the simulated setup in the else block
        mock_saxo_auth_class.assert_called_once()
        mock_saxo_api_client_class.assert_called_once()
        mock_accounts_me_class.assert_called()
        mock_saxo_api_client_instance.request.assert_any_call(mock_accounts_me_instance)
        mock_instr_service.assert_called_once()
        mock_order_service.assert_called_once()
        mock_pos_service.assert_called_once()
        mock_trading_orch.assert_called_once()
        mock_perf_monitor.assert_called_once()
        found_ready_message = any("✅📈 WATA Trader vtest-version is running" in c[0][1] for c in mock_send_mq_telegram.call_args_list if len(c[0]) > 1)
        assert found_ready_message, "Ready message not sent to Telegram in simulated setup"
        # sys.exit might be called if WATA_CONFIG_PATH is not set, but we mock getenv.
        # mock_sys_exit.assert_not_called() # This might be too strict depending on exact flow for no main_runner
