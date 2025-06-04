package saxo_openapi

import "time"

// Note: Many of these structs are based on the Python client's response classes.
// Field names and types should be adjusted based on actual API responses from Saxo.
// Pointers are used for fields that can be null or are optional in the JSON response.

// --- Accounts ---

// Account represents a single trading account.
type Account struct {
	AccountGroupKey                  *string   `json:"AccountGroupKey"`
	AccountID                        string    `json:"AccountId"` // Usually a key identifier
	AccountKey                       string    `json:"AccountKey"`
	AccountType                      string    `json:"AccountType"` // E.g., "NormalTrading", "Isa"
	Active                           bool      `json:"Active"`
	CanUseCashPositionsAsMarginCollateral bool `json:"CanUseCashPositionsAsMarginCollateral"`
	ClientID                        string    `json:"ClientId"`
	Currency                        string    `json:"Currency"`
	CurrencyDecimals                int       `json:"CurrencyDecimals"`
	DirectMarketAccess              bool      `json:"DirectMarketAccess"`
	IndividualMargining             bool      `json:"IndividualMargining"`
	IsMarginTradingAllowed          bool      `json:"IsMarginTradingAllowed"`
	IsTrialAccount                  bool      `json:"IsTrialAccount"`
	LegalAssetTypes                 []string  `json:"LegalAssetTypes"` // E.g., ["FxSpot", "Stock"]
	Sharing                         *string   `json:"Sharing"`         // E.g., "SharedWithMe", "None"
	SupportsAccountValueProtection  bool      `json:"SupportsAccountValueShield"`
	AccountValueProtectionLimit     *float64  `json:"AccountValueProtectionLimit"`
	InitialMargin                   *InitialMargin `json:"InitialMargin"` // Nested struct
}

// InitialMargin contains initial margin information for an account.
type InitialMargin struct {
	CalculationReliability string  `json:"CalculationReliability"` // E.g. "Ok"
	CashAvailableAsMarginCollateral float64 `json:"CashAvailableAsMarginCollateral"`
	MarginAvailable        float64 `json:"MarginAvailable"`
	MarginCollateralNotAvailable float64 `json:"MarginCollateralNotAvailable"`
	MarginCoverage         float64 `json:"MarginCoverage"`
	MarginExposure         float64 `json:"MarginExposure"`
	MarginNetExposure      float64 `json:"MarginNetExposure"`
	MarginUsedByCurrentPositions float64 `json:"MarginUsedByCurrentPositions"`
	MarginUtilizationPct   float64 `json:"MarginUtilizationPct"`
	NetEquityForMargin     float64 `json:"NetEquityForMargin"`
}

// GetAccountsResponse is the response structure for querying multiple accounts.
type GetAccountsResponse struct {
	Data []Account `json:"Data"`
}


// --- Balances ---

// Balance represents the financial balance of an account.
type Balance struct {
	CashAvailableAsMarginCollateral float64 `json:"CashAvailableAsMarginCollateral"`
	CashBalance                     float64 `json:"CashBalance"`
	ChangesInExposureSinceLastDay   float64 `json:"ChangesInExposureSinceLastDay"`
	ChangesInMarginSinceLastDay     float64 `json:"ChangesInMarginSinceLastDay"`
	CollateralAvailable             float64 `json:"CollateralAvailable"` // Not present in Python example, but common
	CollateralCreditValue           float64 `json:"CollateralCreditValue"`
	CostToClosePositions            float64 `json:"CostToClosePositions"`
	Currency                        string  `json:"Currency"`
	CurrencySymbol                  string  `json:"CurrencySymbol"` // Not in Python, but useful
	InitialMargin                   InitialMargin `json:"InitialMargin"` // Re-use struct from Account
	IsPortfolioMarginModel          bool    `json:"IsPortfolioMarginModel"`
	MarginAvailable                 float64 `json:"MarginAvailable"`
	MarginCollateralNotAvailable    float64 `json:"MarginCollateralNotAvailable"`
	MarginExposureCoverage          float64 `json:"MarginExposureCoverage"`
	MarginNetExposure               float64 `json:"MarginNetExposure"`
	MarginPledgedAsCollateral       float64 `json:"MarginPledgedAsCollateral"`
	MarginUsedByCurrentPositions    float64 `json:"MarginUsedByCurrentPositions"`
	MarginUtilization               float64 `json:"MarginUtilization"`
	NetEquityForMargin              float64 `json:"NetEquityForMargin"`
	NetPositionsCount               int     `json:"NetPositionsCount"`
	NonMarginPositionsValue         float64 `json:"NonMarginPositionsValue"`
	OpenPositionsCount              int     `json:"OpenPositionsCount"`
	OptionPremiumsMarketValue       float64 `json:"OptionPremiumsMarketValue"`
	OrdersCount                     int     `json:"OrdersCount"`
	OtherCollateral                 float64 `json:"OtherCollateral"`
	SettlementValue                 float64 `json:"SettlementValue"`
	TotalValue                      float64 `json:"TotalValue"`
	TransactionsNotBooked           float64 `json:"TransactionsNotBooked"`
	UnrealizedMarginClosedProfitLoss float64 `json:"UnrealizedMarginClosedProfitLoss"`
	UnrealizedMarginOpenProfitLoss  float64 `json:"UnrealizedMarginOpenProfitLoss"`
	UnrealizedMarginProfitLoss      float64 `json:"UnrealizedMarginProfitLoss"`
	UnrealizedPositionsValue        float64 `json:"UnrealizedPositionsValue"`
}

