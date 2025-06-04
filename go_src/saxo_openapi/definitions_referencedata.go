package saxo_openapi

import "time"

// --- Instruments ---

// InstrumentDetail represents detailed information about an instrument.
// This is a complex type; fields are based on common Saxo responses.
// It might be broken down further or adjusted based on specific /details endpoint.
type InstrumentDetail struct {
	AmountDecimals             int      `json:"AmountDecimals"`
	AssetType                  string   `json:"AssetType"` // E.g., "Stock", "FxSpot", "CfdOnStock"
	CurrencyCode               string   `json:"CurrencyCode"`
	DefaultAmount              *float64 `json:"DefaultAmount"` // Optional
	DefaultSlippage            *float64 `json:"DefaultSlippage"`
	DefaultSlippageType        *string  `json:"DefaultSlippageType"`
	Description                string   `json:"Description"`
	Exchange                   Exchange `json:"Exchange"` // Nested Exchange info
	Format                     Format   `json:"Format"`   // Nested Format info
	GroupID                    int      `json:"GroupId"`
	IncrementSize              *float64 `json:"IncrementSize"`
	IsTradable                 bool     `json:"IsTradable"` // Not in Python example, but common
	LotSize                    *float64 `json:"LotSize"`    // Not in Python example, but common
	MinimumTradeSize           *float64 `json:"MinimumTradeSize"`
	OrderDistances             OrderDistances `json:"OrderDistances"`
	PriceCurrency              string   `json:"PriceCurrency"` // Not in Python example
	PrimaryExchange            *string  `json:"PrimaryExchange"` // Optional
	RelatedInstruments         *[]RelatedInstrument `json:"RelatedInstruments"` // Optional
	StandardAmounts            *[]float64 `json:"StandardAmounts"` // Optional
	SupportedOrderTypes        []string `json:"SupportedOrderTypes"`
	Symbol                     string   `json:"Symbol"`
	TickSize                   *float64 `json:"TickSize"` // Optional
	TickSizeScheme             *TickSizeScheme `json:"TickSizeScheme"` // Optional
	TradableAs                 []string `json:"TradableAs"` // E.g. ["FxSpot", "FxForward"]
	TradableOn                 []string `json:"TradableOn"` // Exchange IDs
	TradingSignals             *string  `json:"TradingSignals"` // Optional
	Uic                        int      `json:"Uic"`
	RelatedOptionRoots         *string  `json:"RelatedOptionRoots"` // Example, specific to options
	// Many more fields can exist depending on AssetType...
	// Stock specific
	IsinCode                   *string   `json:"IsinCode"`
	PriceToBook                *float64  `json:"PriceToBook"`
	PriceToEarnings            *float64  `json:"PriceToEarnings"`
	SectorID                   *string   `json:"SectorId"`
	SectorName                 *string   `json:"SectorName"`
	StandardTradableLotSize    *float64  `json:"StandardTradableLotSize"`
	// Bond specific
	AccruedInterest            *float64  `json:"AccruedInterest"`
	CleanPrice                 *float64  `json:"CleanPrice"`
	DirtyPrice                 *float64  `json:"DirtyPrice"`
	IssueDate                  *string   `json:"IssueDate"` // Date string
	MaturityDate               *string   `json:"MaturityDate"` // Date string
	NominalValue               *float64  `json:"NominalValue"`
	// Futures/Options specific
	ContractSize               *float64  `json:"ContractSize"`
	ContractValue              *float64  `json:"ContractValue"`
	ExpiryDate                 *string   `json:"ExpiryDate"` // Date string
	FirstTradingDate           *string   `json:"FirstTradingDate"`
	LastTradingDate            *string   `json:"LastTradingDate"`
	OptionType                 *string   `json:"OptionType"` // "Call" or "Put"
	StrikePrice                *float64  `json:"StrikePrice"`
	UnderlyingAssetType        *string   `json:"UnderlyingAssetType"`
	UnderlyingUic              *int      `json:"UnderlyingUic"`
}

