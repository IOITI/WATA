package trade_exceptions

import (
	"encoding/json"
	"fmt" // Corrected: added closing quote
	"strings"
)

// --- Generic Error (not in Python, but can be useful) ---
type GenericError struct {
	Message string
	Details interface{}
}
func (e *GenericError) Error() string {
	if e.Details != nil { return fmt.Sprintf("%s: %v", e.Message, e.Details) }
	return e.Message
}

// --- Trading Specific Exceptions ---

// TradingRuleViolation mirrors the Python exception.
type TradingRuleViolation struct {
	Message string
	RuleID  string
}
func (e *TradingRuleViolation) Error() string {
	return fmt.Sprintf("TradingRuleViolation: %s (Rule: %s)", e.Message, e.RuleID)
}
func NewTradingRuleViolation(message, ruleID string) *TradingRuleViolation {
	return &TradingRuleViolation{Message: message, RuleID: ruleID}
}

// NoTurbosAvailableException mirrors the Python exception.
type NoTurbosAvailableException struct {
	Message       string
	SearchContext map[string]interface{}
}
func (e *NoTurbosAvailableException) Error() string {
	if len(e.SearchContext) > 0 {
		return fmt.Sprintf("NoTurbosAvailableException: %s (Context: %v)", e.Message, e.SearchContext)
	}
	return fmt.Sprintf("NoTurbosAvailableException: %s", e.Message)
}
func NewNoTurbosAvailableException(message string, context map[string]interface{}) *NoTurbosAvailableException {
	return &NoTurbosAvailableException{Message: message, SearchContext: context}
}

