package trade_exceptions

import (
	// "encoding/json" // Removed unused
	"errors"
	// "fmt" // Removed unused
	"strings"
	"testing"
)

func TestTradingRuleViolation(t *testing.T) {
	err := NewTradingRuleViolation("Stop loss too far", "SL001")
	expected := "TradingRuleViolation: Stop loss too far (Rule: SL001)"
	if err.Error() != expected {
		t.Errorf("Expected error string '%s', got '%s'", expected, err.Error())
	}
}

func TestNoTurbosAvailableException(t *testing.T) {
	context := map[string]interface{}{"symbol": "DAX"}
	err := NewNoTurbosAvailableException("No turbos for DAX", context)
	expectedPrefix := "NoTurbosAvailableException: No turbos for DAX"
	if !strings.HasPrefix(err.Error(), expectedPrefix) {
		t.Errorf("Expected error string to start with '%s', got '%s'", expectedPrefix, err.Error())
	}
	if !strings.Contains(err.Error(), "map[symbol:DAX]") {
		t.Errorf("Error string does not contain context details: %s", err.Error())
	}

	errNoContext := NewNoTurbosAvailableException("No turbos at all", nil)
	expectedNoContext := "NoTurbosAvailableException: No turbos at all"
	if errNoContext.Error() != expectedNoContext {
		t.Errorf("Expected error string '%s', got '%s'", expectedNoContext, errNoContext.Error())
	}
}

func TestPositionNotFoundException(t *testing.T) {
	err := &PositionNotFoundException{
		Message:               "Could not find active position",
		OrderID:               "order123",
		AssetType:             "Stock",
		Uic:                   211,
		Amount:                100,
		CancellationAttempted: true,
		CancellationSucceeded: false,
	}
	expected := "PositionNotFoundException: Could not find active position (OrderID: order123, Asset: Stock/211, Amount: 100.000000, Cancelled: true/false)"
	if err.Error() != expected {
		t.Errorf("Expected error string '%s', got '%s'", expected, err.Error())
	}
}

func TestInsufficientFundsException(t *testing.T) {
	err := NewInsufficientFundsException("Cannot place order", 1000.50, 2500.75)
	expected := "InsufficientFundsException: Cannot place order (Available: 1000.50, Required: 2500.75)"
	if err.Error() != expected {
		t.Errorf("Expected error string '%s', got '%s'", expected, err.Error())
	}
}

func TestApiRequestException(t *testing.T) {
	err := &ApiRequestException{
		Message:    "Bad request",
		Endpoint:   "/trade/v1/orders",
		StatusCode: 400,
		Params:     map[string]interface{}{"uic": 123},
		Response:   `{"ErrorCode":"VALIDATION_ERROR","Message":"Uic is invalid"}`,
	}
	expectedPrefix := "ApiRequestException: Failed API request to /trade/v1/orders (Status: 400): Bad request. Params: map[uic:123]. Response: "
	if !strings.HasPrefix(err.Error(), expectedPrefix) {
		t.Errorf("Expected error string to start with '%s', got '%s'", expectedPrefix, err.Error())
	}
	if !strings.Contains(err.Error(), `"ErrorCode":"VALIDATION_ERROR"`) {
		t.Errorf("Error string does not contain response details: %s", err.Error())
	}
}

func TestSaxoApiError(t *testing.T) {
	t.Run("WithDetailsStruct", func(t *testing.T) {
		err := &SaxoApiError{
			Message:    "Order validation failed",
			StatusCode: 400,
			SaxoErrorDetails: []SaxoErrorDetail{
				{ErrorCode: "ValRule01", Message: "Price is too far", Field: "OrderPrice"},
				{ErrorCode: "ValRule02", Message: "Amount too low", Field: "Amount"},
			},
			RequestDetails: map[string]interface{}{"uic": 123},
		}
		// Expected: SaxoApiError (Status 400): Order validation failed. Details: [ValRule01] Price is too far (Field: OrderPrice); [ValRule02] Amount too low (Field: Amount); . Request: map[uic:123]. Order: map[].
		errMsg := err.Error()
		if !strings.Contains(errMsg, "SaxoApiError (Status 400): Order validation failed") {t.Error("Prefix mismatch")}
		if !strings.Contains(errMsg, "[ValRule01] Price is too far (Field: OrderPrice)") {t.Error("Detail 1 mismatch")}
		if !strings.Contains(errMsg, "[ValRule02] Amount too low (Field: Amount)") {t.Error("Detail 2 mismatch")}
		if !strings.Contains(errMsg, "Request: map[uic:123]") {t.Error("Request details mismatch")}
	})

	t.Run("WithRawErrorResponseGeneric", func(t *testing.T) {
		rawResp := `{"ErrorCode":"GEN_ERR","Message":"A generic error occurred"}`
		err := &SaxoApiError{
			Message:          "Generic Saxo failure",
			StatusCode:       500,
			RawErrorResponse: rawResp,
		}
		// Expected: SaxoApiError (Status 500): Generic Saxo failure. Details: [GEN_ERR] A generic error occurred. Request: map[]. Order: map[].
		errMsg := err.Error()
		if !strings.Contains(errMsg, "SaxoApiError (Status 500): Generic Saxo failure") {t.Error("Prefix mismatch")}
		if !strings.Contains(errMsg, "[GEN_ERR] A generic error occurred") {
			t.Errorf("Expected generic error details from raw response, got: %s", errMsg)
		}
	})

	t.Run("WithRawErrorResponseNonStandardJSON", func(t *testing.T) {
		rawResp := `This is not a standard Saxo JSON error, just plain text.`
		err := &SaxoApiError{
			Message:          "Plain text failure",
			StatusCode:       503,
			RawErrorResponse: rawResp,
		}
		// Expected: SaxoApiError (Status 503): Plain text failure. Details: This is not a standard Saxo JSON error, just plain text.. Request: map[]. Order: map[].
		errMsg := err.Error()
		if !strings.Contains(errMsg, "SaxoApiError (Status 503): Plain text failure") {t.Error("Prefix mismatch")}
		if !strings.Contains(errMsg, "Details: This is not a standard Saxo JSON error, just plain text.") { // Truncated if very long
			t.Errorf("Expected raw plain text in details, got: %s", errMsg)
		}
	})
}


