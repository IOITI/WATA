package saxo_openapi

import (
	"encoding/json"
	"errors"
	"fmt"
)

// SaxoErrorDetail is a common structure for detailed error messages from Saxo.
type SaxoErrorDetail struct {
	ErrorCode string `json:"ErrorCode"`
	Message   string `json:"Message"`
}

// SaxoErrorMessage is a common structure for the top-level error object from Saxo.
// It's used to parse JSON error responses from the API.
type SaxoErrorMessage struct {
	MessageID    string              `json:"MessageId,omitempty"` // Often a GUID
	ErrorCode    string              `json:"ErrorCode,omitempty"` // e.g., "IllegalInputArgument", "ReferenceDataNotFound"
	Message      string              `json:"Message,omitempty"`   // Human-readable top-level message
	ErrorDetails json.RawMessage       `json:"Details,omitempty"`   // Can be string or SaxoErrorDetail array or object
	ModelState   map[string][]string `json:"ModelState,omitempty"` // For validation errors
}


// OpenAPIError represents an error from the Saxo OpenAPI.
type OpenAPIError struct {
	Code         int    // HTTP status code
	Reason       string // HTTP status reason (e.g., "Bad Request")
	RawContent   string // Raw response content, useful if JSON parsing of SaxoErrorMessage fails
	MessageID    string // From parsed SaxoErrorMessage.MessageID
	ErrorCode    string // From parsed SaxoErrorMessage.ErrorCode
	ErrorMessage string // From parsed SaxoErrorMessage.Message (top-level human-readable message)
	// DetailedSaxoErrors []SaxoErrorDetail // Optional: if we parse ErrorDetails into struct
}

// Error implements the error interface for OpenAPIError.
func (e *OpenAPIError) Error() string {
	var specificDetails string
	if e.ErrorCode != "" {
		specificDetails += fmt.Sprintf("ErrorCode: %s", e.ErrorCode)
	}
	if e.MessageID != "" {
		if specificDetails != "" { specificDetails += ", " }
		specificDetails += fmt.Sprintf("MessageID: %s", e.MessageID)
	}

	// Use the parsed ErrorMessage if available, otherwise fallback to RawContent for context
	finalMessageContent := e.ErrorMessage
	if finalMessageContent == "" {
		finalMessageContent = e.RawContent
		if len(finalMessageContent) > 100 { // Truncate raw content if too long
			finalMessageContent = finalMessageContent[:100] + "..."
		}
	}

	if specificDetails != "" {
		return fmt.Sprintf("Saxo OpenAPI Error (HTTP %d %s): %s - Details: %s",
			e.Code, e.Reason, finalMessageContent, specificDetails)
	}
	return fmt.Sprintf("Saxo OpenAPI Error (HTTP %d %s): %s", e.Code, e.Reason, finalMessageContent)
}

// StreamTerminated is an error indicating that a streaming connection was terminated.
var StreamTerminated = errors.New("saxo openapi: stream terminated")

// tryParseSaxoError attempts to parse the raw error content into the OpenAPIError fields.
// This function is called internally when an OpenAPIError is constructed.
func tryParseSaxoError(rawContent string, openAPIErr *OpenAPIError) {
	var saxoErr SaxoErrorMessage
	// It's possible the rawContent is not a JSON or not the expected SaxoErrorMessage structure.
	if err := json.Unmarshal([]byte(rawContent), &saxoErr); err == nil {
		openAPIErr.MessageID = saxoErr.MessageID
		openAPIErr.ErrorCode = saxoErr.ErrorCode
		openAPIErr.ErrorMessage = saxoErr.Message
		// Note: saxoErr.ErrorDetails is json.RawMessage.
		// Further parsing into []SaxoErrorDetail or string could be done here if needed
		// and stored in OpenAPIError if it had a dedicated field for structured details.
	}
	// If unmarshalling fails, ErrorMessage, ErrorCode, MessageID will remain empty
	// and the RawContent will be the primary source of info in the Error() string.
}

// NewOpenAPIError creates a new OpenAPIError, attempting to parse Saxo-specific details from content.
func NewOpenAPIError(code int, reason string, rawContent string) *OpenAPIError {
	err := &OpenAPIError{
		Code:       code,
		Reason:     reason,
		RawContent: rawContent,
	}
	tryParseSaxoError(rawContent, err) // Populate MessageID, ErrorCode, ErrorMessage
	return err
}
