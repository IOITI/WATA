package saxo_openapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http" // For http.StatusNoContent
	// "net/url" // Removed unused
	"reflect"
	// "strings"
	// "time"
)

// --- Value Added Services (vas) ---

// --- Price Alerts ---

// GetPriceAlertsParams defines query parameters for fetching price alerts.
type GetPriceAlertsParams struct {
	AccountKey string  `url:"AccountKey"`          // Required
	AssetType  *string `url:"AssetType,omitempty"` // Optional
	Status     *string `url:"Status,omitempty"`    // Optional, e.g. "Active,Triggered"
	Uic        *int    `url:"Uic,omitempty"`       // Optional
	// Saxo docs also list $top, $skip, $inlinecount for some VAS endpoints
	Top *int `url:"$top,omitempty"`
}

// GetPriceAlerts retrieves a list of price alerts.
// GET /openapi/vas/v1/pricealerts
func (c *Client) GetPriceAlerts(ctx context.Context, params *GetPriceAlertsParams) (*GetPriceAlertsResponse, error) {
	if params == nil || params.AccountKey == "" {
		return nil, fmt.Errorf("AccountKey is required in params for GetPriceAlerts")
	}
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}

	responseBodyType := reflect.TypeOf(GetPriceAlertsResponse{})
	result, _, err := c.doRequest(ctx, "GET", "vas/v1/pricealerts", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetPriceAlertsResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetPriceAlertsResponse. Got: %T", result)
}

// GetPriceAlert retrieves a single price alert by its definition ID.
// GET /openapi/vas/v1/pricealerts/{AlertDefinitionId}
func (c *Client) GetPriceAlert(ctx context.Context, alertDefinitionID string) (*PriceAlert, error) {
	if alertDefinitionID == "" {
		return nil, fmt.Errorf("alertDefinitionID is required")
	}
	path := fmt.Sprintf("vas/v1/pricealerts/%s", alertDefinitionID)

	responseBodyType := reflect.TypeOf(PriceAlert{})
	result, _, err := c.doRequest(ctx, "GET", path, nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*PriceAlert); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert PriceAlert. Got: %T", result)
}

// CreatePriceAlert creates a new price alert.
// POST /openapi/vas/v1/pricealerts
func (c *Client) CreatePriceAlert(ctx context.Context, alertRequest CreatePriceAlertRequest) (*CreatePriceAlertResponse, error) {
	// Basic validation, more can be added (e.g. Operator values)
	if alertRequest.AccountKey == "" || alertRequest.AssetType == "" || alertRequest.Uic == 0 || alertRequest.Operator == "" || alertRequest.PriceTypeToWatch == "" {
		return nil, fmt.Errorf("AccountKey, AssetType, Uic, Operator, Price, ExpiryDateTime, PriceTypeToWatch are required")
	}
	// ExpiryDateTime format check could be added here.

	bodyBytes, err := json.Marshal(alertRequest)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal create price alert request: %w", err)
	}

	responseBodyType := reflect.TypeOf(CreatePriceAlertResponse{})
	result, httpResp, err := c.doRequest(ctx, "POST", "vas/v1/pricealerts", nil, bytes.NewBuffer(bodyBytes), responseBodyType)
	if err != nil {
		return nil, err
	}
	// Expect 201 Created
	if httpResp.StatusCode != http.StatusCreated {
		return nil, fmt.Errorf("unexpected status code %d when creating price alert. Expected 201", httpResp.StatusCode)
	}

	if typedResult, ok := result.(*CreatePriceAlertResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert CreatePriceAlertResponse. Got: %T", result)
}

// UpdatePriceAlert updates an existing price alert.
// PATCH /openapi/vas/v1/pricealerts/{AlertDefinitionId}
func (c *Client) UpdatePriceAlert(ctx context.Context, alertDefinitionID string, alertUpdates UpdatePriceAlertRequest) error {
	if alertDefinitionID == "" {
		return fmt.Errorf("alertDefinitionID is required")
	}
	path := fmt.Sprintf("vas/v1/pricealerts/%s", alertDefinitionID)

	bodyBytes, err := json.Marshal(alertUpdates)
	if err != nil {
		return fmt.Errorf("failed to marshal update price alert request: %w", err)
	}

	// Expect 204 No Content
	_, httpResp, err := c.doRequest(ctx, "PATCH", path, nil, bytes.NewBuffer(bodyBytes), nil)
	if err != nil {
		return err
	}
	if httpResp.StatusCode != http.StatusNoContent {
		return fmt.Errorf("unexpected status code %d when updating price alert. Expected 204", httpResp.StatusCode)
	}
	return nil
}