// Format defines display and formatting rules for an instrument.
type Format struct {
	Decimals    int    `json:"Decimals"`
	Format      string `json:"Format"` // E.g., "ALLOW_DECIMALS"
	OrderDecimals int  `json:"OrderDecimals"`
	StrikeDecimals int `json:"StrikeDecimals,omitempty"` // For options
}

// OrderDistances defines minimum and default distances for order prices.
type OrderDistances struct {
	DefaultDistanceLimit     *float64 `json:"DefaultDistanceLimit"`
	DefaultDistanceStop      *float64 `json:"DefaultDistanceStop"`
	DefaultDistanceStopLimit *float64 `json:"DefaultDistanceStopLimit"`
	DefaultDistanceTakeProfit*float64 `json:"DefaultDistanceTakeProfit"`
	MinimumDistanceLimit     *float64 `json:"MinimumDistanceLimit"`
	MinimumDistanceStop      *float64 `json:"MinimumDistanceStop"`
	MinimumDistanceStopLimit *float64 `json:"MinimumDistanceStopLimit"`
	MinimumDistanceTakeProfit*float64 `json:"MinimumDistanceTakeProfit"`
}

// RelatedInstrument defines a related instrument.
type RelatedInstrument struct {
	AmountRatio float64 `json:"AmountRatio"`
	AssetType   string  `json:"AssetType"`
	Description string  `json:"Description"`
	Symbol      string  `json:"Symbol"`
	Uic         int     `json:"Uic"`
}

// TickSizeScheme defines a tick size scheme.
type TickSizeScheme struct {
	DefaultTickSize float64     `json:"DefaultTickSize"`
	Elements        []TickSizeElement `json:"Elements"`
}
type TickSizeElement struct {
	HighPrice float64 `json:"HighPrice"`
	TickSize  float64 `json:"TickSize"`
}


// GetInstrumentsResponse is for fetching multiple instruments (summary).
type GetInstrumentsResponse struct {
	Data  []InstrumentDetail `json:"Data"` // Often InstrumentDetail is used even for list summaries
	Next  *string            `json:"__next"` // For pagination
	Count *int               `json:"__count"` // Total count, if available
}

// GetInstrumentDetailsResponse is usually a single InstrumentDetail.
// Alias for clarity, or could be InstrumentDetail directly.
type GetInstrumentDetailsResponse = InstrumentDetail


// --- Exchanges ---

// Exchange represents a stock exchange.
type Exchange struct {
	CountryCode     string `json:"CountryCode"`
	Currency        string `json:"Currency"`
	ExchangeID      string `json:"ExchangeId"`
	ExchangeMIC     string `json:"ExchangeMic"` // Market Identifier Code
	Name            string `json:"Name"`
	TimeZoneID      string `json:"TimeZoneId"` // IANA Timezone ID
	TimeZoneOffset  string `json:"TimeZoneOffset"` // E.g. "+01:00:00" (not used in Python example, but common)
	City            *string `json:"City"`
	DataProvider    *string `json:"DataProvider"`
	IsOpen          *bool   `json:"IsOpen"`
	MarketType      *string `json:"MarketType"`
	SessionTime     *string `json:"SessionTime"` // E.g. "09:00:00-17:30:00"
	TradesPossible  *bool   `json:"TradesPossible"`
}

// GetExchangesResponse is for fetching multiple exchanges.
type GetExchangesResponse struct {
	Data []Exchange `json:"Data"`
}

// --- Currencies ---

// Currency represents a currency.
type Currency struct {
	Code            string `json:"Code"`
	Decimals        int    `json:"Decimals"`
	Description     string `json:"Description"`
	Name            string `json:"Name"` // Not in Python example, but useful
	Symbol          string `json:"Symbol"`
	TradableAsCash  bool   `json:"TradableAsCash"`
	TradableAsSpot  bool   `json:"TradableAsSpot"`
}

