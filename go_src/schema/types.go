package schema

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
)

// --- WebhookPayload ---

// WebhookAction represents the allowed actions for a webhook.
type WebhookAction string

const (
	ActionLong  WebhookAction = "long"
	ActionShort WebhookAction = "short"
	ActionStop  WebhookAction = "stop"
	ActionTest  WebhookAction = "test" // From Python example
)

var validWebhookActions = map[WebhookAction]bool{
	ActionLong:  true,
	ActionShort: true,
	ActionStop:  true,
	ActionTest:  true,
}

// UnmarshalJSON implements the json.Unmarshaler interface for WebhookAction.
// This allows validating the action string during JSON unmarshalling.
func (wa *WebhookAction) UnmarshalJSON(data []byte) error {
	var s string
	if err := json.Unmarshal(data, &s); err != nil {
		return fmt.Errorf("WebhookAction should be a string, got %s: %w", data, err)
	}
	action := WebhookAction(strings.ToLower(s)) // Normalize to lowercase
	if !validWebhookActions[action] {
		return fmt.Errorf("invalid WebhookAction: '%s'. Valid actions are 'long', 'short', 'stop', 'test'", s)
	}
	*wa = action
	return nil
}

// WebhookPayload defines the structure for incoming webhook data.
type WebhookPayload struct {
	Action          WebhookAction `json:"action"`
	Indice          string        `json:"indice"` // e.g., "CAC40", "DAX40"
	SignalTimestamp time.Time     `json:"signal_timestamp"`
	AlertTimestamp  time.Time     `json:"alert_timestamp"`
}

// Validate checks the integrity of the WebhookPayload.
func (wp *WebhookPayload) Validate() error {
	if wp.Action == "" { // This implies it wasn't a valid enum value during UnmarshalJSON
		return fmt.Errorf("action is required and must be a valid WebhookAction (long, short, stop, test)")
	}
	if strings.TrimSpace(wp.Indice) == "" {
		return fmt.Errorf("indice is required")
	}
	if wp.SignalTimestamp.IsZero() {
		return fmt.Errorf("signal_timestamp is required and must be a valid timestamp")
	}
	if wp.AlertTimestamp.IsZero() {
		return fmt.Errorf("alert_timestamp is required and must be a valid timestamp")
	}
	// Additional validation: AlertTimestamp should not be before SignalTimestamp
	if wp.AlertTimestamp.Before(wp.SignalTimestamp) {
		return fmt.Errorf("alert_timestamp cannot be before signal_timestamp (alert: %v, signal: %v)", wp.AlertTimestamp, wp.SignalTimestamp)
	}
	return nil
}

// --- TradingActionPayload ---

// TradingAction represents the allowed actions for internal trading messages.
type TradingAction string

const (
	ActionPlaceOrder      TradingAction = "place_order"
	ActionModifyOrder     TradingAction = "modify_order"
	ActionCancelOrder     TradingAction = "cancel_order"
	ActionCheckPositions  TradingAction = "check_positions_on_saxo_api" // Matches scheduler
	ActionDailyStats      TradingAction = "daily_stats"                 // Matches scheduler
	ActionClosePosition   TradingAction = "close-position"              // Matches scheduler
	ActionProcessWebhook  TradingAction = "process_webhook"             // New action for webhook processing
	ActionRepeatLastTrade TradingAction = "try_repeat_last_action_at_the_open" // Matches scheduler
	// Add other actions as needed from your system design
)

var validTradingActions = map[TradingAction]bool{
	ActionPlaceOrder:      true,
	ActionModifyOrder:     true,
	ActionCancelOrder:     true,
	ActionCheckPositions:  true,
	ActionDailyStats:      true,
	ActionClosePosition:   true,
	ActionProcessWebhook:  true,
	ActionRepeatLastTrade: true,
}

// UnmarshalJSON implements the json.Unmarshaler interface for TradingAction.
func (ta *TradingAction) UnmarshalJSON(data []byte) error {
	var s string
	if err := json.Unmarshal(data, &s); err != nil {
		return fmt.Errorf("TradingAction should be a string, got %s: %w", data, err)
	}
	action := TradingAction(strings.ToLower(s)) // Normalize
	if !validTradingActions[action] {
		// Construct valid actions string for error message
		validKeys := make([]string, 0, len(validTradingActions))
		for k := range validTradingActions {
			validKeys = append(validKeys, string(k))
		}
		return fmt.Errorf("invalid TradingAction: '%s'. Valid actions are: %s", s, strings.Join(validKeys, ", "))
	}
	*ta = action
	return nil
}

