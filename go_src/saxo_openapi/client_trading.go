package saxo_openapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http" // Added import
	"net/url"
	"reflect"
	// "strconv" // Removed unused import
	// "strings"
)

// --- Orders ---

// GetOrderParams defines query parameters for fetching a single order.
type GetOrderParams struct {
	AccountKey  string  `url:"AccountKey"`
	ClientKey   *string `url:"ClientKey,omitempty"`
	FieldGroups *string `url:"FieldGroups,omitempty"`
}

// GetOrder retrieves details for a single order.
func (c *Client) GetOrder(ctx context.Context, orderID string, params *GetOrderParams) (*Order, error) {
	if orderID == "" {
		return nil, fmt.Errorf("orderID is required")
	}
	path := fmt.Sprintf("trade/v1/orders/%s", orderID)

	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}

	responseBodyType := reflect.TypeOf(Order{})
	result, _, err := c.doRequest(ctx, "GET", path, queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*Order); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert Order. Got: %T", result)
}


// GetMultiLegOrder retrieves details for a multi-leg order.
func (c *Client) GetMultiLegOrder(ctx context.Context, multiLegOrderID string, params *GetOrderParams) (*Order, error) {
    if multiLegOrderID == "" {
        return nil, fmt.Errorf("multiLegOrderID is required")
    }
    path := fmt.Sprintf("trade/v1/orders/multileg/%s", multiLegOrderID)
    queryParams, err := paramsToQueryValues(params)
    if err != nil {
        return nil, fmt.Errorf("failed to convert params: %w", err)
    }

    responseBodyType := reflect.TypeOf(Order{})
    result, _, err := c.doRequest(ctx, "GET", path, queryParams, nil, responseBodyType)
    if err != nil {
        return nil, err
    }
    if typedResult, ok := result.(*Order); ok {
        return typedResult, nil
    }
    return nil, fmt.Errorf("failed to type assert Order for multi-leg. Got: %T", result)
}


// GetOrdersParams defines query parameters for querying multiple orders.
type GetOrdersParams struct {
	AccountKey        string  `url:"AccountKey"`
	ClientKey         *string `url:"ClientKey,omitempty"`
	FieldGroups       *string `url:"FieldGroups,omitempty"`
	Status            *string `url:"Status,omitempty"`
	Type              *string `url:"Type,omitempty"`
	AssetType         *string `url:"AssetType,omitempty"`
	Uic               *int    `url:"Uic,omitempty"`
	FromLastUpdatedID *string `url:"FromLastUpdatedId,omitempty"`
	Top               *int    `url:"$top,omitempty"`
}

// GetOrders_deprecated retrieves a list of orders for a specific account.
func (c *Client) GetOrders_deprecated(ctx context.Context, params *GetOrdersParams) (*GetOrdersResponse, error) {
	if params == nil || params.AccountKey == "" {
		return nil, fmt.Errorf("AccountKey is required in params")
	}
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}

	responseBodyType := reflect.TypeOf(GetOrdersResponse{})
	result, _, err := c.doRequest(ctx, "GET", "trade/v1/orders", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetOrdersResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetOrdersResponse. Got: %T", result)
}


// PlaceOrder places a new order.
func (c *Client) PlaceOrder(ctx context.Context, orderRequest map[string]interface{}) (*PlaceOrderResponse, error) {
	bodyBytes, err := json.Marshal(orderRequest)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal order request: %w", err)
	}

	responseBodyType := reflect.TypeOf(PlaceOrderResponse{})
	result, _, err := c.doRequest(ctx, "POST", "trade/v1/orders", nil, bytes.NewBuffer(bodyBytes), responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*PlaceOrderResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert PlaceOrderResponse. Got: %T", result)
}