// GetCurrenciesResponse is for fetching multiple currencies.
type GetCurrenciesResponse struct {
	Data []Currency `json:"Data"`
}


// --- Currency Pairs ---

// CurrencyPair represents a currency pair.
type CurrencyPair struct {
	AmountDecimals        int      `json:"AmountDecimals"`
	DefaultAmount         float64  `json:"DefaultAmount"`
	Description           string   `json:"Description"`
	FromCurrency          Currency `json:"FromCurrency"` // Nested Currency
	GroupID               int      `json:"GroupId"`
	IsTradable            bool     `json:"IsTradable"`
	PriceDecimals         int      `json:"PriceDecimals"`
	Symbol                string   `json:"Symbol"`
	ToCurrency            Currency `json:"ToCurrency"` // Nested Currency
	TradableAs            []string `json:"TradableAs"`
	Uic                   int      `json:"Uic"`
}

// GetCurrencyPairsResponse is for fetching multiple currency pairs.
type GetCurrencyPairsResponse struct {
	Data []CurrencyPair `json:"Data"`
}


// --- Cultures ---
type Culture struct {
	CultureCode string `json:"CultureCode"` // e.g. "en-US"
	DisplayName string `json:"DisplayName"` // e.g. "English (United States)"
	Name        string `json:"Name"`        // e.g. "en-US"
}
type GetCulturesResponse struct {
	Data []Culture `json:"Data"`
}

// --- Languages ---
type Language struct {
	LanguageCode string `json:"LanguageCode"` // e.g. "en"
	Name         string `json:"Name"`         // e.g. "English"
}
type GetLanguagesResponse struct {
	Data []Language `json:"Data"`
}

// --- Timezones ---
type TimeZone struct {
	DisplayName string `json:"DisplayName"` // e.g. "(UTC-05:00) Eastern Time (US & Canada)"
	SupportsDaylightSavingTime bool `json:"SupportsDaylightSavingTime"`
	TimeZoneID  string `json:"TimeZoneId"`  // e.g. "America/New_York" (IANA ID)
	UTCOffset   string `json:"UtcOffset"`   // e.g. "-PT5H" or "-05:00:00"
}
type GetTimezonesResponse struct {
	Data []TimeZone `json:"Data"`
}

// --- Standard Dates ---
type StandardDate struct {
	Date        time.Time `json:"Date"` // Or string, then parse
	DateString  string    `json:"DateString"` // YYYY-MM-DD
	Description string    `json:"Description"`
}
type GetStandardDatesResponse struct {
	Data []StandardDate `json:"Data"`
}

// --- Algo Strategies ---
type AlgoStrategy struct {
	ID          string `json:"Id"`
	Name        string `json:"Name"`
	Description string `json:"Description"`
	Parameters  []AlgoStrategyParameter `json:"Parameters"`
}
type AlgoStrategyParameter struct {
	DataType    string      `json:"DataType"` // e.g. "Decimal", "String", "Boolean"
	Default     interface{} `json:"Default"`  // Can be float64, string, bool
	Description string      `json:"Description"`
	DisplayName string      `json:"DisplayName"`
	ID          string      `json:"Id"`
	Name        string      `json:"Name"`
	Required    bool        `json:"Required"`
	// Min, Max, EnumValues, etc. could be here depending on DataType
}
type GetAlgoStrategiesResponse struct {
	Data []AlgoStrategy `json:"Data"`
}


// --- Countries ---
type Country struct {
    CountryCode string `json:"CountryCode"` // e.g. "US"
    DefaultCulture string `json:"DefaultCulture"` // e.g. "en-US"
    IsTradable bool `json:"IsTradable"`
    Name string `json:"Name"` // e.g. "United States"
}
type GetCountriesResponse struct {
    Data []Country `json:"Data"`
}
