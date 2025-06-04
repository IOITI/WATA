package saxo_openapi

import (
	"encoding/json"
	"fmt"
	"strings" // Added import
	// "time" // Removed unused import
	// "github.com/sirupsen/logrus" // If logging is needed here
)

// --- Order Type Constants (mirrors Python's OrderType class attributes) ---
const (
	OrderTypeLimit              OrderType = "Limit"
	OrderTypeMarket             OrderType = "Market"
	OrderTypeStop               OrderType = "Stop"
	OrderTypeStopLimit          OrderType = "StopLimit"
	OrderTypeTrailingStop       OrderType = "TrailingStop"
	OrderTypeTrailingStopIfTraded OrderType = "TrailingStopIfTraded"
	OrderTypeAlgo               OrderType = "Algo"
)


// --- Order Duration Helper ---
func NewOrderDuration(durationType string, expirationDate *string) OrderDuration {
	od := OrderDuration{DurationType: durationType}
	if durationType == DurationTypeGoodTillDate && expirationDate != nil {
		od.GtDDate = expirationDate
	} else if expirationDate != nil {
		od.ExpirationDateTime = expirationDate
	}
	return od
}


// --- Main Order Construction Logic ---
func OrderPayloadBuilder(orderRequest interface{}) (map[string]interface{}, error) {
	jsonData, err := json.Marshal(orderRequest)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal order request struct: %w", err)
	}

	var payload map[string]interface{}
	err = json.Unmarshal(jsonData, &payload)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal order request to map: %w", err)
	}

	if _, ok := payload["AmountType"]; !ok {
		assetType, _ := payload["AssetType"].(string)
		switch strings.ToLower(assetType) { // Used strings
		case "fxspot", "fxforward", "fxswap", "fxoption":
			payload["AmountType"] = AmountTypeQuantity
		default:
			payload["AmountType"] = AmountTypeQuantity
		}
	}

	if _, ok := payload["ManualOrder"]; !ok {
		payload["ManualOrder"] = false
	}

	if val, ok := payload["Duration"]; !ok || val == nil {
		payload["Duration"] = NewOrderDuration(DurationTypeDayOrder, nil)
	}

	if _, ok := payload["ToOpenClose"]; !ok {
		// Removed unused 'amount' variable here; logic for ToOpenClose defaulting was commented out as complex.
		// if _, amountOk := payload["Amount"].(float64); amountOk { ... }
	}

	// Field name in struct is IsGSL *bool `json:"IsGslOrder,omitempty"`
	// So after json.Marshal and Unmarshal into map, key should be "IsGslOrder".
	if isGSL, ok := payload["IsGslOrder"].(bool); ok && isGSL {
		gslPrice, priceOk := payload["GSLPrice"] // This is the temporary field from BaseOrderRequest
		if !priceOk {
			// This error implies GSLPrice was not set on the input BaseOrderRequest struct,
			// which would be a caller error if IsGslOrder is true.
			// or if GSLPrice is mandatory when IsGslOrder is true.
			// For now, assume GSLPrice would be present if IsGslOrder is true.
			// If GSLPrice is optional even if IsGslOrder is true, this needs more thought.
			// The current BaseOrderRequest makes GSLPrice a pointer, so it can be nil.
			// If GSLPrice is nil here (not found in map), we should probably error or not set GslOrderData.
			return nil, fmt.Errorf("GSLPrice must be provided in the input request map/struct if IsGslOrder is true and GSLPrice is intended")
		}
		payload["GslOrderData"] = map[string]interface{}{
			"Price": gslPrice,
		}
		delete(payload, "GSLPrice")
	} else {
		delete(payload, "GslOrderData")
		// Also remove IsGslOrder if it was explicitly set to false and we don't want to send it
		if !isGSL && ok { // If "IsGslOrder": false was in payload
			delete(payload, "IsGslOrder")
		}
	}

	if algoName, ok := payload["AlgoStrategyName"].(string); ok && algoName != "" {
		payload["OrderType"] = OrderTypeAlgo
	} else {
		delete(payload, "AlgoStrategyName")
		delete(payload, "StrategyParameters")
	}

	for k, v := range payload {
		if v == nil {
			delete(payload, k)
		}
	}

	return payload, nil
}


// --- Specific Order Helper Functions (Examples) ---
func NewLimitOrderPayload(params LimitOrderRequest) (map[string]interface{}, error) {
	params.OrderType = OrderTypeLimit
	return OrderPayloadBuilder(params)
}

func NewMarketOrderPayload(params MarketOrderRequest) (map[string]interface{}, error) {
	params.OrderType = OrderTypeMarket
	return OrderPayloadBuilder(params)
}

func NewStopOrderPayload(params StopOrderRequest) (map[string]interface{}, error) {
	params.OrderType = OrderTypeStop
	return OrderPayloadBuilder(params)
}

func NewStopLimitOrderPayload(params StopLimitOrderRequest) (map[string]interface{}, error) {
	params.OrderType = OrderTypeStopLimit
	return OrderPayloadBuilder(params)
}

func NewTrailingStopOrderPayload(params TrailingStopOrderRequest) (map[string]interface{}, error) {
	if params.OrderType == "" {
		params.OrderType = OrderTypeTrailingStopIfTraded
	}
	return OrderPayloadBuilder(params)
}


func AddStopLossToOrderRequest(primaryOrderPayload map[string]interface{}, stopLossParams OnFillOrderRequest) {
	if _, ok := primaryOrderPayload["Orders"]; !ok {
		primaryOrderPayload["Orders"] = []interface{}{}
	}
	ordersSlice, _ := primaryOrderPayload["Orders"].([]interface{})
	primaryOrderPayload["Orders"] = append(ordersSlice, stopLossParams)
}

func AddTakeProfitToOrderRequest(primaryOrderPayload map[string]interface{}, takeProfitParams OnFillOrderRequest) {
	if _, ok := primaryOrderPayload["Orders"]; !ok {
		primaryOrderPayload["Orders"] = []interface{}{}
	}
	ordersSlice, _ := primaryOrderPayload["Orders"].([]interface{})
	primaryOrderPayload["Orders"] = append(ordersSlice, takeProfitParams)
}
