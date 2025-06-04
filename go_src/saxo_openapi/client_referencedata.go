package saxo_openapi

import (
	"context"
	"fmt"
	// "net/url" // No longer needed here, paramsToQueryValues is in client.go
	"reflect"
	// "strconv" // No longer needed here
	// "strings" // No longer needed here
)

// --- Instruments ---

// GetInstrumentsParams defines query parameters for fetching instruments.
type GetInstrumentsParams struct {
	AccountKey         *string  `url:"AccountKey,omitempty"`
	AssetTypes         *string  `url:"AssetTypes,omitempty"` // Comma-separated, e.g., "Stock,FxSpot"
	ClientKey          *string  `url:"ClientKey,omitempty"`  // Typically required if AccountKey is not set
	DescriptionLanguage *string  `url:"DescriptionLanguage,omitempty"`
	ExchangeID         *string  `url:"ExchangeId,omitempty"`
	Keywords           *string  `url:"Keywords,omitempty"` // Supports operators like "and", "or", "not"
	Sector             *string  `url:"Sector,omitempty"`
	Symbol             *string  `url:"Symbol,omitempty"`
	ToOpenApiVersion   *string  `url:"ToOpenApiVersion,omitempty"`
	TradableAs         *string  `url:"TradableAs,omitempty"` // Comma-separated
	Uics               *string  `url:"Uics,omitempty"`       // Comma-separated list of UICs
	CanParticipateInMultiLegOrder *bool `url:"CanParticipateInMultiLegOrder,omitempty"`
	IncludeNonTradable *bool    `url:"IncludeNonTradable,omitempty"`
	Top                *int     `url:"$top,omitempty"`    // Number of records to return
	Skip               *int     `url:"$skip,omitempty"`   // Number of records to skip (for pagination)
	Inlinecount        *string  `url:"$inlinecount,omitempty"` // "allpages" or "none"
}

// GetInstruments retrieves a list of instruments based on query parameters.
// GET /openapi/ref/v1/instruments
func (c *Client) GetInstruments(ctx context.Context, params *GetInstrumentsParams) (*GetInstrumentsResponse, error) {
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params to query values: %w", err)
	}

	responseBodyType := reflect.TypeOf(GetInstrumentsResponse{})
	result, _, err := c.doRequest(ctx, "GET", "ref/v1/instruments", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetInstrumentsResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetInstrumentsResponse. Got: %T", result)
}

// GetInstrumentDetailsParams defines parameters for fetching instrument details.
type GetInstrumentDetailsParams struct {
	AccountKey         *string `url:"AccountKey,omitempty"`
	AssetType          string  `url:"AssetType"` // Required
	ClientKey          *string `url:"ClientKey,omitempty"`
	DescriptionLanguage *string `url:"DescriptionLanguage,omitempty"`
	FieldGroups        *string `url:"FieldGroups,omitempty"` // Comma-separated
	ToOpenApiVersion   *string `url:"ToOpenApiVersion,omitempty"`
	Uic                int     `url:"Uic"` // Required
}

// GetInstrumentDetails retrieves detailed information for a single instrument.
// GET /openapi/ref/v1/instruments/details
func (c *Client) GetInstrumentDetails(ctx context.Context, params *GetInstrumentDetailsParams) (*InstrumentDetail, error) {
	if params == nil || params.AssetType == "" || params.Uic == 0 {
		return nil, fmt.Errorf("AssetType and Uic are required in params for GetInstrumentDetails")
	}
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params to query values: %w", err)
	}

	responseBodyType := reflect.TypeOf(InstrumentDetail{})
	result, _, err := c.doRequest(ctx, "GET", "ref/v1/instruments/details", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*InstrumentDetail); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert InstrumentDetail. Got: %T", result)
}

// GetInstrumentDetailsByUicAssetType retrieves detailed information for a single instrument by its UIC and AssetType.
// GET /openapi/ref/v1/instruments/details/{Uic}/{AssetType}
type GetInstrumentDetailsByUicAssetTypeParams struct {
	AccountKey         *string `url:"AccountKey,omitempty"`
	ClientKey          *string `url:"ClientKey,omitempty"`
	DescriptionLanguage *string `url:"DescriptionLanguage,omitempty"`
	FieldGroups        *string `url:"FieldGroups,omitempty"`
	ToOpenApiVersion   *string `url:"ToOpenApiVersion,omitempty"`
}
func (c *Client) GetInstrumentDetailsByUicAssetType(ctx context.Context, uic int, assetType string, params *GetInstrumentDetailsByUicAssetTypeParams) (*InstrumentDetail, error) {
	if assetType == "" || uic == 0 {
		return nil, fmt.Errorf("uic and assetType are required")
	}
	path := fmt.Sprintf("ref/v1/instruments/details/%d/%s", uic, assetType)

	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}

	responseBodyType := reflect.TypeOf(InstrumentDetail{})
	result, _, err := c.doRequest(ctx, "GET", path, queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*InstrumentDetail); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert InstrumentDetail. Got: %T", result)
}


// --- Exchanges ---

// GetExchangesParams defines parameters for fetching exchanges.
type GetExchangesParams struct {
	MarketType *string `url:"MarketType,omitempty"`
	CountryCode *string `url:"CountryCode,omitempty"`
}

