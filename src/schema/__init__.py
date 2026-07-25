webhook_schema = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["long", "short", "close-long", "close-short", "close-position", "daily_stats"],
        },
        "indice": {"type": "string"},
        "signal_timestamp": {
            "type": "string",
            "pattern": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        },
        "alert_timestamp": {
            "type": "string",
            "pattern": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 2.0,
        },
    },
    "required": ["action", "indice", "signal_timestamp", "alert_timestamp"],
}

trading_action_schema = {
    "type": "object",
    "properties": {
        "signal_uuid": {"type": "string", "format": "uuid"},
        "action": {
            "type": "string",
            "enum": [
                "long",
                "short",
                "close-long",
                "close-short",
                "close-position",
                "ping_saxo_api",
                "check_positions_on_saxo_api",
                "daily_stats"
            ],
        },
        "indice": {"type": "string"},
        "signal_timestamp": {
            "type": "string",
            "pattern": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        },
        "alert_timestamp": {
            "type": "string",
            "pattern": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        },
        "mqsend_timestamp": {
            "type": "string",
            "pattern": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z",
        },
        "received_timestamp": {
            "type": "string",
            "pattern": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 2.0,
        },
    },
    "required": ["action", "indice", "signal_timestamp", "alert_timestamp"],
}


class SchemaLoader:
    @staticmethod
    def get_webhook_schema():
        return webhook_schema

    @staticmethod
    def get_trading_action_schema():
        return trading_action_schema
