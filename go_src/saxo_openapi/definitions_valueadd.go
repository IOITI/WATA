package saxo_openapi

import "time"

// --- Value Added Services (vas) ---

// --- Price Alerts ---

// PriceAlert represents a price alert definition.
type PriceAlert struct {
	AlertDefinitionID *string       `json:"AlertDefinitionId,omitempty"` // ID of the alert, present in responses
	AccountKey        string        `json:"AccountKey"`                  // Account this alert is tied to
	AssetType         string        `json:"AssetType"`                 // e.g., "Stock", "FxSpot"
	Comment           *string       `json:"Comment,omitempty"`           // User-defined comment
	CreatedDateTime   *time.Time    `json:"CreatedDateTime,omitempty"`   // Server-assigned creation time
	ExpiryDateTime    time.Time     `json:"ExpiryDateTime"`              // When the alert expires
	InstrumentName    *string       `json:"InstrumentName,omitempty"`    // Human-readable name (often in response)
	IsEnabled         bool          `json:"IsEnabled"`                   // Is the alert active?
	Operator          string        `json:"Operator"`                    // "GreaterThanOrEqual", "LessThanOrEqual"
	Price             float64       `json:"Price"`                       // Trigger price
	PriceTypeToWatch  string        `json:"PriceTypeToWatch"`            // "Ask", "Bid", "LastTraded"
	Status            *string       `json:"Status,omitempty"`            // e.g., "Active", "Triggered", "Expired" (often in response)
	TargetPrice       float64       `json:"TargetPrice"`                 // Same as Price, sometimes used in requests
	Uic               int           `json:"Uic"`                         // Instrument identifier
}

// CreatePriceAlertRequest is used as the body for creating a price alert.
// It's very similar to PriceAlert but might omit response-only fields.
type CreatePriceAlertRequest struct {
	AccountKey       string    `json:"AccountKey"`
	AssetType        string    `json:"AssetType"`
	Comment          *string   `json:"Comment,omitempty"`
	ExpiryDateTime   string    `json:"ExpiryDateTime"` // String format "YYYY-MM-DDTHH:MM:SSZ" or "YYYY-MM-DD"
	IsEnabled        *bool     `json:"IsEnabled,omitempty"` // Defaults to true if omitted by API
	Operator         string    `json:"Operator"`
	Price            float64   `json:"Price"`
	PriceTypeToWatch string    `json:"PriceTypeToWatch"`
	TargetPrice      *float64  `json:"TargetPrice,omitempty"` // If not provided, usually defaults to Price
	Uic              int       `json:"Uic"`
}

// CreatePriceAlertResponse is the response after creating a price alert.
type CreatePriceAlertResponse struct {
	AlertDefinitionID string `json:"AlertDefinitionId"`
	// May include other fields of the created alert.
}

// GetPriceAlertsResponse is for fetching multiple price alerts.
type GetPriceAlertsResponse struct {
	Data  []PriceAlert `json:"Data"`
	MaxRows *int       `json:"MaxRows,omitempty"` // If pagination is supported
	NextPage *string    `json:"NextPage,omitempty"` // If pagination is supported
}

// UpdatePriceAlertRequest is used as the body for updating a price alert.
// Similar to CreatePriceAlertRequest, but typically for PATCH where fields are optional.
type UpdatePriceAlertRequest struct {
	Comment          *string   `json:"Comment,omitempty"`
	ExpiryDateTime   *string   `json:"ExpiryDateTime,omitempty"` // String format
	IsEnabled        *bool     `json:"IsEnabled,omitempty"`
	Operator         *string   `json:"Operator,omitempty"`
	Price            *float64  `json:"Price,omitempty"`
	PriceTypeToWatch *string   `json:"PriceTypeToWatch,omitempty"`
	TargetPrice      *float64  `json:"TargetPrice,omitempty"`
}


// --- Positions (ValueAdd specific, if any) ---
// The /vas/positions endpoints in Python seem to be about positions-as-collateral.
// These might have specific fields not covered by portfolio.Position.
// For now, let's assume they are similar or define a new struct if differences are identified.

// Example: PositionAsCollateral (if different from portfolio.Position)
/*
type PositionAsCollateral struct {
    // ... fields ...
}
type GetPositionsAsCollateralResponse struct {
    Data []PositionAsCollateral `json:"Data"`
}
*/

// --- Users (ValueAdd specific, if any) ---
// The /vas/users endpoints in Python seem to be for user settings like enabling/disabling price alerts.

// PriceAlertsUserSettings represents settings for price alerts for a user.
type PriceAlertsUserSettings struct {
    PriceAlertsEnabled bool `json:"PriceAlertsEnabled"`
}
// Note: GET /vas/users/me/pricealertusersettings returns the struct above.
// PUT /vas/users/me/pricealertusersettings takes the struct above as request body.
// Response for PUT is often 204 No Content.

// Add other ValueAdd service related DTOs here as they are implemented.
// E.g., for /recommendations, /watchlist, etc.