// --- Positions ---

// PositionBase contains common fields for positions.
type PositionBase struct {
	AccountID                 string    `json:"AccountId"` // Not in Saxo's PositionBase, but useful context
	AccountKey                string    `json:"AccountKey"`
	Amount                    float64   `json:"Amount"`
	AssetType                 string    `json:"AssetType"` // E.g. "Stock", "FxSpot", "CfdOnStock"
	CanBeClosed               bool      `json:"CanBeClosed"`
	ClientID                  string    `json:"ClientId"`
	CloseConversionRateSettled bool     `json:"CloseConversionRateSettled"`
	CorrelatedInstruments     *[]string `json:"CorrelatedInstruments"` // Optional
	OpenPrice                 float64   `json:"OpenPrice"`
	SpotPrice                 *float64  `json:"SpotPrice"` // For some asset types
	Status                    string    `json:"Status"`    // E.g. "Open"
	Uic                       int       `json:"Uic"`
	ValueDate                 string    `json:"ValueDate"` // Date string "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS..."
}

// PositionData contains details specific to an open position.
type PositionData struct {
	CalculationReliability    string    `json:"CalculationReliability"`
	CashAvailableAsMarginCollateral float64 `json:"CashAvailableAsMarginCollateral"`
	CurrentPrice              float64   `json:"CurrentPrice"`
	CurrentPriceDelayMinutes  float64   `json:"CurrentPriceDelayMinutes"`
	CurrentPriceType          string    `json:"CurrentPriceType"`
	Exposure                  float64   `json:"Exposure"`
	ExposureInBaseCurrency    float64   `json:"ExposureInBaseCurrency"`
	InstrumentPriceDayPercentChange float64 `json:"InstrumentPriceDayPercentChange"`
	MarketValue               float64   `json:"MarketValue"`
	MarketValueInBaseCurrency float64   `json:"MarketValueInBaseCurrency"`
	ProfitLossOnTrade         float64   `json:"ProfitLossOnTrade"`
	ProfitLossOnTradeInBaseCurrency float64 `json:"ProfitLossOnTradeInBaseCurrency"`
	TradeCostsTotal           float64   `json:"TradeCostsTotal"`         // In base currency
	TradeCostsTotalInBaseCurrency float64 `json:"TradeCostsTotalInBaseCurrency"`
}

// Position is the full structure for an open position.
type Position struct {
	NetPositionID string       `json:"NetPositionId"` // This is for linking to NetPosition
	PositionBase  PositionBase `json:"PositionBase"`
	PositionData  PositionData `json:"PositionView"` // Saxo often calls this "PositionView" or "DisplayAndFormat"
	PositionID    string       `json:"PositionId"`
	// SinglePosition *bool     `json:"SinglePosition"` // Not standard, from Python example
	// SinglePositionQty *float64 `json:"SinglePositionQty"`// Not standard
}

// GetPositionsResponse is for fetching multiple open positions.
type GetPositionsResponse struct {
	Count int        `json:"__count"` // Or Count, check API
	Data  []Position `json:"Data"`
}

// --- Net Positions ---

// NetPosition holds information about a net position.
type NetPosition struct {
	NetPositionID string       `json:"NetPositionId"`
	PositionBase  PositionBase `json:"PositionBase"` // Common fields
	PositionData  PositionData `json:"PositionView"` // Details
}

// GetNetPositionsResponse is for fetching multiple net positions.
type GetNetPositionsResponse struct {
	Count int           `json:"__count"`
	Data  []NetPosition `json:"Data"`
}


// --- Closed Positions ---

// ClosedPositionData contains details specific to a closed position.
type ClosedPositionData struct {
	ClosePrice                float64   `json:"ClosePrice"`
	ClosedProfitLoss          float64   `json:"ClosedProfitLoss"`
	ClosedProfitLossInBaseCurrency float64 `json:"ClosedProfitLossInBaseCurrency"`
	ClosingMarketValue        float64   `json:"ClosingMarketValue"`
	ClosingMarketValueInBaseCurrency float64 `json:"ClosingMarketValueInBaseCurrency"`
	ClosingDate               time.Time `json:"ClosingDate"` // Or string, check format
	ConversionRate            float64   `json:"ConversionRate"`
	ExecutionTimeOpen         time.Time `json:"ExecutionTimeOpen"`
	ExecutionTimeClose        time.Time `json:"ExecutionTimeClose"`
	OpeningMarketValue        float64   `json:"OpeningMarketValue"`
	OpeningMarketValueInBaseCurrency float64 `json:"OpeningMarketValueInBaseCurrency"`
	TradeCostsTotal           float64   `json:"TradeCostsTotal"`
	TradeCostsTotalInBaseCurrency float64 `json:"TradeCostsTotalInBaseCurrency"`
}