// ModifyOrder modifies an existing order.
func (c *Client) ModifyOrder(ctx context.Context, orderID string, accountKey string, orderUpdates map[string]interface{}) (*ModifyOrderResponse, error) {
	if orderID == "" {
		return nil, fmt.Errorf("orderID is required")
	}
	if accountKey == "" {
		return nil, fmt.Errorf("accountKey is required")
	}
	if orderUpdates == nil {
		orderUpdates = make(map[string]interface{})
	}
	orderUpdates["AccountKey"] = accountKey

	bodyBytes, err := json.Marshal(orderUpdates)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal order updates: %w", err)
	}

	path := fmt.Sprintf("trade/v1/orders/%s", orderID)
	responseBodyType := reflect.TypeOf(ModifyOrderResponse{})

	result, httpResp, err := c.doRequest(ctx, "PATCH", path, nil, bytes.NewBuffer(bodyBytes), responseBodyType)
	if err != nil {
		return nil, err
	}
	if httpResp.StatusCode == http.StatusAccepted || httpResp.StatusCode == http.StatusNoContent {
		return &ModifyOrderResponse{OrderID: orderID}, nil
	}

	if typedResult, ok := result.(*ModifyOrderResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert ModifyOrderResponse or unexpected response. Got: %T", result)
}


// CancelOrder cancels an existing order.
func (c *Client) CancelOrder(ctx context.Context, orderID string, accountKey string) (*CancelOrderResponse, error) {
	if orderID == "" {
		return nil, fmt.Errorf("orderID is required")
	}
	if accountKey == "" {
		return nil, fmt.Errorf("accountKey is required")
	}

	path := fmt.Sprintf("trade/v1/orders/%s", orderID)
	queryParams := url.Values{}
	queryParams.Set("AccountKey", accountKey)

	responseBodyType := reflect.TypeOf(CancelOrderResponse{})
	result, httpResp, err := c.doRequest(ctx, "DELETE", path, queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}

    if httpResp.StatusCode == http.StatusNoContent {
        return &CancelOrderResponse{OrderID: orderID, Status: "Cancelled (assumed from 204)"}, nil
    }
	if typedResult, ok := result.(*CancelOrderResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert CancelOrderResponse. Got: %T", result)
}


// --- Positions (from /trade/v1/positions) ---
type GetTradingPositionsParams struct {
	AccountKey      *string `url:"AccountKey,omitempty"`
	ClientKey       string  `url:"ClientKey"`
	FieldGroups     *string `url:"FieldGroups,omitempty"`
	NettingProfileKey *string `url:"NettingProfileKey,omitempty"`
	Path            *bool   `url:"Path,omitempty"`
	PositionFiltering *string `url:"PositionFiltering,omitempty"`
	Status          *string `url:"Status,omitempty"`
	Uic             *int    `url:"Uic,omitempty"`
	WatchlistName   *string `url:"WatchlistName,omitempty"`
}

func (c *Client) GetTradingPositions(ctx context.Context, params *GetTradingPositionsParams) (*GetTradingPositionsResponse, error) {
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetTradingPositionsResponse{})
	result, _, err := c.doRequest(ctx, "GET", "trade/v1/positions", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetTradingPositionsResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetTradingPositionsResponse. Got: %T", result)
}


// --- Prices (from /trade/v1/prices) ---
type GetPricesParams struct {
	AccountKey  string  `url:"AccountKey"`
	AssetType   string  `url:"AssetType"`
	Uics        string  `url:"Uics"`
	FieldGroups *string `url:"FieldGroups,omitempty"`
}

func (c *Client) GetPrices(ctx context.Context, params *GetPricesParams) (*GetPricesResponse, error) {
	if params == nil || params.AccountKey == "" || params.AssetType == "" || params.Uics == "" {
		return nil, fmt.Errorf("AccountKey, AssetType, and Uics are required in params")
	}
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetPricesResponse{})
	result, _, err := c.doRequest(ctx, "GET", "trade/v1/prices", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetPricesResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetPricesResponse. Got: %T", result)
}

type GetSinglePriceParams struct {
	AccountKey string `url:"AccountKey"`
	FieldGroups *string `url:"FieldGroups,omitempty"`
}
func (c *Client) GetPrice(ctx context.Context, assetType string, uic int, params *GetSinglePriceParams) (*Price, error) {
	if assetType == "" || uic == 0 {
		return nil, fmt.Errorf("assetType and uic are required")
	}
	if params == nil || params.AccountKey == "" {
		return nil, fmt.Errorf("AccountKey is required in params")
	}
	path := fmt.Sprintf("trade/v1/prices/%s/%d", assetType, uic)
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}

	responseBodyType := reflect.TypeOf(Price{})
	result, _, err := c.doRequest(ctx, "GET", path, queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*Price); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert Price. Got: %T", result)
}


