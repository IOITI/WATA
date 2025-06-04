package message_helper

import (
	"encoding/json"
	"fmt"
	"pymath/go_src/trade_exceptions" // Assuming this path is correct
	"strings"
	"time"
)

const (
	defaultTimezone = "Europe/Paris" // Default timezone if not provided or invalid
	timestampFormat = "2006-01-02 15:04:05 MST" // For output
	inputTimestampLayout = "2006-01-02T15:04:05.000Z07:00" // More robust ISO 8601 parsing
	inputTimestampLayoutFallback = "2006-01-02T15:04:05Z07:00" // Fallback without milliseconds
)

// TelegramMessageComposer helps build formatted messages for Telegram.
type TelegramMessageComposer struct {
	signalData         map[string]interface{}
	sections           []string
	signalTimestampRaw string
	signalTimestamp    *time.Time
	askTimestamp       *time.Time
	execTimestamp      *time.Time
	timezone           *time.Location
}

// NewTelegramMessageComposer creates a new message composer.
// signalData is the initial data map from the signal.
// signalTimestampStr is the timestamp from the signal.
// timezoneStr is the IANA timezone string (e.g., "Europe/Paris").
func NewTelegramMessageComposer(signalData map[string]interface{}, signalTimestampStr string, timezoneStr string) (*TelegramMessageComposer, error) {
	loc, err := time.LoadLocation(timezoneStr)
	if err != nil {
		// Fallback to default or handle error more gracefully
		fmt.Printf("Warning: Failed to load timezone '%s', falling back to %s. Error: %v\n", timezoneStr, defaultTimezone, err)
		loc, _ = time.LoadLocation(defaultTimezone) // Should not fail for a known default
	}

	composer := &TelegramMessageComposer{
		signalData:         signalData,
		sections:           []string{},
		signalTimestampRaw: signalTimestampStr,
		timezone:           loc,
	}

	composer.signalTimestamp = composer.parseTimestamp(signalTimestampStr)
	composer.addSignalSection()

	return composer, nil
}

// parseTimestamp parses a timestamp string into a time.Time object in UTC.
// It tries multiple layouts common in ISO 8601.
func (c *TelegramMessageComposer) parseTimestamp(timestampStr string) *time.Time {
	if timestampStr == "" {
		return nil
	}
	// Try with milliseconds and timezone offset
	ts, err := time.Parse(inputTimestampLayout, timestampStr)
	if err == nil {
		utcTs := ts.UTC()
		return &utcTs
	}
	// Try without milliseconds but with timezone offset
	ts, err = time.Parse(inputTimestampLayoutFallback, timestampStr)
	if err == nil {
		utcTs := ts.UTC()
		return &utcTs
	}
    // Try basic ISO 8601 if others fail (might be less precise with timezone)
    // For example, if it's just "2023-10-26T10:00:00Z"
    layouts := []string{
        time.RFC3339,
        time.RFC3339Nano,
        "2006-01-02T15:04:05Z", // Common ISO 8601 UTC
    }
    for _, layout := range layouts {
        ts, err = time.Parse(layout, timestampStr)
        if err == nil {
            utcTs := ts.UTC()
            return &utcTs
        }
    }

	fmt.Printf("Warning: Could not parse timestamp string '%s' with known layouts\n", timestampStr)
	return nil
}

// formatTimestamp formats a time.Time object to "YYYY-MM-DD HH:MM:SS ZZZ" in the composer's timezone.
func (c *TelegramMessageComposer) formatTimestamp(timestamp *time.Time) string {
	if timestamp == nil {
		return "N/A"
	}
	return timestamp.In(c.timezone).Format(timestampFormat)
}

// calculateTimeDiff calculates the difference between two timestamps and returns a formatted string.
func (c *TelegramMessageComposer) calculateTimeDiff(start, end *time.Time) string {
	if start == nil || end == nil {
		return "N/A"
	}
	if end.Before(*start) {
		return "end time before start time" // Or handle as an error/invalid state
	}
	diff := end.Sub(*start)
	return fmt.Sprintf("%.3f seconds", diff.Seconds())
}

// addSignalSection formats and adds the initial signal data.
func (c *TelegramMessageComposer) addSignalSection() {
	// Create a deep copy of signalData to avoid modifying the original map
	dataForDisplay := make(map[string]interface{})
	for k, v := range c.signalData {
		dataForDisplay[k] = v
	}

	// Remove sensitive or overly verbose fields if necessary for the display
	// delete(dataForDisplay, "some_internal_id")

	// Convert map to pretty JSON string for display
	jsonData, err := json.MarshalIndent(dataForDisplay, "", "  ")
	var content string
	if err != nil {
		content = fmt.Sprintf("Error marshalling signal data: %v", err)
	} else {
		content = string(jsonData)
	}

	c.sections = append(c.sections, fmt.Sprintf("--- SIGNAL (%s) ---\nRaw: %s\nParsed Signal Time: %s\n%s",
		c.signalTimestampRaw,
		c.formatTimestamp(c.signalTimestamp), // Format the parsed signal timestamp
		c.formatTimestamp(c.signalTimestamp), // This line seems redundant with the one above. Original python code has self.signal_timestamp_raw, then self.format_timestamp(self.signal_timestamp)
		content))
}


