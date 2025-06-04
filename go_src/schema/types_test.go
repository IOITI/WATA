package schema

import (
	"encoding/json"
	"fmt" // Added import
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestWebhookAction_UnmarshalJSON(t *testing.T) {
	testCases := []struct {
		name        string
		jsonData    string
		expected    WebhookAction
		expectError bool
		errorMsg    string
	}{
		{"ValidLong", `"long"`, ActionLong, false, ""},
		{"ValidShort", `"short"`, ActionShort, false, ""},
		{"ValidStop", `"stop"`, ActionStop, false, ""},
		{"ValidTest", `"test"`, ActionTest, false, ""},
		{"ValidLongUppercase", `"LONG"`, ActionLong, false, ""}, // Should normalize
		{"InvalidAction", `"unknown"`, "", true, "invalid WebhookAction: 'unknown'"},
		{"NotAString", `123`, "", true, "WebhookAction should be a string"},
		{"EmptyString", `""`, "", true, "invalid WebhookAction: ''"},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			var wa WebhookAction
			err := json.Unmarshal([]byte(tc.jsonData), &wa)

			if tc.expectError {
				if err == nil {
					t.Fatalf("Expected an error, but got nil")
				}
				if !strings.Contains(err.Error(), tc.errorMsg) {
					t.Errorf("Expected error message to contain '%s', got '%v'", tc.errorMsg, err)
				}
			} else {
				if err != nil {
					t.Fatalf("Did not expect an error, but got: %v", err)
				}
				if wa != tc.expected {
					t.Errorf("Expected WebhookAction %s, got %s", tc.expected, wa)
				}
			}
		})
	}
}

func TestWebhookPayload_Validate(t *testing.T) {
	now := time.Now().UTC()
	validPayload := WebhookPayload{
		Action:          ActionLong,
		Indice:          "CAC40",
		SignalTimestamp: now.Add(-time.Minute),
		AlertTimestamp:  now,
	}

	testCases := []struct {
		name        string
		payload     WebhookPayload
		expectError bool
		errorMsg    string
	}{
		{"Valid", validPayload, false, ""},
		{
			name:        "MissingAction",
			payload:     func() WebhookPayload { p := validPayload; p.Action = ""; return p }(),
			expectError: true, errorMsg: "action is required",
		},
		{
			name:        "MissingIndice",
			payload:     func() WebhookPayload { p := validPayload; p.Indice = " "; return p }(),
			expectError: true, errorMsg: "indice is required",
		},
		{
			name:        "ZeroSignalTimestamp",
			payload:     func() WebhookPayload { p := validPayload; p.SignalTimestamp = time.Time{}; return p }(),
			expectError: true, errorMsg: "signal_timestamp is required",
		},
		{
			name:        "ZeroAlertTimestamp",
			payload:     func() WebhookPayload { p := validPayload; p.AlertTimestamp = time.Time{}; return p }(),
			expectError: true, errorMsg: "alert_timestamp is required",
		},
		{
			name:        "AlertBeforeSignal",
			payload:     func() WebhookPayload { p := validPayload; p.AlertTimestamp = p.SignalTimestamp.Add(-time.Second); return p }(),
			expectError: true, errorMsg: "alert_timestamp cannot be before signal_timestamp",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.payload.Validate()
			if tc.expectError {
				if err == nil {
					t.Fatalf("Expected validation error, but got nil")
				}
				if !strings.Contains(err.Error(), tc.errorMsg) {
					t.Errorf("Expected error message containing '%s', got '%v'", tc.errorMsg, err)
				}
			} else {
				if err != nil {
					t.Fatalf("Did not expect validation error, but got: %v", err)
				}
			}
		})
	}
}

func TestWebhookPayload_UnmarshalJSON(t *testing.T) {
	validJson := `{"action": "long", "indice": "DAX40", "signal_timestamp": "2023-01-01T10:00:00Z", "alert_timestamp": "2023-01-01T10:00:05Z"}`
	var payload WebhookPayload
	err := json.Unmarshal([]byte(validJson), &payload)
	if err != nil {
		t.Fatalf("Failed to unmarshal valid JSON: %v", err)
	}
	if payload.Action != ActionLong {t.Errorf("Action mismatch")}
	if payload.Indice != "DAX40" {t.Errorf("Indice mismatch")}
	// Further checks on timestamps if needed, default time.Time unmarshalling handles RFC3339

	invalidActionJson := `{"action": "invalid_action", "indice": "DAX40", "signal_timestamp": "2023-01-01T10:00:00Z", "alert_timestamp": "2023-01-01T10:00:05Z"}`
	err = json.Unmarshal([]byte(invalidActionJson), &payload)
	if err == nil {
		t.Fatal("Expected error for invalid action in JSON, got nil")
	}
	if !strings.Contains(err.Error(), "invalid WebhookAction") {
		t.Errorf("Expected specific error for invalid action, got: %v", err)
	}

	invalidTimestampJson := `{"action": "long", "indice": "DAX40", "signal_timestamp": "not-a-time", "alert_timestamp": "2023-01-01T10:00:05Z"}`
	err = json.Unmarshal([]byte(invalidTimestampJson), &payload)
	if err == nil {
		t.Fatal("Expected error for invalid timestamp format, got nil")
	}
	// Error message from time.Time.UnmarshalJSON can be complex, check for a known part
	if !strings.Contains(err.Error(), "cannot parse") && !strings.Contains(err.Error(),"parsing time") {
		t.Errorf("Expected error related to time parsing, got: %v", err)
	}
}