// ClosedPosition is the full structure for a closed position.
type ClosedPosition struct {
	ClosedPositionData ClosedPositionData `json:"ClosedPositionView"` // Or similar key from API
	NetPositionID      string             `json:"NetPositionId"`
	PositionBase       PositionBase       `json:"PositionBase"`
	PositionID         string             `json:"PositionId"`
}

// GetClosedPositionsResponse is for fetching multiple closed positions.
type GetClosedPositionsResponse struct {
	Count int              `json:"__count"`
	Data  []ClosedPosition `json:"Data"`
}

// --- Orders ---
// Order-related structs would be extensive. For now, a placeholder if needed by portfolio.
// Actual order definitions would be in an "orders" or "trading" package.
// This is just if portfolio views return some summarized order info.
type OrderSummary struct {
	OrderID string  `json:"OrderId"`
	Amount  float64 `json:"Amount"`
	Price   float64 `json:"Price"`
	Status  string  `json:"Status"`
	// ... other fields
}


// --- AccountGroups ---
// AccountGroup represents a group of accounts.
type AccountGroup struct {
    AccountGroupKey string `json:"AccountGroupKey"`
    GroupName       string `json:"GroupName"`
    OwnerKey        string `json:"OwnerKey"`
    // ... other relevant fields
}

// GetAccountGroupsResponse is for fetching multiple account groups.
type GetAccountGroupsResponse struct {
    Data []AccountGroup `json:"Data"`
}

// --- Exposure ---
// CurrencyExposure represents exposure in a specific currency.
type CurrencyExposure struct {
    Amount              float64 `json:"Amount"`
    Currency            string  `json:"Currency"`
    ExposureCrossRate   float64 `json:"ExposureCrossRate"`
    ExposureInBaseCcy   float64 `json:"ExposureInBaseCcy"`
}

// InstrumentExposureDetail provides details for a single instrument's exposure.
type InstrumentExposureDetail struct {
    Amount                  float64 `json:"Amount"`
    AssetType               string  `json:"AssetType"`
    CanBeClosed             bool    `json:"CanBeClosed"`
    Description             string  `json:"Description"`
    DisplayName             string  `json:"DisplayName"` // Not in py example, but useful
    Exposure                float64 `json:"Exposure"`
    ExposureConvRate        float64 `json:"ExposureConvRate"`
    ExposureInBaseCcy       float64 `json:"ExposureInBaseCcy"`
    InstrumentSymbol        string  `json:"InstrumentSymbol"` // Not in py example
    Uic                     int     `json:"Uic"`
}

// InstrumentExposure holds exposure data grouped by instrument.
type InstrumentExposure struct {
    CalculationReliability  string                     `json:"CalculationReliability"`
    Currency                string                     `json:"Currency"` // Base currency of the account
    CurrencyExposure        []CurrencyExposure         `json:"CurrencyExposure"`
    InstrumentExposureDetails []InstrumentExposureDetail `json:"InstrumentExposureDetails"`
}

// --- Subscriptions (Portfolio) ---

// Subscription represents a subscription to an account's portfolio events.
type Subscription struct {
    ContextID         string                 `json:"ContextId"`
    Format            string                 `json:"Format,omitempty"` // Optional, e.g. "application/json"
    InactivityTimeout int                    `json:"InactivityTimeout"` // Seconds
    ReferenceID       string                 `json:"ReferenceId"`
    RefreshRate       int                    `json:"RefreshRate"` // Milliseconds
    Snapshot          map[string]interface{} `json:"Snapshot"`    // Snapshot of data, structure varies
    Tag               string                 `json:"Tag,omitempty"`     // Optional
		State             string                 `json:"State"` // Added from Saxo docs (Active, Reserved, etc.)
		TargetReferenceID string                 `json:"TargetReferenceId"` // Added from Saxo docs
}

// CreateSubscriptionArgs are arguments for creating a new subscription.
type CreateSubscriptionArgs struct {
    ContextID         string `json:"ContextId"`
    Format            string `json:"Format,omitempty"`
    ReferenceID       string `json:"ReferenceId"`
    RefreshRate       int    `json:"RefreshRate"`
    Tag               string `json:"Tag,omitempty"`
    // Arguments is a map of specific arguments for the subscription type
    // e.g., for accounts: {"AccountKey": "...", "FieldGroups": ["DisplayAndFormat", "PositionBase"]}
    Arguments map[string]interface{} `json:"Arguments"`
}

// CreateSubscriptionResponse is the response from creating a subscription.
type CreateSubscriptionResponse Subscription // Typically returns the created subscription details

// RemoveSubscriptionResponse (usually 202 Accepted, no body or simple status)
type RemoveSubscriptionResponse struct {
    Status string // e.g. "Successfully deleted" or similar, if API returns a body
}