// AddTurboSearchResult formats and adds information about the turbo search result.
func (c *TelegramMessageComposer) AddTurboSearchResult(foundedTurbo map[string]interface{}, err error, searchContext map[string]interface{}) {
	var builder strings.Builder
	builder.WriteString("\n--- TURBO SEARCH ---\n")
	builder.WriteString(fmt.Sprintf("Context: %v\n", searchContext))

	if err != nil {
		builder.WriteString(fmt.Sprintf("Error: %s\n", err.Error()))
		// Type assert to specific error types for more details
		switch e := err.(type) {
		case *trade_exceptions.NoTurbosAvailableException:
			builder.WriteString(fmt.Sprintf("Type: NoTurbosAvailableException\n"))
			// Access specific fields if needed: e.g. e.SearchQuery
		case *trade_exceptions.ApiRequestException:
			builder.WriteString(fmt.Sprintf("Type: ApiRequestException\nEndpoint: %s\nStatus Code: %d\n", e.Endpoint, e.StatusCode))
		case *trade_exceptions.SaxoApiError:
			builder.WriteString(fmt.Sprintf("Type: SaxoApiError\nStatus Code: %d\n", e.StatusCode))
			for _, detail := range e.SaxoErrorDetails {
				builder.WriteString(fmt.Sprintf("  Saxo Detail: [%s] %s\n", detail.ErrorCode, detail.Message))
			}
		default:
			builder.WriteString(fmt.Sprintf("Type: Generic/Unknown\n"))
		}
	} else if foundedTurbo == nil || len(foundedTurbo) == 0 {
		builder.WriteString("No turbo found (nil or empty map returned).\n")
	} else {
		// Pretty print foundedTurbo map
		turboData, jsonErr := json.MarshalIndent(foundedTurbo, "", "  ")
		if jsonErr != nil {
			builder.WriteString(fmt.Sprintf("Selected Turbo: Error marshalling data: %v\n", jsonErr))
		} else {
			builder.WriteString(fmt.Sprintf("Selected Turbo:\n%s\n", string(turboData)))
		}
	}
	c.sections = append(c.sections, builder.String())
}

// AddPositionResult formats and adds information about the position opening attempt.
func (c *TelegramMessageComposer) AddPositionResult(buyDetails map[string]interface{}, err error, orderID interface{}, availableFunds, requiredPrice float64) {
	var builder strings.Builder
	builder.WriteString("\n--- POSITION RESULT ---\n")

	if orderIDStr, ok := orderID.(string); ok && orderIDStr != "" {
		builder.WriteString(fmt.Sprintf("Order ID: %s\n", orderIDStr))
	} else if orderIDInt, ok := orderID.(int); ok && orderIDInt != 0 { // Example if orderID could be int
		builder.WriteString(fmt.Sprintf("Order ID: %d\n", orderIDInt))
	}


	if err != nil {
		builder.WriteString(fmt.Sprintf("Error: %s\n", err.Error()))
		// Type assert for specific error types
		switch e := err.(type) {
		case *trade_exceptions.InsufficientFundsException:
			builder.WriteString(fmt.Sprintf("Type: InsufficientFundsException\nAvailable: %.2f, Required: %.2f\n", e.AvailableFunds, e.RequiredFunds))
		case *trade_exceptions.OrderPlacementError:
			builder.WriteString(fmt.Sprintf("Type: OrderPlacementError\nReason: %s\nDetails: %v\n", e.Reason, e.OrderDetails))
		case *trade_exceptions.SaxoApiError:
			builder.WriteString(fmt.Sprintf("Type: SaxoApiError\nStatus Code: %d\n", e.StatusCode))
			for _, detail := range e.SaxoErrorDetails {
				builder.WriteString(fmt.Sprintf("  Saxo Detail: [%s] %s\n", detail.ErrorCode, detail.Message))
			}
			if e.OrderDetails != nil {
				builder.WriteString(fmt.Sprintf("  Order Attempt Details: %v\n", e.OrderDetails))
			}
		case *trade_exceptions.ApiRequestException:
			builder.WriteString(fmt.Sprintf("Type: ApiRequestException\nEndpoint: %s\nStatus Code: %d\n", e.Endpoint, e.StatusCode))
		default:
			builder.WriteString(fmt.Sprintf("Type: Generic/Unknown\n"))
		}
	} else if buyDetails == nil || len(buyDetails) == 0 {
        builder.WriteString("Position opened successfully, but no buy details returned or details map is empty.\n")
    } else {
		builder.WriteString("Position Opened Successfully:\n")
		// Pretty print buyDetails map
		detailsData, jsonErr := json.MarshalIndent(buyDetails, "", "  ")
		if jsonErr != nil {
			builder.WriteString(fmt.Sprintf("Buy Details: Error marshalling data: %v\n", jsonErr))
		} else {
			builder.WriteString(fmt.Sprintf("%s\n", string(detailsData)))
		}
	}

	// Timestamps and diffs
	c.askTimestamp = c.parseTimestamp(safeGetNestedString(buyDetails, "timestamps", "ask"))
	c.execTimestamp = c.parseTimestamp(safeGetNestedString(buyDetails, "timestamps", "execution"))

	builder.WriteString(fmt.Sprintf("Signal Time: %s\n", c.formatTimestamp(c.signalTimestamp)))
	builder.WriteString(fmt.Sprintf("Ask Time   : %s\n", c.formatTimestamp(c.askTimestamp)))
	builder.WriteString(fmt.Sprintf("Exec Time  : %s\n", c.formatTimestamp(c.execTimestamp)))
	builder.WriteString(fmt.Sprintf("Signal->Ask Diff : %s\n", c.calculateTimeDiff(c.signalTimestamp, c.askTimestamp)))
	builder.WriteString(fmt.Sprintf("Ask->Exec Diff   : %s\n", c.calculateTimeDiff(c.askTimestamp, c.execTimestamp)))
	builder.WriteString(fmt.Sprintf("Signal->Exec Diff: %s\n", c.calculateTimeDiff(c.signalTimestamp, c.execTimestamp)))

	c.sections = append(c.sections, builder.String())
}

