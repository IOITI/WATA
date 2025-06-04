package message_helper

import (
	"encoding/json"
	"errors" // Go's standard errors package
	"pymath/go_src/trade_exceptions"
	"strings"
	"testing"
	"time"
)

func TestNewTelegramMessageComposer(t *testing.T) {
	signalData := map[string]interface{}{"type": "test_signal", "value": 123}
	rawTs := time.Now().UTC().Format(time.RFC3339Nano)
	tzStr := "Europe/Paris"

	composer, err := NewTelegramMessageComposer(signalData, rawTs, tzStr)
	if err != nil {
		t.Fatalf("NewTelegramMessageComposer failed: %v", err)
	}

	if composer == nil {
		t.Fatal("Composer is nil")
	}
	if composer.timezone.String() != tzStr {
		t.Errorf("Expected timezone %s, got %s", tzStr, composer.timezone.String())
	}
	if composer.signalTimestampRaw != rawTs {
		t.Errorf("signalTimestampRaw mismatch")
	}
	if composer.signalTimestamp == nil {
		t.Error("signalTimestamp should be parsed")
	}
	if len(composer.sections) != 1 { // Initial signal section
		t.Errorf("Expected 1 initial section, got %d", len(composer.sections))
	}
	if !strings.Contains(composer.sections[0], "--- SIGNAL") {
		t.Error("Initial section doesn't seem to be signal section")
	}
	if !strings.Contains(composer.sections[0], `"type": "test_signal"`) {
		t.Error("Signal data not found in initial section")
	}

	// Test with invalid timezone
	_, err = NewTelegramMessageComposer(signalData, rawTs, "Invalid/Timezone")
	if err != nil {
		// The constructor currently prints a warning and uses default, doesn't return error for this.
		// To test this properly, it should return an error or the test should check for the fallback.
		t.Logf("NewTelegramMessageComposer with invalid timezone (expected fallback, no error returned by current impl): %v", err)
	}
}

func TestParseTimestamp(t *testing.T) {
	composer, _ := NewTelegramMessageComposer(map[string]interface{}{}, "", "UTC")
	testCases := []struct {
		name      string
		input     string
		expectNil bool
		expected  time.Time // if not nil, compare this UTC value
	}{
		{"RFC3339Nano", "2023-10-26T10:30:45.123456789Z", false, time.Date(2023, 10, 26, 10, 30, 45, 123456789, time.UTC)},
		{"RFC3339", "2023-10-26T10:30:45Z", false, time.Date(2023, 10, 26, 10, 30, 45, 0, time.UTC)},
		{"ISOWithOffset", "2023-10-26T12:30:45.123+02:00", false, time.Date(2023, 10, 26, 10, 30, 45, 123000000, time.UTC)},
		{"ISOWithOffsetNoMillis", "2023-10-26T12:30:45+02:00", false, time.Date(2023, 10, 26, 10, 30, 45, 0, time.UTC)},
		{"EmptyString", "", true, time.Time{}},
		{"InvalidString", "not a timestamp", true, time.Time{}},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			parsedTime := composer.parseTimestamp(tc.input)
			if tc.expectNil {
				if parsedTime != nil {
					t.Errorf("Expected nil, got %v", parsedTime)
				}
			} else {
				if parsedTime == nil {
					t.Fatalf("Expected non-nil time, got nil")
				}
				if !parsedTime.Equal(tc.expected) {
					t.Errorf("Expected time %v, got %v", tc.expected, *parsedTime)
				}
			}
		})
	}
}

func TestFormatTimestamp(t *testing.T) {
	tzParis, _ := time.LoadLocation("Europe/Paris")
	composer, _ := NewTelegramMessageComposer(map[string]interface{}{}, "", "Europe/Paris")

	utcTime := time.Date(2023, 10, 26, 10, 30, 0, 0, time.UTC)
	// expectedFormat := "2023-10-26 12:30:00 CEST" // This variable was unused

	formatted := composer.formatTimestamp(&utcTime)
	// Note: The actual zone abbreviation (CEST/CET) depends on the date and Go's timezone data.
	// It's safer to check the offset or the time itself in the specific zone.
	parsedBack, _ := time.ParseInLocation("2006-01-02 15:04:05 MST", formatted, tzParis)
	if !parsedBack.Equal(utcTime.In(tzParis)) {
		t.Errorf("Expected formatted time to represent %v in Paris, got %s (parsed back to %v)", utcTime, formatted, parsedBack)
	}
	if composer.formatTimestamp(nil) != "N/A" {
		t.Errorf("Expected 'N/A' for nil timestamp, got %s", composer.formatTimestamp(nil))
	}
}

