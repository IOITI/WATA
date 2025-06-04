package saxo_openapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http" // Keep for http.StatusAccepted etc.
	"net/url"
	"reflect"
	// "strconv" // No longer needed here
	// "strings" // No longer needed here
)

// --- Accounts ---

// GetAccount retrieves details for a single account.
// GET /openapi/port/v1/accounts/{AccountKey}
func (c *Client) GetAccount(ctx context.Context, accountKey string) (*Account, error) {
	if accountKey == "" {
		return nil, fmt.Errorf("accountKey is required")
	}
	path := fmt.Sprintf("port/v1/accounts/%s", accountKey)

	responseBodyType := reflect.TypeOf(Account{})
	result, _, err := c.doRequest(ctx, "GET", path, nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*Account); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert Account from API response. Got: %T", result)
}

// GetAccountsParams defines query parameters for fetching accounts.
type GetAccountsParams struct {
	ClientKey         string  `url:"ClientKey,omitempty"`
	AccountGroupKey   *string `url:"AccountGroupKey,omitempty"`
	AccountKey        *string `url:"AccountKey,omitempty"`
	Sharing           *string `url:"Sharing,omitempty"`
	SupportsOptionTrading *bool `url:"SupportsOptionTrading,omitempty"`
}

// GetAccounts retrieves a list of accounts based on specified criteria.
// GET /openapi/port/v1/accounts
func (c *Client) GetAccounts(ctx context.Context, params *GetAccountsParams) (*GetAccountsResponse, error) {
	queryParams, err := paramsToQueryValues(params) // This function is now in client.go
	if err != nil {
		return nil, fmt.Errorf("failed to convert params to query values: %w", err)
	}

	responseBodyType := reflect.TypeOf(GetAccountsResponse{})
	result, _, err := c.doRequest(ctx, "GET", "port/v1/accounts", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetAccountsResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetAccountsResponse from API response. Got: %T", result)
}

// UpdateAccount updates specific properties of an account.
// PATCH /openapi/port/v1/accounts/{AccountKey}
func (c *Client) UpdateAccount(ctx context.Context, accountKey string, updates map[string]interface{}) (*Account, error) {
	if accountKey == "" {
		return nil, fmt.Errorf("accountKey is required")
	}
	path := fmt.Sprintf("port/v1/accounts/%s", accountKey)

	bodyBytes, err := json.Marshal(updates)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal account updates: %w", err)
	}

	responseBodyType := reflect.TypeOf(Account{})
	result, _, err := c.doRequest(ctx, "PATCH", path, nil, bytes.NewBuffer(bodyBytes), responseBodyType)
	if err != nil {
		return nil, err
	}
    if result == nil {
        return c.GetAccount(ctx, accountKey)
    }
	if typedResult, ok := result.(*Account); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert Account from API response after update. Got: %T", result)
}


// --- Balances ---

// GetAccountBalance retrieves the balance for a specific account.
// GET /openapi/port/v1/balances/{AccountKey}
func (c *Client) GetAccountBalance(ctx context.Context, accountKey string) (*Balance, error) {
	if accountKey == "" {
		return nil, fmt.Errorf("accountKey is required")
	}
	path := fmt.Sprintf("port/v1/balances/%s", accountKey)

	responseBodyType := reflect.TypeOf(Balance{})
	result, _, err := c.doRequest(ctx, "GET", path, nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*Balance); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert Balance from API response. Got: %T", result)
}

// GetCashBalances retrieves cash balances for accounts matching criteria.
// GET /openapi/port/v1/balances/cash
type GetCashBalancesParams struct {
	AccountGroupKey *string `url:"AccountGroupKey,omitempty"`
	AccountKey      *string `url:"AccountKey,omitempty"`
	ClientKey       string  `url:"ClientKey"`
	FieldGroups     *string `url:"FieldGroups,omitempty"`
}
func (c *Client) GetCashBalances(ctx context.Context, params *GetCashBalancesParams) (*Balance, error) {
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(Balance{})
	result, _, err := c.doRequest(ctx, "GET", "port/v1/balances/cash", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*Balance); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert Balance from API response. Got: %T", result)
}