func TestOrderPlacementError(t *testing.T) {
	saxoErr := &SaxoApiError{ Message: "Underlying Saxo rejection", StatusCode: 400, SaxoErrorDetails: []SaxoErrorDetail{{ErrorCode: "REJECTED", Message: "Market closed"}} }
	err := &OrderPlacementError{
		Message:      "Could not place limit order",
		OrderDetails: map[string]interface{}{"uic": 789, "price": 1.23},
		Reason:       "REJECTED_BY_EXCHANGE",
		SaxoError:    saxoErr,
	}
	expectedPrefix := "OrderPlacementError: Failed to place order: Could not place limit order (Reason: REJECTED_BY_EXCHANGE). Order: map[price:1.23 uic:789]"
	if !strings.HasPrefix(err.Error(), expectedPrefix) {
		t.Errorf("Expected error string to start with '%s', got '%s'", expectedPrefix, err.Error())
	}
	if !strings.Contains(err.Error(), "Saxo Details: SaxoApiError (Status 400): Underlying Saxo rejection. Details: [REJECTED] Market closed") {
		t.Errorf("Error string does not contain SaxoError details: %s", err.Error())
	}
}

func TestWebSocketConnectionException(t *testing.T) {
	baseErr := errors.New("network timeout")
	err := &WebSocketConnectionException{
		Message: "Failed to connect",
		URL:     "wss://streaming.example.com",
		Cause:   baseErr,
	}
	expected := "WebSocketConnectionException: Error with WebSocket wss://streaming.example.com: Failed to connect (Cause: network timeout)"
	if err.Error() != expected {
		t.Errorf("Expected error string '%s', got '%s'", expected, err.Error())
	}
	if !errors.Is(err, baseErr) {
		t.Error("errors.Is check failed for wrapped Cause")
	}
	if unwrapped := errors.Unwrap(err); unwrapped != baseErr {
		t.Errorf("errors.Unwrap expected %v, got %v", baseErr, unwrapped)
	}

	errNoCause := &WebSocketConnectionException{Message: "Disconnected", URL: "wss://foo"}
	expectedNoCause := "WebSocketConnectionException: Error with WebSocket wss://foo: Disconnected"
	if errNoCause.Error() != expectedNoCause {
		t.Errorf("Expected error string '%s', got '%s'", expectedNoCause, errNoCause.Error())
	}
}

func TestNoMarketAvailableException(t *testing.T) {
	err := NewNoMarketAvailableException("Market for DAX is currently closed", "DAX")
	expected := "NoMarketAvailableException: Market for DAX is currently closed (Indice: DAX)"
	if err.Error() != expected {
		t.Errorf("Expected error string '%s', got '%s'", expected, err.Error())
	}
}

// Test for other error types:
// - TokenAuthenticationException
// - DatabaseOperationException
// - ConfigurationError
// - PositionCloseException

func TestTokenAuthenticationException(t *testing.T) {
	err := &TokenAuthenticationException{Message: "Auth failed", Reason: "Token expired"}
	expected := "TokenAuthenticationException: Auth failed (Reason: Token expired)"
	if err.Error() != expected {
		t.Errorf("Expected: %s, Got: %s", expected, err.Error())
	}
}

func TestDatabaseOperationException(t *testing.T) {
	err := &DatabaseOperationException{Message: "Record not found", Operation: "SELECT", Query: "SELECT * FROM users WHERE id=1"}
	expected := `DatabaseOperationException: Failed DB operation 'SELECT': Record not found (Query: SELECT * FROM users WHERE id=1)`
	if err.Error() != expected {
		t.Errorf("Expected: %s, Got: %s", expected, err.Error())
	}
}

func TestConfigurationError(t *testing.T) {
	err := &ConfigurationError{Message: "Missing API key", Key: "services.saxo.apiKey"}
	expected := "ConfigurationError: Missing API key (Key: services.saxo.apiKey)"
	if err.Error() != expected {
		t.Errorf("Expected: %s, Got: %s", expected, err.Error())
	}
}

func TestPositionCloseException(t *testing.T) {
	err := &PositionCloseException{Message: "Market closed", PositionID: "pos123", Reason: "MARKET_CLOSED"}
	expected := "PositionCloseException: Failed to close position pos123: Market closed (Reason: MARKET_CLOSED)"
	if err.Error() != expected {
		t.Errorf("Expected: %s, Got: %s", expected, err.Error())
	}
}
