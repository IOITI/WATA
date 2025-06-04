package trade_exceptions

import "fmt"

// GenericError is a base for other errors if needed, or just use specific types.
type GenericError struct {
	Message string
	Details interface{} // Can hold arbitrary details
}

func (e *GenericError) Error() string {
	if e.Details != nil {
		return fmt.Sprintf("%s: %v", e.Message, e.Details)
	}
	return e.Message
}

// TradingRuleViolation mirrors the Python exception.
type TradingRuleViolation struct {
	Message string
	RuleID  string // Example field, adjust if message_helper needs more
}

func (e *TradingRuleViolation) Error() string {
	return fmt.Sprintf("TradingRuleViolation: %s (Rule: %s)", e.Message, e.RuleID)
}

// NoTurbosAvailableException mirrors the Python exception.
type NoTurbosAvailableException struct {
	Message     string
	SearchQuery map[string]interface{} // Example field
}

func (e *NoTurbosAvailableException) Error() string {
	return e.Message
}

// InsufficientFundsException mirrors the Python exception.
type InsufficientFundsException struct {
	Message        string
	AvailableFunds float64
	RequiredFunds  float64
}

func (e *InsufficientFundsException) Error() string {
	return fmt.Sprintf("InsufficientFundsException: %s (Available: %.2f, Required: %.2f)", e.Message, e.AvailableFunds, e.RequiredFunds)
}

// ApiRequestException mirrors the Python exception.
type ApiRequestException struct {
	Message    string
	Endpoint   string
	StatusCode int
	Params     map[string]interface{}
	Response   string // Store raw response if needed
}

func (e *ApiRequestException) Error() string {
	return fmt.Sprintf("ApiRequestException: Failed API request to %s (Status: %d): %s. Params: %v", e.Endpoint, e.StatusCode, e.Message, e.Params)
}

// TokenAuthenticationException mirrors the Python exception.
type TokenAuthenticationException struct {
	Message string
	Reason  string // e.g., "expired", "invalid"
}

func (e *TokenAuthenticationException) Error() string {
	return fmt.Sprintf("TokenAuthenticationException: %s (Reason: %s)", e.Message, e.Reason)
}

// DatabaseOperationException mirrors the Python exception.
type DatabaseOperationException struct {
	Message   string
	Operation string // e.g., "SELECT", "INSERT"
	Query     string // Potentially sensitive, use with caution
}

func (e *DatabaseOperationException) Error() string {
	return fmt.Sprintf("DatabaseOperationException: Failed DB operation '%s': %s", e.Operation, e.Message)
}

// PositionCloseException mirrors the Python exception.
type PositionCloseException struct {
	Message    string
	PositionID string
	Reason     string
}

func (e *PositionCloseException) Error() string {
	return fmt.Sprintf("PositionCloseException: Failed to close position %s: %s (Reason: %s)", e.PositionID, e.Message, e.Reason)
}

// WebSocketConnectionException mirrors the Python exception.
type WebSocketConnectionException struct {
	Message string
	URL     string
}

func (e *WebSocketConnectionException) Error() string {
	return fmt.Sprintf("WebSocketConnectionException: Error with WebSocket %s: %s", e.URL, e.Message)
}

// SaxoErrorDetail mirrors the nested error structure in SaxoApiError.
type SaxoErrorDetail struct {
	ErrorCode string `json:"ErrorCode"` // e.g. "NO_LIQUIDITY"
	Message   string `json:"Message"`   // e.g. "No liquidity available for this instrument at the moment."
	// Add other fields from Saxo's error details if needed
}

// SaxoApiError mirrors the Python exception.
type SaxoApiError struct {
	Message          string
	StatusCode       int
	SaxoErrorDetails []SaxoErrorDetail      // A slice as there can be multiple error details
	RequestDetails   map[string]interface{} // e.g. URL, method, headers, body
	OrderDetails     map[string]interface{} // Details specific to the order if it was an order placement error
}

func (e *SaxoApiError) Error() string {
	errorDetailsStr := ""
	if len(e.SaxoErrorDetails) > 0 {
		for _, d := range e.SaxoErrorDetails {
			errorDetailsStr += fmt.Sprintf("[%s: %s] ", d.ErrorCode, d.Message)
		}
	}
	return fmt.Sprintf("SaxoApiError (Status %d): %s. Details: %s. Request: %v. Order: %v",
		e.StatusCode, e.Message, errorDetailsStr, e.RequestDetails, e.OrderDetails)
}

// OrderPlacementError mirrors the Python exception.
type OrderPlacementError struct {
	Message      string
	OrderDetails map[string]interface{}
	Reason       string // e.g. "REJECTED_BY_EXCHANGE", "TIMEOUT"
}

func (e *OrderPlacementError) Error() string {
	return fmt.Sprintf("OrderPlacementError: Failed to place order: %s (Reason: %s). Order: %v", e.Message, e.Reason, e.OrderDetails)
}

// ConfigurationError mirrors the Python exception.
type ConfigurationError struct {
	Message string
	Key     string // Config key that was problematic
}

func (e *ConfigurationError) Error() string {
	return fmt.Sprintf("ConfigurationError: %s (Key: %s)", e.Message, e.Key)
}

// PositionNotFoundException mirrors the Python exception.
type PositionNotFoundException struct {
	Message    string
	PositionID string
}

func (e *PositionNotFoundException) Error() string {
	return fmt.Sprintf("PositionNotFoundException: Position %s not found: %s", e.PositionID, e.Message)
}
