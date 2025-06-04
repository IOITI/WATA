package saxo_openapi

// import "time" // No longer needed after OrderDuration field changes in BaseOrderRequest

// --- Order Duration Types ---
// (Already defined in definitions_trading.go as OrderDuration struct,
// but specific DurationType constants can be here if preferred for clarity)
const (
	DurationTypeDayOrder      = "DayOrder"
	DurationTypeGoodTillDate  = "GoodTillDate"
	DurationTypeGoodTillCancel= "GoodTillCancel"
	// ... other duration types like "ImmediateOrCancel", "FillOrKill"
)

// --- Order Relation Types (for conditional orders) ---
const (
	OrderRelationIfDoneMaster = "IfDoneMaster" // IfDone Master order (primary order in an If-Done setup)
	OrderRelationIfDoneSlave  = "IfDoneSlave"  // IfDone Slave order (e.g. StopLoss/TakeProfit for the Master)
	OrderRelationOco          = "Oco"          // One-Cancels-the-Other
)


// --- Base Order Parameters ---

// BaseOrderRequest is embedded in specific order types.
// It mirrors fields from saxo_openapi.contrib.orders.baseorder.BaseOrder
type BaseOrderRequest struct {
	Uic         int             `json:"Uic"`
	AssetType   string          `json:"AssetType"`
	Amount      float64         `json:"Amount"`
	BuySell     OrderDirection  `json:"BuySell"` // "Buy" or "Sell"
	OrderType   OrderType       `json:"OrderType"` // "Limit", "Market", etc.
	AmountType  OrderAmountType `json:"AmountType,omitempty"` // Defaults based on AssetType often
	AccountKey  string          `json:"AccountKey"`           // Required
	AccountID   string          `json:"AccountId,omitempty"`  // Sometimes available, not always sent
	ClientKey   string          `json:"ClientKey,omitempty"`  // Usually not sent, derived from token

	// OrderDuration handling (matches OrderDuration struct in definitions_trading.go)
	Duration          *OrderDuration `json:"Duration,omitempty"`

	// External Reference and other common fields
	ExternalReference string `json:"ExternalReference,omitempty"`
	ManualOrder       *bool  `json:"ManualOrder,omitempty"` // Typically false or omitted for API orders

	// Conditional Order links (StopLoss, TakeProfit)
	Orders *[]interface{} `json:"Orders,omitempty"` // Slice of related order requests (e.g. StopOrderRequest)

	// For specific strategies or conditions
	ToOpenClose        *ToOpenClose `json:"ToOpenClose,omitempty"` // "ToOpen" or "ToClose"
	RelatedPositionID  *string      `json:"RelatedPositionId,omitempty"` // For closing specific position
	ForwardDate        *string      `json:"ForwardDate,omitempty"` // For FxForwards, format YYYY-MM-DD
	ExpiryDate         *string      `json:"ExpiryDate,omitempty"`  // For Futures/Options, format YYYY-MM-DD
	MarketID           *string      `json:"MarketId,omitempty"`    // ExchangeId for some asset types

	// GSL - Guaranteed Stop Loss
	IsGSL              *bool    `json:"IsGslOrder,omitempty"` // Corrected JSON tag to match usage
	GSLPrice           *float64 `json:"GSLPrice,omitempty"` // Custom field for builder, removed before final JSON.
	                                                       // Actual GSL price is part of GslOrderData in payload.

	// Algo Order parameters
	AlgoStrategyName *string                  `json:"AlgoStrategyName,omitempty"`
	StrategyParameters *[]AlgoParameterRequest `json:"StrategyParameters,omitempty"`
}

// AlgoParameterRequest defines a parameter for an Algo order.
type AlgoParameterRequest struct {
	ID    string      `json:"Id"`
	Value interface{} `json:"Value"`
}


// --- Specific Order Type Parameters ---

type LimitOrderRequest struct {
	BaseOrderRequest
	OrderPrice float64 `json:"OrderPrice"`
}

type MarketOrderRequest struct {
	BaseOrderRequest
}

type StopOrderRequest struct {
	BaseOrderRequest
	OrderPrice float64 `json:"OrderPrice"`
}

type StopLimitOrderRequest struct {
	BaseOrderRequest
	OrderPrice     float64 `json:"OrderPrice"`
	StopLimitPrice float64 `json:"StopLimitPrice"`
}

type TrailingStopOrderRequest struct {
	BaseOrderRequest
	TrailingStopDistanceToMarket *float64 `json:"TrailingStopDistanceToMarket,omitempty"`
	TrailingStopPegOffsetPct     *float64 `json:"TrailingStopPegOffsetPct,omitempty"`
	TrailingStopStepPct          *float64 `json:"TrailingStopStepPct,omitempty"`
}


// --- OnFill / Related Order Parameters ---
type OnFillOrderRequest struct {
	Uic              int            `json:"Uic"`
	AssetType        string         `json:"AssetType"`
	Amount           float64        `json:"Amount"`
	BuySell          OrderDirection `json:"BuySell"`
	OrderType        OrderType      `json:"OrderType"`
	OrderPrice       float64        `json:"OrderPrice"`
	Duration         OrderDuration  `json:"Duration"`
	AccountKey       string         `json:"AccountKey"`
	OrderRelation    string         `json:"OrderRelation"`
	ToOpenClose      ToOpenClose    `json:"ToOpenClose"`
	ManualOrder      bool           `json:"ManualOrder"`
}

func NewStopLossOrderRequest(primaryOrder BaseOrderRequest, price float64) OnFillOrderRequest {
	return OnFillOrderRequest{
		Uic:           primaryOrder.Uic,
		AssetType:     primaryOrder.AssetType,
		Amount:        primaryOrder.Amount,
		BuySell:       oppositeDirection(primaryOrder.BuySell),
		OrderType:     OrderType("Stop"),
		OrderPrice:    price,
		Duration:      OrderDuration{DurationType: DurationTypeGoodTillCancel},
		AccountKey:    primaryOrder.AccountKey,
		OrderRelation: OrderRelationIfDoneSlave,
		ToOpenClose:   ToClose,
		ManualOrder:   false,
	}
}

func NewTakeProfitOrderRequest(primaryOrder BaseOrderRequest, price float64) OnFillOrderRequest {
	return OnFillOrderRequest{
		Uic:           primaryOrder.Uic,
		AssetType:     primaryOrder.AssetType,
		Amount:        primaryOrder.Amount,
		BuySell:       oppositeDirection(primaryOrder.BuySell),
		OrderType:     OrderType("Limit"),
		OrderPrice:    price,
		Duration:      OrderDuration{DurationType: DurationTypeGoodTillCancel},
		AccountKey:    primaryOrder.AccountKey,
		OrderRelation: OrderRelationIfDoneSlave,
		ToOpenClose:   ToClose,
		ManualOrder:   false,
	}
}

func oppositeDirection(dir OrderDirection) OrderDirection {
	if dir == DirectionBuy {
		return DirectionSell
	}
	return DirectionBuy
}
