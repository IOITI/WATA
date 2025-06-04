package saxo_openapi

import "time"

// --- Common Trading Related Sub-Structs ---

// OrderDuration represents the duration of an order.
type OrderDuration struct {
	DurationType     string `json:"DurationType"` // E.g., "DayOrder", "GoodTillDate", "GoodTillCancel"
	ExpirationDateTime *string `json:"ExpirationDateTime,omitempty"` // Required for GtdDate, YYYY-MM-DDTHH:MM:SS[.fffffff]Z or YYYY-MM-DD
	GtDDate            *string `json:"GtDDate,omitempty"` // Legacy, use ExpirationDateTime
}

// OrderAmountType defines the type of amount for an order.
// Examples: "Quantity", "CashAmount", "Currency" (for FX)
type OrderAmountType string

const (
	AmountTypeQuantity   OrderAmountType = "Quantity"
	AmountTypeCashAmount OrderAmountType = "CashAmount"
	AmountTypeCurrency   OrderAmountType = "Currency"
)

// OrderDirection defines the direction of an order.
type OrderDirection string

const (
	DirectionBuy  OrderDirection = "Buy"
	DirectionSell OrderDirection = "Sell"
)

// OrderType defines the type of an order.
// Examples: "Limit", "Market", "Stop", "StopLimit", "TrailingStop", "Algo"
type OrderType string
// Specific order types will be defined in contrib_orders_types.go as constants for clarity

// ToOpenClose defines whether an order is to open or close a position.
type ToOpenClose string

const (
	ToOpen        ToOpenClose = "ToOpen"
	ToClose       ToOpenClose = "ToClose"
	ToCloseReduce ToOpenClose = "ToCloseReduce" // Reduce existing position, don't open opposite
)


// --- Orders ---

// Order represents a trading order.
// This struct is a general representation; specific fields might vary based on AssetType and OrderType.
type Order struct {
	AccountID                   *string   `json:"AccountId,omitempty"` // Usually not in response, but useful for context
	AccountKey                  string    `json:"AccountKey"`
	AlgoStrategyName            *string   `json:"AlgoStrategyName,omitempty"`
	Amount                      float64   `json:"Amount"`
	AmountType                  OrderAmountType `json:"AmountType"`
	AssetType                   string    `json:"AssetType"` // E.g., "Stock", "FxSpot"
	BuySell                     OrderDirection `json:"BuySell"` // Python uses BuySell, Go uses Direction
	CalculationReliability      *string   `json:"CalculationReliability,omitempty"`
	ClientID                    *string   `json:"ClientId,omitempty"`
	ClientKey                   *string   `json:"ClientKey,omitempty"` // Usually in response
	CorrelationKey              *string   `json:"CorrelationKey,omitempty"`
	CurrentPrice                *float64  `json:"CurrentPrice,omitempty"`
	CurrentPriceDelayMinutes    *float64  `json:"CurrentPriceDelayMinutes,omitempty"`
	CurrentPriceType            *string   `json:"CurrentPriceType,omitempty"`
	DistanceToMarket            *float64  `json:"DistanceToMarket,omitempty"`
	Duration                    OrderDuration `json:"Duration"`
	ExternalReference           *string   `json:"ExternalReference,omitempty"`
	FilledAmount                *float64  `json:"FilledAmount,omitempty"` // Not in Python example but common
	IsMarketOpen                *bool     `json:"IsMarketOpen,omitempty"`
	MarketPrice                 *float64  `json:"MarketPrice,omitempty"`
	OpenOrderType               *OrderType `json:"OpenOrderType,omitempty"` // For conditional orders
	OrderAmount                 *float64  `json:"OrderAmount,omitempty"` // Legacy, use Amount
	OrderID                     string    `json:"OrderId"` // Key identifier
	OrderPrice                  *float64  `json:"OrderPrice,omitempty"` // For Limit, StopLimit
	OrderType                   OrderType `json:"OrderType"`
	Price                       *float64  `json:"Price,omitempty"` // Alias for OrderPrice often
	RelatedOpenOrders           *[]RelatedOrderInfo `json:"RelatedOpenOrders,omitempty"`
	RelatedPositionID           *string   `json:"RelatedPositionId,omitempty"`
	Status                      string    `json:"Status"` // E.g. "Working", "Filled", "Cancelled"
	StopLimitPrice              *float64  `json:"StopLimitPrice,omitempty"` // For StopLimit orders
	ToOpenClose                 *ToOpenClose `json:"ToOpenClose,omitempty"`
	TradingStatus               *string   `json:"TradingStatus,omitempty"` // E.g. "Tradable", "NonTradable"
	Uic                         int       `json:"Uic"`
	ValueDate                   *string   `json:"ValueDate,omitempty"` // YYYY-MM-DD
	// Fields for GSL (Guaranteed Stop Loss)
	GslOrderID                  *string   `json:"GslOrderId,omitempty"`
	GslServiceCharge            *float64  `json:"GslServiceCharge,omitempty"`
	IsGslOrder                  *bool     `json:"IsGslOrder,omitempty"`
	// Timestamps
	OrderTime                   *time.Time `json:"OrderTime,omitempty"` // Or string from API
	LastUpdated                 *time.Time `json:"LastUpdated,omitempty"`
	// Array of related orders (e.g. StopLoss, TakeProfit)
	Orders                      *[]Order `json:"Orders,omitempty"`
}