// --- InfoPrices (from /trade/v1/infoprices) ---
type GetInfoPricesParams struct {
	AssetType   string  `url:"AssetType"`
	Uics        string  `url:"Uics"`
	AccountKey  *string `url:"AccountKey,omitempty"`
	FieldGroups *string `url:"FieldGroups,omitempty"`
}

func (c *Client) GetInfoPricesList(ctx context.Context, params *GetInfoPricesParams) (*GetInfoPricesListResponse, error) {
	if params == nil || params.AssetType == "" || params.Uics == "" {
		return nil, fmt.Errorf("AssetType and Uics are required in params")
	}
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetInfoPricesListResponse{})
	result, _, err := c.doRequest(ctx, "GET", "trade/v1/infoprices/list", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetInfoPricesListResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetInfoPricesListResponse. Got: %T", result)
}

// --- Option Chain (from /trade/v1/optionschain) ---
type GetOptionChainParams struct {
	AssetType string `url:"AssetType"`
	Uic int `url:"Uic"`
	ExpiryDates *string `url:"ExpiryDates,omitempty"`
	OptionSpaceSegment *string `url:"OptionSpaceSegment,omitempty"`
}
func (c *Client) GetOptionChain(ctx context.Context, params *GetOptionChainParams) (*GetOptionChainResponse, error) {
	if params == nil || params.AssetType == "" || params.Uic == 0 {
		return nil, fmt.Errorf("AssetType and Uic are required in params")
	}
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetOptionChainResponse{})
	result, _, err := c.doRequest(ctx, "GET", "trade/v1/optionschain", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetOptionChainResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetOptionChainResponse. Got: %T", result)
}


// --- AllocationKeys ---
type GetAllocationKeysParams struct {
    OwnerKey string `url:"OwnerKey"`
}
func (c *Client) GetAllocationKeys(ctx context.Context, params *GetAllocationKeysParams) (*GetAllocationKeysResponse, error) {
	if params == nil || params.OwnerKey == "" {
		return nil, fmt.Errorf("OwnerKey is required in params")
	}
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetAllocationKeysResponse{})
	result, _, err := c.doRequest(ctx, "GET", "trade/v1/allocationkeys", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetAllocationKeysResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetAllocationKeysResponse. Got: %T", result)
}


// --- TradeMessages ---
type GetTradeMessagesParams struct {
    AccountKey string `url:"AccountKey"`
    FromID *int `url:"FromId,omitempty"`
    MaxRows *int `url:"MaxRows,omitempty"`
    StatusCSV *string `url:"StatusCsv,omitempty"`
    TypeCSV *string `url:"TypeCsv,omitempty"`
}
func (c *Client) GetTradeMessages(ctx context.Context, params *GetTradeMessagesParams) (*GetTradeMessagesResponse, error) {
	if params == nil || params.AccountKey == "" {
		return nil, fmt.Errorf("AccountKey is required in params")
	}
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetTradeMessagesResponse{})
	result, _, err := c.doRequest(ctx, "GET", "trade/v1/messages", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetTradeMessagesResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetTradeMessagesResponse. Got: %T", result)
}

// --- Screener ---
type GetScreenerItemsParams struct {
    ScreenerID string `url:"ScreenerId"`
    AccountKey *string `url:"AccountKey,omitempty"`
    ClientKey string `url:"ClientKey"`
    Count *int `url:"Count,omitempty"`
    Market *string `url:"Market,omitempty"`
    StartIndex *int `url:"StartIndex,omitempty"`
}
func (c *Client) GetScreenerItems(ctx context.Context, params *GetScreenerItemsParams) (*GetScreenerResponse, error) {
	if params == nil || params.ScreenerID == "" || params.ClientKey == "" {
		return nil, fmt.Errorf("ScreenerId and ClientKey are required in params")
	}
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetScreenerResponse{})
	result, _, err := c.doRequest(ctx, "GET", "trade/v1/screeners", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetScreenerResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetScreenerResponse. Got: %T", result)
}