// UpdateClientDetails updates client details.
// PATCH /openapi/port/v1/clients/me
func (c *Client) UpdateClientDetails(ctx context.Context, updates map[string]interface{}) error {
	bodyBytes, err := json.Marshal(updates)
	if err != nil {
		return fmt.Errorf("failed to marshal client updates: %w", err)
	}
	_, _, err = c.doRequest(ctx, "PATCH", "port/v1/clients/me", nil, bytes.NewBuffer(bodyBytes), nil)
	return err
}

// --- Positions ---
type GetPositionParams struct {
	AccountGroupKey *string `url:"AccountGroupKey,omitempty"`
	AccountKey      *string `url:"AccountKey,omitempty"`
	ClientKey       *string `url:"ClientKey,omitempty"`
	FieldGroups     *string `url:"FieldGroups,omitempty"`
	WatchlistName   *string `url:"WatchlistName,omitempty"`
}

func (c *Client) GetPositions(ctx context.Context, params *GetPositionParams) (*GetPositionsResponse, error) {
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetPositionsResponse{})
	result, _, err := c.doRequest(ctx, "GET", "port/v1/positions", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetPositionsResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetPositionsResponse from API response. Got: %T", result)
}

func (c *Client) GetPosition(ctx context.Context, positionID string, params *GetPositionParams) (*Position, error) {
	if positionID == "" {
		return nil, fmt.Errorf("positionID is required")
	}
	path := fmt.Sprintf("port/v1/positions/%s", positionID)
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}

	responseBodyType := reflect.TypeOf(Position{})
	result, _, err := c.doRequest(ctx, "GET", path, queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*Position); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert Position from API response. Got: %T", result)
}

// --- Net Positions ---
func (c *Client) GetNetPositions(ctx context.Context, params *GetPositionParams) (*GetNetPositionsResponse, error) {
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetNetPositionsResponse{})
	result, _, err := c.doRequest(ctx, "GET", "port/v1/netpositions", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetNetPositionsResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetNetPositionsResponse from API response. Got: %T", result)
}

func (c *Client) GetNetPosition(ctx context.Context, netPositionID string, params *GetPositionParams) (*NetPosition, error) {
	if netPositionID == "" {
		return nil, fmt.Errorf("netPositionID is required")
	}
	path := fmt.Sprintf("port/v1/netpositions/%s", netPositionID)
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}

	responseBodyType := reflect.TypeOf(NetPosition{})
	result, _, err := c.doRequest(ctx, "GET", path, queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*NetPosition); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert NetPosition from API response. Got: %T", result)
}

// --- Closed Positions ---
type GetClosedPositionParams struct {
	AccountKey      *string `url:"AccountKey,omitempty"`
	ClientKey       string  `url:"ClientKey"`
	FromDate        *string `url:"FromDate,omitempty"`
	ToDate          *string `url:"ToDate,omitempty"`
	Skip            *int    `url:"$skip,omitempty"`
	Top             *int    `url:"$top,omitempty"`
	FieldGroups     *string `url:"FieldGroups,omitempty"`
}

func (c *Client) GetClosedPositions(ctx context.Context, params *GetClosedPositionParams) (*GetClosedPositionsResponse, error) {
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetClosedPositionsResponse{})
	result, _, err := c.doRequest(ctx, "GET", "port/v1/closedpositions", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetClosedPositionsResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetClosedPositionsResponse from API response. Got: %T", result)
}

func (c *Client) GetClosedPosition(ctx context.Context, closedPositionID string, clientKey string) (*ClosedPosition, error) {
	if closedPositionID == "" {
		return nil, fmt.Errorf("closedPositionID is required")
	}
	if clientKey == "" {
		return nil, fmt.Errorf("clientKey is required")
	}
	path := fmt.Sprintf("port/v1/closedpositions/%s", closedPositionID)
	queryParams := url.Values{}
	queryParams.Set("ClientKey", clientKey)

	responseBodyType := reflect.TypeOf(ClosedPosition{})
	result, _, err := c.doRequest(ctx, "GET", path, queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*ClosedPosition); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert ClosedPosition from API response. Got: %T", result)
}