func TestTradingAction_UnmarshalJSON(t *testing.T) {
	testCases := []struct {
		name        string
		jsonData    string
		expected    TradingAction
		expectError bool
		errorMsg    string
	}{
		{"ValidPlaceOrder", `"place_order"`, ActionPlaceOrder, false, ""},
		{"ValidCheckPositions", `"check_positions_on_saxo_api"`, ActionCheckPositions, false, ""},
		{"ValidUppercase", `"PROCESS_WEBHOOK"`, ActionProcessWebhook, false, ""}, // Should normalize
		{"InvalidAction", `"unknown_trade_action"`, "", true, "invalid TradingAction: 'unknown_trade_action'"},
		{"NotAString", `true`, "", true, "TradingAction should be a string"},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			var ta TradingAction
			err := json.Unmarshal([]byte(tc.jsonData), &ta)
			if tc.expectError {
				if err == nil {t.Fatalf("Expected an error, got nil")}
				if !strings.Contains(err.Error(), tc.errorMsg) {
					t.Errorf("Expected error msg containing '%s', got '%v'", tc.errorMsg, err)
				}
			} else {
				if err != nil {t.Fatalf("Did not expect an error, got: %v", err)}
				if ta != tc.expected {t.Errorf("Expected TradingAction %s, got %s", tc.expected, ta)}
			}
		})
	}
}

func TestTradingActionPayload_Validate(t *testing.T) {
	now := time.Now().UTC()
	validSignalID := uuid.New()
	validWebhook := &WebhookPayload{
		Action: ActionShort, Indice: "FTSE100",
		SignalTimestamp: now.Add(-2*time.Minute), AlertTimestamp: now.Add(-time.Minute),
	}

	validPayload := TradingActionPayload{
		SignalID:        validSignalID,
		Action:          ActionProcessWebhook,
		Indice:          "FTSE100",
		SignalTimestamp: now.Add(-2 * time.Minute),
		AlertTimestamp:  now.Add(-time.Minute),
		MQSendTimestamp: now,
		WebhookRaw:      validWebhook,
	}

	testCases := []struct {
		name        string
		payload     TradingActionPayload
		expectError bool
		errorMsg    string
	}{
		{"Valid", validPayload, false, ""},
		{"NilSignalID", func() TradingActionPayload { p := validPayload; p.SignalID = uuid.Nil; return p }(), true, "signal_id is required"},
		{"MissingAction", func() TradingActionPayload { p := validPayload; p.Action = ""; return p }(), true, "action is required"},
		{"MissingIndiceForProcessWebhook", func() TradingActionPayload { p := validPayload; p.Indice = ""; return p }(), true, "indice is required for action 'process_webhook'"},
		{"ZeroSignalTimestamp", func() TradingActionPayload { p := validPayload; p.SignalTimestamp = time.Time{}; return p }(), true, "signal_timestamp is required"},
		{"MQSendBeforeAlert", func() TradingActionPayload { p := validPayload; p.MQSendTimestamp = p.AlertTimestamp.Add(-time.Second); return p }(), true, "mqsend_timestamp cannot be before alert_timestamp"},
		{"MissingWebhookRawForProcessWebhook", func() TradingActionPayload { p := validPayload; p.WebhookRaw = nil; return p }(), true, "webhook_payload is required for action 'process_webhook'"},
		{"InvalidWebhookRaw", func() TradingActionPayload { p := validPayload; p.WebhookRaw = &WebhookPayload{Indice: " "}; return p }(), true, "webhook_payload validation failed: action is required"},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.payload.Validate()
			if tc.expectError {
				if err == nil {t.Fatalf("Expected validation error, got nil")}
				if !strings.Contains(err.Error(), tc.errorMsg) {
					t.Errorf("Expected error msg containing '%s', got '%v'", tc.errorMsg, err)
				}
			} else {
				if err != nil {t.Fatalf("Did not expect validation error, got: %v", err)}
			}
		})
	}
}

func TestTradingActionPayload_UnmarshalJSON(t *testing.T) {
	signalID := uuid.New()
	now := time.Now().UTC()
	st := now.Add(-time.Minute).Format(time.RFC3339Nano)
	at := now.Format(time.RFC3339Nano)
	mt := now.Add(time.Second).Format(time.RFC3339Nano)

	validJson := fmt.Sprintf(`{
		"signal_id": "%s",
		"action": "place_order",
		"indice": "SMI20",
		"signal_timestamp": "%s",
		"alert_timestamp": "%s",
		"mqsend_timestamp": "%s"
	}`, signalID, st, at, mt)

	var payload TradingActionPayload
	err := json.Unmarshal([]byte(validJson), &payload)
	if err != nil {t.Fatalf("Failed to unmarshal valid JSON for TradingActionPayload: %v", err)}
	if payload.SignalID != signalID {t.Error("SignalID mismatch")}
	if payload.Action != ActionPlaceOrder {t.Error("Action mismatch")}

	// Corrected invalidUUIDJson to be valid JSON, but with an invalid UUID format
	invalidUUIDJson := fmt.Sprintf(`{
		"signal_id": "not-a-uuid-string",
		"action": "place_order",
		"indice": "SMI20",
		"signal_timestamp": "%s",
		"alert_timestamp": "%s",
		"mqsend_timestamp": "%s"
	}`, st, at, mt) // Use previously defined timestamps for valid structure

	err = json.Unmarshal([]byte(invalidUUIDJson), &payload)
	if err == nil {t.Fatal("Expected error for invalid UUID format")}

	// Check for the specific error from uuid.UnmarshalText
	// uuid.Parse typically returns "invalid UUID length" or "invalid UUID format"
	if !strings.Contains(err.Error(), "invalid UUID") && !strings.Contains(err.Error(), "cannot unmarshal") {
		t.Errorf("Expected error about invalid UUID format or unmarshal error, got: %v", err)
	}
}