// RelatedOrderInfo provides info about related orders.
type RelatedOrderInfo struct {
	OrderID string    `json:"OrderId"`
	OrderType OrderType `json:"OrderType"`
	Status  string    `json:"Status"`
}

// GetOrdersResponse is for fetching multiple orders.
type GetOrdersResponse struct {
	Count *int    `json:"__count,omitempty"`
	Data  []Order `json:"Data"`
	Next  *string `json:"__next,omitempty"`
}

// PlaceOrderResponse is the typical response after placing an order.
type PlaceOrderResponse struct {
	OrderID string `json:"OrderId"`
	// Orders may contain related orders (StopLoss, TakeProfit) created.
	Orders *[]Order `json:"Orders,omitempty"`
	// Other fields like Duration, AccountKey, etc. might be present.
}

// ModifyOrderResponse is the typical response after modifying an order.
type ModifyOrderResponse struct {
    OrderID string `json:"OrderId"` // The ID of the order that was targeted for modification
    // Saxo often returns 202 Accepted with a new OrderID in Location header if modification leads to new order.
    // Or, it might return the modified order details or just a status.
    // For PATCH that results in a new order, the response body might be empty,
    // and the new OrderID is in the 'Location' header.
    // If the order is modified in place, it might return the modified order.
}

// CancelOrderResponse (typically 200 OK with modified order or just OrderID, or 204 No Content)
type CancelOrderResponse struct {
	OrderID string `json:"OrderId,omitempty"` // ID of the cancelled order
	Status  string `json:"Status,omitempty"`  // New status, e.g., "Cancelled"
}


// --- Positions (Trading context, might differ from Portfolio positions) ---
// Re-using portfolio.Position for now if structure is identical or very similar.
// If /trading/positions returns a different structure, define it here.
// For now, let's assume it's similar enough to portfolio.Position.
// type TradingPosition Position // Alias if needed

// GetTradingPositionsResponse is for fetching positions from /trading endpoints.
type GetTradingPositionsResponse struct {
	Count *int       `json:"__count,omitempty"`
	Data  []Position `json:"Data"` // Using portfolio.Position for now
	Next  *string    `json:"__next,omitempty"`
}


// --- Prices ---

// Price represents a price quote for an instrument.
type Price struct {
	AssetType      string   `json:"AssetType"`
	LastUpdated    *string  `json:"LastUpdated,omitempty"` // Timestamp string
	PriceSource    *string  `json:"PriceSource,omitempty"`
	Quote          *Quote   `json:"Quote,omitempty"` // Nested Quote info
	Uic            int      `json:"Uic"`
	// Other fields like LastTraded, High, Low, Open, etc.
	LastTraded     *float64 `json:"LastTraded,omitempty"`
	High           *float64 `json:"High,omitempty"`
	Low            *float64 `json:"Low,omitempty"`
	Open           *float64 `json:"Open,omitempty"`
	Mid            *float64 `json:"Mid,omitempty"` // Calculated from Ask/Bid
	Ask            *float64 `json:"Ask,omitempty"`
	Bid            *float64 `json:"Bid,omitempty"`
	PercentChange  *float64 `json:"PercentChange,omitempty"`
	DisplayAndFormat *Format `json:"DisplayAndFormat,omitempty"` // Re-use from instrument definitions
}

// Quote contains detailed quote information.
type Quote struct {
	Amount         *float64 `json:"Amount,omitempty"` // For Fx Forwards
	Ask            *float64 `json:"Ask,omitempty"`
	Bid            *float64 `json:"Bid,omitempty"`
	DelayedByMinutes int    `json:"DelayedByMinutes"`
	ErrorCode      *string  `json:"ErrorCode,omitempty"` // e.g. "NoPrices"
	MarketState    *string  `json:"MarketState,omitempty"` // e.g. "Open", "Closed"
	Mid            *float64 `json:"Mid,omitempty"`
	PriceTypeAsk   *string  `json:"PriceTypeAsk,omitempty"`
	PriceTypeBid   *string  `json:"PriceTypeBid,omitempty"`
	RFQState       *string  `json:"RfqState,omitempty"`
}

// GetPricesResponse is for batch price requests.
type GetPricesResponse struct {
	Data []Price `json:"Data"`
}


// --- InfoPrices ---

// InfoPrice is a light-weight price quote, often for list display.
type InfoPrice struct {
	AssetType        string      `json:"AssetType"`
	LastUpdated      *string     `json:"LastUpdated,omitempty"`
	PriceSource      *string     `json:"PriceSource,omitempty"`
	Quote            *Quote      `json:"Quote,omitempty"` // Can re-use Quote
	Uic              int         `json:"Uic"`
	DisplayAndFormat *Format     `json:"DisplayAndFormat,omitempty"`
	LastTraded       *float64    `json:"LastTraded,omitempty"`
	High             *float64    `json:"High,omitempty"`
	Low              *float64    `json:"Low,omitempty"`
	Open             *float64    `json:"Open,omitempty"`
	Mid              *float64    `json:"Mid,omitempty"`
	Ask              *float64    `json:"Ask,omitempty"`
	Bid              *float64    `json:"Bid,omitempty"`
}