// --- AccountGroups ---
func (c *Client) GetAccountGroups(ctx context.Context, ownerKey string) (*GetAccountGroupsResponse, error) {
	if ownerKey == "" {
		return nil, fmt.Errorf("ownerKey is required")
	}
	params := url.Values{}
	params.Set("OwnerKey", ownerKey)

	responseBodyType := reflect.TypeOf(GetAccountGroupsResponse{})
	result, _, err := c.doRequest(ctx, "GET", "port/v1/accountgroups", params, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetAccountGroupsResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetAccountGroupsResponse from API response. Got: %T", result)
}

// --- Exposure ---
func (c *Client) GetInstrumentExposure(ctx context.Context, clientKey string) (*InstrumentExposure, error) {
	if clientKey == "" {
		return nil, fmt.Errorf("clientKey is required")
	}
	params := url.Values{}
	params.Set("ClientKey", clientKey)

	responseBodyType := reflect.TypeOf(InstrumentExposure{})
	result, _, err := c.doRequest(ctx, "GET", "port/v1/exposure/instrument", params, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*InstrumentExposure); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert InstrumentExposure from API response. Got: %T", result)
}

// --- Portfolio Subscriptions ---
func (c *Client) CreatePortfolioSubscription(ctx context.Context, subscriptionType string, args CreateSubscriptionArgs) (*CreateSubscriptionResponse, error) {
	if subscriptionType != "accounts" && subscriptionType != "positions" && subscriptionType != "balances" {
		return nil, fmt.Errorf("invalid subscriptionType: %s. Must be 'accounts', 'positions', or 'balances'", subscriptionType)
	}
	path := fmt.Sprintf("port/v1/%s/subscriptions", subscriptionType)

	bodyBytes, err := json.Marshal(args)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal subscription args: %w", err)
	}

	responseBodyType := reflect.TypeOf(CreateSubscriptionResponse{})
	result, _, err := c.doRequest(ctx, "POST", path, nil, bytes.NewBuffer(bodyBytes), responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*CreateSubscriptionResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert CreateSubscriptionResponse. Got: %T", result)
}

func (c *Client) RemovePortfolioSubscriptionsByTag(ctx context.Context, subscriptionType string, contextID string, tag string) error {
	if subscriptionType != "accounts" && subscriptionType != "positions" && subscriptionType != "balances" {
		return fmt.Errorf("invalid subscriptionType: %s", subscriptionType)
	}
	if contextID == "" || tag == "" {
		return fmt.Errorf("contextID and tag are required")
	}
	path := fmt.Sprintf("port/v1/%s/subscriptions/%s/%s", subscriptionType, contextID, tag)

	_, httpResp, err := c.doRequest(ctx, "DELETE", path, nil, nil, nil)
	if err != nil {
		return err
	}
	if httpResp.StatusCode != http.StatusAccepted && httpResp.StatusCode != http.StatusNoContent {
		return fmt.Errorf("unexpected status code %d when removing subscription by tag", httpResp.StatusCode)
	}
	return nil
}

func (c *Client) RemovePortfolioSubscriptionByID(ctx context.Context, subscriptionType string, contextID string, referenceID string) error {
	if subscriptionType != "accounts" && subscriptionType != "positions" && subscriptionType != "balances" {
		return fmt.Errorf("invalid subscriptionType: %s", subscriptionType)
	}
	if contextID == "" || referenceID == "" {
		return fmt.Errorf("contextID and referenceID are required")
	}
	path := fmt.Sprintf("port/v1/%s/subscriptions/%s", subscriptionType, contextID)
	params := url.Values{}
	params.Set("Tag", referenceID)

	_, httpResp, err := c.doRequest(ctx, "DELETE", path, params, nil, nil)
	if err != nil {
		return err
	}
	if httpResp.StatusCode != http.StatusAccepted && httpResp.StatusCode != http.StatusNoContent {
		return fmt.Errorf("unexpected status code %d when removing subscription by ID (as Tag)", httpResp.StatusCode)
	}
	return nil
}