func TestCalculateTimeDiff(t *testing.T) {
	composer, _ := NewTelegramMessageComposer(map[string]interface{}{}, "", "UTC")
	t1 := time.Date(2023, 1, 1, 10, 0, 0, 0, time.UTC)
	t2 := time.Date(2023, 1, 1, 10, 0, 5, 500000000, time.UTC) // 5.5 seconds later

	diff := composer.calculateTimeDiff(&t1, &t2)
	if diff != "5.500 seconds" {
		t.Errorf("Expected diff '5.500 seconds', got '%s'", diff)
	}
	if composer.calculateTimeDiff(nil, &t2) != "N/A" {
		t.Error("Expected 'N/A' for nil start time")
	}
	if composer.calculateTimeDiff(&t1, nil) != "N/A" {
		t.Error("Expected 'N/A' for nil end time")
	}
	if composer.calculateTimeDiff(&t2, &t1) != "end time before start time" {
		t.Error("Expected 'end time before start time' for reversed times")
	}
}

func TestAddTurboSearchResult(t *testing.T) {
	composer, _ := NewTelegramMessageComposer(map[string]interface{}{}, "", "UTC")
	searchContext := map[string]interface{}{"instrument": "DE40"}

	t.Run("Success", func(t *testing.T) {
		turbo := map[string]interface{}{"name": "Turbo X", "isin": "DE000XXXXX"}
		composer.AddTurboSearchResult(turbo, nil, searchContext)
		msg := composer.GetMessage()
		if !strings.Contains(msg, "--- TURBO SEARCH ---") {
			t.Error("Missing turbo search section title")
		}
		if !strings.Contains(msg, `"name": "Turbo X"`) {
			t.Error("Missing turbo details")
		}
		if !strings.Contains(msg, "Context: map[instrument:DE40]") {
			t.Error("Missing search context")
		}
		composer.sections = []string{} // Reset for next sub-test
	})

	t.Run("NoTurbosAvailableException", func(t *testing.T) {
		err := &trade_exceptions.NoTurbosAvailableException{Message: "No turbos found for DE40"}
		composer.AddTurboSearchResult(nil, err, searchContext)
		msg := composer.GetMessage()
		if !strings.Contains(msg, "Error: No turbos found for DE40") {
			t.Error("Missing error message for NoTurbosAvailableException")
		}
		if !strings.Contains(msg, "Type: NoTurbosAvailableException") {
			t.Error("Missing type detail for NoTurbosAvailableException")
		}
		composer.sections = []string{}
	})

	t.Run("SaxoApiError", func(t *testing.T) {
		err := &trade_exceptions.SaxoApiError{
			Message:    "Saxo API failed",
			StatusCode: 500,
			SaxoErrorDetails: []trade_exceptions.SaxoErrorDetail{
				{ErrorCode: "NO_LIQ", Message: "No liquidity"},
			},
		}
		composer.AddTurboSearchResult(nil, err, searchContext)
		msg := composer.GetMessage()
		if !strings.Contains(msg, "Type: SaxoApiError") {
			t.Error("Missing type detail for SaxoApiError")
		}
		if !strings.Contains(msg, "[NO_LIQ] No liquidity") {
			t.Error("Missing Saxo error detail")
		}
		composer.sections = []string{}
	})

	t.Run("NilTurboAndNoError", func(t *testing.T) {
		composer.AddTurboSearchResult(nil, nil, searchContext)
		msg := composer.GetMessage()
		if !strings.Contains(msg, "No turbo found (nil or empty map returned).") {
			t.Error("Missing message for nil turbo and no error")
		}
		composer.sections = []string{}
	})
}

func TestAddPositionResult(t *testing.T) {
	composer, _ := NewTelegramMessageComposer(map[string]interface{}{}, "", "UTC")
	initialSignalTime := time.Date(2023,1,1,10,0,0,0,time.UTC)
	composer.signalTimestamp = &initialSignalTime

	t.Run("Success", func(t *testing.T) {
		buyDetails := map[string]interface{}{
			"price": 120.5,
			"quantity": 10,
			"timestamps": map[string]interface{}{ // Nested map for timestamps
				"ask": time.Date(2023,1,1,10,0,2,0,time.UTC).Format(time.RFC3339Nano),
				"execution": time.Date(2023,1,1,10,0,3,0,time.UTC).Format(time.RFC3339Nano),
			},
		}
		composer.AddPositionResult(buyDetails, nil, "order123", 5000.0, 1205.0)
		msg := composer.GetMessage()

		if !strings.Contains(msg, "--- POSITION RESULT ---") {t.Error("Missing position result section title")}
		if !strings.Contains(msg, "Order ID: order123") {t.Error("Missing order ID")}
		if !strings.Contains(msg, "Position Opened Successfully:") {t.Error("Missing success message")}
		if !strings.Contains(msg, `"price": 120.5`) {t.Error("Missing buy details")}
		if !strings.Contains(msg, "Signal->Ask Diff : 2.000 seconds") {t.Error("Incorrect Signal->Ask Diff")}
		composer.sections = []string{}
	})

	t.Run("InsufficientFundsException", func(t *testing.T) {
		err := &trade_exceptions.InsufficientFundsException{Message: "Not enough money", AvailableFunds: 100.0, RequiredFunds: 1000.0}
		composer.AddPositionResult(nil, err, "orderError1", 100.0, 1000.0)
		msg := composer.GetMessage()
		if !strings.Contains(msg, "Type: InsufficientFundsException") {t.Error("Missing type detail for InsufficientFundsException")}
		if !strings.Contains(msg, "Available: 100.00, Required: 1000.00") {t.Error("Missing fund details")}
		composer.sections = []string{}
	})

	t.Run("SaxoApiErrorWithOrderDetails", func(t *testing.T) {
		err := &trade_exceptions.SaxoApiError{
			Message: "Order rejected",
			StatusCode: 400,
			SaxoErrorDetails: []trade_exceptions.SaxoErrorDetail{{ErrorCode: "ORD_REJ", Message: "Order was rejected"}},
			OrderDetails: map[string]interface{}{"AssetType": "Stock"},
		}
		composer.AddPositionResult(nil, err, "orderSaxoFail", 5000, 1000)
		msg := composer.GetMessage()
		if !strings.Contains(msg, "Type: SaxoApiError") {t.Error("Missing type detail for SaxoApiError")}
		if !strings.Contains(msg, "[ORD_REJ] Order was rejected") {t.Error("Missing saxo error details")}
		if !strings.Contains(msg, "Order Attempt Details: map[AssetType:Stock]") {t.Error("Missing order attempt details from SaxoApiError")}
		composer.sections = []string{}
	})
}