// GetInfoPricesResponse is for batch InfoPrice requests.
type GetInfoPricesListResponse struct { // Python has InfoPrices, implying a list
	Data []InfoPrice `json:"Data"`
}

// --- Option Chain ---

// OptionSpace represents an option space (series of options for an underlying).
type OptionSpace struct {
	AssetType         string    `json:"AssetType"`
	ContinuousContracts []OptionContract `json:"ContinuousContracts,omitempty"`
	DefaultExpiry     string    `json:"DefaultExpiry"` // Date string
	DisplayFormat     string    `json:"DisplayFormat"`
	Expiries          []OptionExpiry `json:"Expiries"`
	IsContinues       bool      `json:"IsContinues"` // Typo in Python example? "IsContinuous"
	StrikeDecimals    int       `json:"StrikeDecimals"`
	Uic               int       `json:"Uic"`
	UnderlyingSymbol  string    `json:"UnderlyingSymbol"`
}

// OptionContract represents a specific option contract within an option space.
type OptionContract struct {
	AmountDecimals      int    `json:"AmountDecimals"`
	AssetType           string `json:"AssetType"`
	CurrencyCode        string `json:"CurrencyCode"`
	Description         string `json:"Description"`
	ExchangeID          string `json:"ExchangeId"`
	Format              Format `json:"Format"`
	GroupID             int    `json:"GroupId"`
	IncrementSize       float64 `json:"IncrementSize"`
	IsTradable          bool   `json:"IsTradable"`
	MinimumTradeSize    float64 `json:"MinimumTradeSize"`
	OrderDistances      OrderDistances `json:"OrderDistances"`
	PrimaryExchange     string `json:"PrimaryExchange"`
	SupportedOrderTypes []string `json:"SupportedOrderTypes"`
	Symbol              string `json:"Symbol"`
	TickSize            float64 `json:"TickSize"`
	TickSizeScheme      TickSizeScheme `json:"TickSizeScheme"`
	TradableAs          []string `json:"TradableAs"`
	TradableOn          []string `json:"TradableOn"`
	Uic                 int    `json:"Uic"`
	// Option specific
	ExpiryDate          string   `json:"ExpiryDate"` // Date string
	OptionType          string   `json:"OptionType"` // "Call" or "Put"
	StrikePrice         float64  `json:"StrikePrice"`
	UnderlyingAssetType string   `json:"UnderlyingAssetType"`
	UnderlyingUic       int      `json:"UnderlyingUic"`
}

// OptionExpiry represents an expiry date and related option series.
type OptionExpiry struct {
	CalculationReliability string `json:"CalculationReliability"`
	CanParticipateInMultiLegOrder bool `json:"CanParticipateInMultiLegOrder"`
	DisplayDaysToExpiry    string `json:"DisplayDaysToExpiry"`
	DistanceToMarket       float64 `json:"DistanceToMarket"`
	Expiry                 string `json:"Expiry"` // Date string
	ExpiryDate             string `json:"ExpiryDate"`
	ExpiryType             string `json:"ExpiryType"`
	OptionRootID           int    `json:"OptionRootId"`
	SpecificExpiries       []OptionContract `json:"SpecificExpiries"` // List of option contracts for this expiry
	UnderlyingCurrency     string `json:"UnderlyingCurrency"`
	UnderlyingPrice        float64 `json:"UnderlyingPrice"`
}

// GetOptionChainResponse is for fetching an option chain.
type GetOptionChainResponse struct {
	Data []OptionSpace `json:"OptionSpace"` // Saxo often nests under a specific key like this
}


// --- Allocation Keys ---
type AllocationKey struct {
    AllocationKeyID string `json:"AllocationKeyId"`
    Name            string `json:"Name"`
    // ... other fields if any
}
type GetAllocationKeysResponse struct {
    Data []AllocationKey `json:"Data"`
}

// --- Trade Messages ---
type TradeMessage struct {
    AccountKey  string `json:"AccountKey"`
    DisplayHint string `json:"DisplayHint"` // E.g., "None", "OrderConfirmation"
    ExternalReference *string `json:"ExternalReference"`
    IsRead      bool   `json:"IsRead"`
    MessageBody string `json:"MessageBody"` // HTML content
    MessageID   string `json:"MessageId"`
    MessageType string `json:"MessageType"` // E.g., "MarginCall", "CorporateAction"
    ReceivedAt  time.Time `json:"ReceivedAt"` // Or string
    Subject     string `json:"Subject"`
    // ... other fields
}
type GetTradeMessagesResponse struct {
    Data []TradeMessage `json:"Data"`
}

// --- Screener ---
// ScreenerItem represents an item from a market screener. Structure highly variable.
type ScreenerItem map[string]interface{} // Generic map for now

type GetScreenerResponse struct {
    Count int            `json:"__count"`
    Data  []ScreenerItem `json:"Data"`
}