// DeletePriceAlert deletes a price alert.
// DELETE /openapi/vas/v1/pricealerts/{AlertDefinitionId}
func (c *Client) DeletePriceAlert(ctx context.Context, alertDefinitionID string) error {
	if alertDefinitionID == "" {
		return fmt.Errorf("alertDefinitionID is required")
	}
	path := fmt.Sprintf("vas/v1/pricealerts/%s", alertDefinitionID)

	// Expect 204 No Content
	_, httpResp, err := c.doRequest(ctx, "DELETE", path, nil, nil, nil)
	if err != nil {
		return err
	}
	if httpResp.StatusCode != http.StatusNoContent {
		return fmt.Errorf("unexpected status code %d when deleting price alert. Expected 204", httpResp.StatusCode)
	}
	return nil
}

// DeleteMultiplePriceAlerts deletes multiple price alerts.
// DELETE /openapi/vas/v1/pricealerts?AccountKey={AccountKey}&AlertDefinitionIds={AlertDefinitionIds}
type DeleteMultiplePriceAlertsParams struct {
	AccountKey         string `url:"AccountKey"`                   // Required
	AlertDefinitionIDs string `url:"AlertDefinitionIds"`           // Required, comma-separated
	ClientKey          *string `url:"ClientKey,omitempty"`         // Optional, if different from token's client
}
func (c *Client) DeleteMultiplePriceAlerts(ctx context.Context, params *DeleteMultiplePriceAlertsParams) error {
	if params == nil || params.AccountKey == "" || params.AlertDefinitionIDs == "" {
		return fmt.Errorf("AccountKey and AlertDefinitionIDs are required")
	}
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return fmt.Errorf("failed to convert params: %w", err)
	}

	// Expect 204 No Content
	_, httpResp, err := c.doRequest(ctx, "DELETE", "vas/v1/pricealerts", queryParams, nil, nil)
	if err != nil {
		return err
	}
	if httpResp.StatusCode != http.StatusNoContent {
		return fmt.Errorf("unexpected status code %d when deleting multiple price alerts. Expected 204", httpResp.StatusCode)
	}
	return nil
}


// --- User Settings (VAS) ---

// GetPriceAlertUserSettings retrieves price alert settings for the current user.
// GET /openapi/vas/v1/users/me/pricealertusersettings
func (c *Client) GetPriceAlertUserSettings(ctx context.Context) (*PriceAlertsUserSettings, error) {
	responseBodyType := reflect.TypeOf(PriceAlertsUserSettings{})
	result, _, err := c.doRequest(ctx, "GET", "vas/v1/users/me/pricealertusersettings", nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*PriceAlertsUserSettings); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert PriceAlertsUserSettings. Got: %T", result)
}

// UpdatePriceAlertUserSettings updates price alert settings for the current user.
// PUT /openapi/vas/v1/users/me/pricealertusersettings
func (c *Client) UpdatePriceAlertUserSettings(ctx context.Context, settings PriceAlertsUserSettings) error {
	bodyBytes, err := json.Marshal(settings)
	if err != nil {
		return fmt.Errorf("failed to marshal price alert user settings: %w", err)
	}

	// Expect 204 No Content
	_, httpResp, err := c.doRequest(ctx, "PUT", "vas/v1/users/me/pricealertusersettings", nil, bytes.NewBuffer(bodyBytes), nil)
	if err != nil {
		return err
	}
	if httpResp.StatusCode != http.StatusNoContent {
		return fmt.Errorf("unexpected status code %d when updating price alert user settings. Expected 204", httpResp.StatusCode)
	}
	return nil
}

// Other VAS endpoints like /recommendations, /watchlist would follow similar patterns.
// For example, /watchlist:
// - GET /openapi/vas/v1/watchlists
// - POST /openapi/vas/v1/watchlists
// - GET /openapi/vas/v1/watchlists/{WatchlistId}
// - PUT /openapi/vas/v1/watchlists/{WatchlistId} (replace)
// - PATCH /openapi/vas/v1/watchlists/{WatchlistId} (update name/sharing)
// - DELETE /openapi/vas/v1/watchlists/{WatchlistId}
// - GET /openapi/vas/v1/watchlists/{WatchlistId}/instruments
// - POST /openapi/vas/v1/watchlists/{WatchlistId}/instruments
// - DELETE /openapi/vas/v1/watchlists/{WatchlistId}/instruments?Uics={Uics}&AssetTypes={AssetTypes}
// These require defining Watchlist, WatchlistInstrument structs etc.
// For brevity, these are not fully implemented here but would follow the established pattern.