// PositionNotFoundException mirrors the Python exception.
type PositionNotFoundException struct {
	Message               string
	OrderID               string
	AssetType             string
	Uic                   int
	Amount                float64
	CancellationAttempted bool
	CancellationSucceeded bool
}
func (e *PositionNotFoundException) Error() string {
	return fmt.Sprintf("PositionNotFoundException: %s (OrderID: %s, Asset: %s/%d, Amount: %f, Cancelled: %t/%t)",
		e.Message, e.OrderID, e.AssetType, e.Uic, e.Amount, e.CancellationAttempted, e.CancellationSucceeded)
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
func NewInsufficientFundsException(message string, available, required float64) *InsufficientFundsException {
	return &InsufficientFundsException{Message: message, AvailableFunds: available, RequiredFunds: required}
}

// ApiRequestException mirrors the Python exception.
type ApiRequestException struct {
	Message    string
	Endpoint   string
	StatusCode int
	Params     map[string]interface{}
	Response   string
}
func (e *ApiRequestException) Error() string {
	return fmt.Sprintf("ApiRequestException: Failed API request to %s (Status: %d): %s. Params: %v. Response: %.100s",
		e.Endpoint, e.StatusCode, e.Message, e.Params, e.Response) // Truncate response
}

// TokenAuthenticationException mirrors the Python exception.
type TokenAuthenticationException struct {
	Message string
	Reason  string
}
func (e *TokenAuthenticationException) Error() string {
	return fmt.Sprintf("TokenAuthenticationException: %s (Reason: %s)", e.Message, e.Reason)
}

// DatabaseOperationException mirrors the Python exception.
type DatabaseOperationException struct {
	Message   string
	Operation string
	Query     string
}
func (e *DatabaseOperationException) Error() string {
	return fmt.Sprintf("DatabaseOperationException: Failed DB operation '%s': %s (Query: %.50s)", e.Operation, e.Message, e.Query) // Truncate query
}

// SaxoErrorDetail mirrors the nested error structure in SaxoApiError.
type SaxoErrorDetail struct {
	ErrorCode string `json:"ErrorCode"`
	Message   string `json:"Message"`
	Field     string `json:"Field,omitempty"` // Sometimes present
}

// SaxoApiError mirrors the Python exception.
type SaxoApiError struct {
	Message          string
	StatusCode       int
	SaxoErrorDetails []SaxoErrorDetail      // A slice as there can be multiple error details
	RawErrorResponse string                 // Store the raw error string for inspection
	RequestDetails   map[string]interface{}
	OrderDetails     map[string]interface{}
}
func (e *SaxoApiError) Error() string {
	var details []string
	for _, d := range e.SaxoErrorDetails {
		details = append(details, fmt.Sprintf("[%s] %s (Field: %s)", d.ErrorCode, d.Message, d.Field))
	}
	detailStr := strings.Join(details, "; ")
	if detailStr == "" && e.RawErrorResponse != "" { // Fallback to raw if no structured details
		var genericSaxoError struct { // Attempt to parse generic Saxo error
			ErrorCode string `json:"ErrorCode"`
			Message string `json:"Message"`
		}
		if json.Unmarshal([]byte(e.RawErrorResponse), &genericSaxoError) == nil {
			if genericSaxoError.ErrorCode != "" || genericSaxoError.Message != "" {
				detailStr = fmt.Sprintf("[%s] %s", genericSaxoError.ErrorCode, genericSaxoError.Message)
			}
		}
		if detailStr == "" { // Still no details, use raw (truncated)
			detailStr = e.RawErrorResponse
			if len(detailStr) > 150 { detailStr = detailStr[:150] + "..." }
		}
	}

	return fmt.Sprintf("SaxoApiError (Status %d): %s. Details: %s. Request: %v. Order: %v",
		e.StatusCode, e.Message, detailStr, e.RequestDetails, e.OrderDetails)
}

// OrderPlacementError mirrors the Python exception.
type OrderPlacementError struct {
	Message      string
	OrderDetails map[string]interface{}
	Reason       string // e.g. "REJECTED_BY_EXCHANGE", "TIMEOUT"
	SaxoError    *SaxoApiError // Embed or include underlying Saxo error if available
}
func (e *OrderPlacementError) Error() string {
	saxoErrStr := ""
	if e.SaxoError != nil {
		saxoErrStr = fmt.Sprintf(" - Saxo Details: %s", e.SaxoError.Error())
	}
	return fmt.Sprintf("OrderPlacementError: Failed to place order: %s (Reason: %s). Order: %v%s",
		e.Message, e.Reason, e.OrderDetails, saxoErrStr)
}

// ConfigurationError mirrors the Python exception.
type ConfigurationError struct {
	Message string
	Key     string
}
func (e *ConfigurationError) Error() string {
	return fmt.Sprintf("ConfigurationError: %s (Key: %s)", e.Message, e.Key)
}

// PositionCloseException mirrors the Python exception.
type PositionCloseException struct {
	Message    string
	PositionID string
	Reason     string
	SaxoError  *SaxoApiError // Embed or include underlying Saxo error if available
}
func (e *PositionCloseException) Error() string {
	saxoErrStr := ""
	if e.SaxoError != nil {
		saxoErrStr = fmt.Sprintf(" - Saxo Details: %s", e.SaxoError.Error())
	}
	return fmt.Sprintf("PositionCloseException: Failed to close position %s: %s (Reason: %s)%s",
		e.PositionID, e.Message, e.Reason, saxoErrStr)
}

// WebSocketConnectionException mirrors the Python exception.
type WebSocketConnectionException struct {
	Message string
	URL     string
	Cause   error // Underlying error
}
func (e *WebSocketConnectionException) Error() string {
	if e.Cause != nil {
		return fmt.Sprintf("WebSocketConnectionException: Error with WebSocket %s: %s (Cause: %v)", e.URL, e.Message, e.Cause)
	}
	return fmt.Sprintf("WebSocketConnectionException: Error with WebSocket %s: %s", e.URL, e.Message)
}
func (e *WebSocketConnectionException) Unwrap() error { return e.Cause }


// NoMarketAvailableException mirrors the Python exception.
type NoMarketAvailableException struct {
    Message string
    Indice string
}
func (e *NoMarketAvailableException) Error() string {
    return fmt.Sprintf("NoMarketAvailableException: %s (Indice: %s)", e.Message, e.Indice)
}
func NewNoMarketAvailableException(message, indice string) *NoMarketAvailableException {
    return &NoMarketAvailableException{Message: message, Indice: indice}
}