// TradingActionPayload defines the structure for messages sent to the trading queue.
type TradingActionPayload struct {
	SignalID        uuid.UUID     `json:"signal_id"` // Unique ID for the signal/event chain
	Action          TradingAction `json:"action"`
	Indice          string        `json:"indice,omitempty"`         // Optional for some actions like DailyStats
	SignalTimestamp time.Time     `json:"signal_timestamp"`         // Original signal time from webhook
	AlertTimestamp  time.Time     `json:"alert_timestamp"`          // Time alert was processed from webhook
	MQSendTimestamp time.Time     `json:"mqsend_timestamp"`         // Time this payload was constructed and sent to MQ
	WebhookRaw      *WebhookPayload `json:"webhook_payload,omitempty"` // Optional: include original webhook payload for place_order
	// Add other fields specific to certain actions, e.g.:
	// OrderDetails map[string]interface{} `json:"order_details,omitempty"` // For place_order, modify_order
	// PositionID string `json:"position_id,omitempty"` // For close_position
}

// Validate checks the integrity of the TradingActionPayload.
func (tp *TradingActionPayload) Validate() error {
	if tp.SignalID == uuid.Nil {
		return fmt.Errorf("signal_id is required and cannot be a nil UUID")
	}
	if tp.Action == "" { // Implies invalid enum value
		return fmt.Errorf("action is required and must be a valid TradingAction")
	}
	// Indice might be optional for some actions (e.g., daily_stats)
	// if tp.Action == ActionPlaceOrder || tp.Action == ActionModifyOrder || tp.Action == ActionClosePosition {
	// 	if strings.TrimSpace(tp.Indice) == "" {
	// 		return fmt.Errorf("indice is required for action '%s'", tp.Action)
	// 	}
	// }
	// Let's make Indice generally required if action implies an instrument, but allow exceptions if needed.
	// For now, a simple check for actions that clearly need it.
	needsIndice := map[TradingAction]bool{
		ActionPlaceOrder: true, ActionModifyOrder: true, ActionCancelOrder: true, ActionClosePosition: true, ActionProcessWebhook: true, ActionRepeatLastTrade: true,
	}
	if needsIndice[tp.Action] && strings.TrimSpace(tp.Indice) == "" {
		return fmt.Errorf("indice is required for action '%s'", tp.Action)
	}


	if tp.SignalTimestamp.IsZero() {
		return fmt.Errorf("signal_timestamp is required")
	}
	if tp.AlertTimestamp.IsZero() {
		return fmt.Errorf("alert_timestamp is required")
	}
	if tp.MQSendTimestamp.IsZero() {
		return fmt.Errorf("mqsend_timestamp is required")
	}

	// Timestamps logical order
	if tp.AlertTimestamp.Before(tp.SignalTimestamp) {
		return fmt.Errorf("alert_timestamp cannot be before signal_timestamp (alert: %v, signal: %v)", tp.AlertTimestamp, tp.SignalTimestamp)
	}
	if tp.MQSendTimestamp.Before(tp.AlertTimestamp) {
		// Allow them to be very close, but MQSend should generally be after or equal to AlertTimestamp
		if tp.MQSendTimestamp.UnixNano() < tp.AlertTimestamp.UnixNano() { // More precise comparison
			return fmt.Errorf("mqsend_timestamp cannot be before alert_timestamp (mqsend: %v, alert: %v)", tp.MQSendTimestamp, tp.AlertTimestamp)
		}
	}

	if tp.Action == ActionProcessWebhook && tp.WebhookRaw == nil {
		return fmt.Errorf("webhook_payload is required for action '%s'", tp.Action)
	}
	if tp.WebhookRaw != nil {
		if err := tp.WebhookRaw.Validate(); err != nil {
			return fmt.Errorf("webhook_payload validation failed: %w", err)
		}
	}

	return nil
}