func TestAddGenericError(t *testing.T) {
	composer, _ := NewTelegramMessageComposer(map[string]interface{}{}, "", "UTC")
	err := errors.New("a generic failure")
	composer.AddGenericError("Test Context", err, true)
	msg := composer.GetMessage()

	if !strings.Contains(msg, "--- ERROR (Test Context) [CRITICAL] ---") {t.Error("Missing generic error title or criticality")}
	if !strings.Contains(msg, "a generic failure") {t.Error("Missing generic error message")}
}

func TestAddRuleViolation(t *testing.T) {
	composer, _ := NewTelegramMessageComposer(map[string]interface{}{}, "", "UTC")
	err := &trade_exceptions.TradingRuleViolation{RuleID: "R001", Message: "Stop loss too far"}
	composer.AddRuleViolation(err)
	msg := composer.GetMessage()

	if !strings.Contains(msg, "--- TRADING RULE VIOLATION ---") {t.Error("Missing rule violation title")}
	if !strings.Contains(msg, "Rule ID: R001") {t.Error("Missing rule ID")}
	if !strings.Contains(msg, "Message: Stop loss too far") {t.Error("Missing rule message")}

	// Test with wrong error type
	composer.sections = []string{}
	plainErr := errors.New("not a TradingRuleViolation")
	composer.AddRuleViolation(plainErr)
	msg = composer.GetMessage()
	if !strings.Contains(msg, "--- ERROR (TradingRuleViolation (unexpected type)) [INFO] ---") {
		t.Error("Generic error section not added for wrong error type to AddRuleViolation")
	}
}

func TestAddTextSection(t *testing.T) {
	composer, _ := NewTelegramMessageComposer(map[string]interface{}{}, "", "UTC")
	composer.AddTextSection("Custom Info", "This is some text.")
	msg := composer.GetMessage()
	if !strings.Contains(msg, "--- CUSTOM INFO ---") {t.Error("Missing text section title")}
	if !strings.Contains(msg, "This is some text.") {t.Error("Missing text content")}
}

func TestAddDictSection(t *testing.T) {
	composer, _ := NewTelegramMessageComposer(map[string]interface{}{}, "", "UTC")
	data := map[string]interface{}{"key": "value", "number": 123.45}
	composer.AddDictSection("Data Dump", data)
	msg := composer.GetMessage()

	expectedJson, _ := json.MarshalIndent(data, "", "  ")

	if !strings.Contains(msg, "--- DATA DUMP ---") {t.Error("Missing dict section title")}
	if !strings.Contains(msg, string(expectedJson)) {t.Errorf("Missing dict content. Expected:\n%s\nGot:\n%s", expectedJson, msg)}
}

func TestSafeGetNestedString(t *testing.T) {
	data := map[string]interface{}{
		"level1_key": "level1_value",
		"level1_map": map[string]interface{}{
			"level2_key": "level2_value",
			"level2_map": map[string]interface{}{
				"level3_key": "level3_value",
			},
			"level2_int": 123,
		},
	}
	if safeGetNestedString(data, "level1_key") != "level1_value" { t.Error("Failed level1_key") }
	if safeGetNestedString(data, "level1_map", "level2_key") != "level2_value" { t.Error("Failed level1_map.level2_key") }
	if safeGetNestedString(data, "level1_map", "level2_map", "level3_key") != "level3_value" { t.Error("Failed level1_map.level2_map.level3_key") }

	if safeGetNestedString(data, "non_existent") != "" { t.Error("Failed non_existent") }
	if safeGetNestedString(data, "level1_map", "non_existent") != "" { t.Error("Failed level1_map.non_existent") }
	if safeGetNestedString(data, "level1_map", "level2_int") != "" { t.Error("Failed level1_map.level2_int (not a string)")}
	if safeGetNestedString(data, "level1_key", "too_deep") != "" { t.Error("Failed level1_key.too_deep (not a map)")}

}