// GetExchanges retrieves a list of exchanges.
// GET /openapi/ref/v1/exchanges
func (c *Client) GetExchanges(ctx context.Context, params *GetExchangesParams) (*GetExchangesResponse, error) {
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetExchangesResponse{})
	result, _, err := c.doRequest(ctx, "GET", "ref/v1/exchanges", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetExchangesResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetExchangesResponse. Got: %T", result)
}

// GetExchange retrieves details for a single exchange by its MIC.
// GET /openapi/ref/v1/exchanges/{Mic}
func (c *Client) GetExchange(ctx context.Context, mic string) (*Exchange, error) {
	if mic == "" {
		return nil, fmt.Errorf("mic is required")
	}
	path := fmt.Sprintf("ref/v1/exchanges/%s", mic)

	responseBodyType := reflect.TypeOf(Exchange{})
	result, _, err := c.doRequest(ctx, "GET", path, nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*Exchange); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert Exchange. Got: %T", result)
}


// --- Currencies ---

// GetCurrencies retrieves a list of currencies.
// GET /openapi/ref/v1/currencies
func (c *Client) GetCurrencies(ctx context.Context) (*GetCurrenciesResponse, error) {
	responseBodyType := reflect.TypeOf(GetCurrenciesResponse{})
	result, _, err := c.doRequest(ctx, "GET", "ref/v1/currencies", nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetCurrenciesResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetCurrenciesResponse. Got: %T", result)
}


// --- Currency Pairs ---
type GetCurrencyPairsParams struct {
	CurrencyCodes *string `url:"CurrencyCodes,omitempty"`
}
// GetCurrencyPairs retrieves a list of currency pairs.
// GET /openapi/ref/v1/currencypairs
func (c *Client) GetCurrencyPairs(ctx context.Context, params *GetCurrencyPairsParams) (*GetCurrencyPairsResponse, error) {
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetCurrencyPairsResponse{})
	result, _, err := c.doRequest(ctx, "GET", "ref/v1/currencypairs", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetCurrencyPairsResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetCurrencyPairsResponse. Got: %T", result)
}

// --- Cultures ---
func (c *Client) GetCultures(ctx context.Context) (*GetCulturesResponse, error) {
	responseBodyType := reflect.TypeOf(GetCulturesResponse{})
	result, _, err := c.doRequest(ctx, "GET", "ref/v1/cultures", nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetCulturesResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetCulturesResponse. Got: %T", result)
}

// --- Languages ---
func (c *Client) GetLanguages(ctx context.Context) (*GetLanguagesResponse, error) {
	responseBodyType := reflect.TypeOf(GetLanguagesResponse{})
	result, _, err := c.doRequest(ctx, "GET", "ref/v1/languages", nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetLanguagesResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetLanguagesResponse. Got: %T", result)
}

// --- Timezones ---
func (c *Client) GetTimezones(ctx context.Context) (*GetTimezonesResponse, error) {
	responseBodyType := reflect.TypeOf(GetTimezonesResponse{})
	result, _, err := c.doRequest(ctx, "GET", "ref/v1/timezones", nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetTimezonesResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetTimezonesResponse. Got: %T", result)
}

// --- Standard Dates ---
type GetStandardDatesParams struct {
	TimeZoneID *string `url:"TimeZoneId,omitempty"`
	Year       int     `url:"Year"`
}
func (c *Client) GetStandardDates(ctx context.Context, params *GetStandardDatesParams) (*GetStandardDatesResponse, error) {
	if params == nil || params.Year == 0 {
		return nil, fmt.Errorf("year is required in params for GetStandardDates")
	}
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetStandardDatesResponse{})
	result, _, err := c.doRequest(ctx, "GET", "ref/v1/standarddates", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetStandardDatesResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetStandardDatesResponse. Got: %T", result)
}

// --- Algo Strategies ---
func (c *Client) GetAlgoStrategies(ctx context.Context, assetType string) (*GetAlgoStrategiesResponse, error) {
	if assetType == "" {
		return nil, fmt.Errorf("assetType is required")
	}
	path := fmt.Sprintf("ref/v1/algostrategies/%s", assetType)

	responseBodyType := reflect.TypeOf(GetAlgoStrategiesResponse{})
	result, _, err := c.doRequest(ctx, "GET", path, nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetAlgoStrategiesResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetAlgoStrategiesResponse. Got: %T", result)
}

// --- Countries ---
type GetCountriesParams struct {
	LegalAssetTypesTradable *string `url:"LegalAssetTypesTradable,omitempty"`
}
func (c *Client) GetCountries(ctx context.Context, params *GetCountriesParams) (*GetCountriesResponse, error) {
	queryParams, err := paramsToQueryValues(params)
	if err != nil {
		return nil, fmt.Errorf("failed to convert params: %w", err)
	}
	responseBodyType := reflect.TypeOf(GetCountriesResponse{})
	result, _, err := c.doRequest(ctx, "GET", "ref/v1/countries", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*GetCountriesResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert GetCountriesResponse. Got: %T", result)
}

func (c *Client) GetCountry(ctx context.Context, countryCode string) (*Country, error) {
	if countryCode == "" {
		return nil, fmt.Errorf("countryCode is required")
	}
	path := fmt.Sprintf("ref/v1/countries/%s", countryCode)

	responseBodyType := reflect.TypeOf(Country{})
	result, _, err := c.doRequest(ctx, "GET", path, nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*Country); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert Country. Got: %T", result)
}