// AddGenericError adds a generic error section.
func (c *TelegramMessageComposer) AddGenericError(context string, err error, isCritical bool) {
	criticality := "INFO"
	if isCritical {
		criticality = "CRITICAL"
	}
	c.sections = append(c.sections, fmt.Sprintf("\n--- ERROR (%s) [%s] ---\n%s\n", context, criticality, err.Error()))
}

// AddRuleViolation adds a section for trading rule violations.
func (c *TelegramMessageComposer) AddRuleViolation(err error) {
	if e, ok := err.(*trade_exceptions.TradingRuleViolation); ok {
		c.sections = append(c.sections, fmt.Sprintf("\n--- TRADING RULE VIOLATION ---\nRule ID: %s\nMessage: %s\n", e.RuleID, e.Message))
	} else {
		c.AddGenericError("TradingRuleViolation (unexpected type)", err, false)
	}
}

// AddTextSection adds a custom text section.
func (c *TelegramMessageComposer) AddTextSection(title string, text string) {
	c.sections = append(c.sections, fmt.Sprintf("\n--- %s ---\n%s\n", strings.ToUpper(title), text))
}

// AddDictSection adds a section by formatting a map[string]interface{}.
func (c *TelegramMessageComposer) AddDictSection(title string, data map[string]interface{}) {
	jsonData, err := json.MarshalIndent(data, "", "  ")
	var content string
	if err != nil {
		content = fmt.Sprintf("Error marshalling data for section %s: %v", title, err)
	} else {
		content = string(jsonData)
	}
	c.sections = append(c.sections, fmt.Sprintf("\n--- %s ---\n%s\n", strings.ToUpper(title), content))
}

// GetMessage joins all sections to produce the final message string.
func (c *TelegramMessageComposer) GetMessage() string {
	return strings.Join(c.sections, "")
}


// safeGetNestedString safely extracts a nested string from a map.
// Used for extracting timestamps from buyDetails.
func safeGetNestedString(data map[string]interface{}, keys ...string) string {
	current := data
	for i, key := range keys {
		if val, ok := current[key]; ok {
			if i == len(keys)-1 { // Last key
				if strVal, strOk := val.(string); strOk {
					return strVal
				}
				return "" // Not a string
			}
			if nextMap, mapOk := val.(map[string]interface{}); mapOk {
				current = nextMap
			} else {
				return "" // Not a map at this level
			}
		} else {
			return "" // Key not found
		}
	}
	return ""
}

// TODO: Implement AddPnlUpdate, AddClosePositionResult, AddStopLossUpdate methods
// These will require similar logic: formatting strings, handling errors, and potentially using error types.
// For example, AddClosePositionResult might use PositionCloseException or SaxoApiError.
// AddStopLossUpdate might use ApiRequestException or SaxoApiError.
