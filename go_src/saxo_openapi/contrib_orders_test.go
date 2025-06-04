package saxo_openapi

import (
	"encoding/json"
	"testing"
	// "reflect"
	// "time"
	// "github.com/stretchr/testify/assert"
)

func TestNewOrderDuration(t *testing.T) {
	t.Run("DayOrder", func(t *testing.T) {
		od := NewOrderDuration(DurationTypeDayOrder, nil)
		if od.DurationType != DurationTypeDayOrder {
			t.Errorf("Expected DurationType %s, got %s", DurationTypeDayOrder, od.DurationType)
		}
		if od.GtDDate != nil || od.ExpirationDateTime != nil {
			t.Error("Expected no expiration for DayOrder")
		}
	})
	t.Run("GoodTillDate", func(t *testing.T) {
		dateStr := "2024-12-31"
		od := NewOrderDuration(DurationTypeGoodTillDate, &dateStr)
		if od.DurationType != DurationTypeGoodTillDate {
			t.Errorf("Expected DurationType %s, got %s", DurationTypeGoodTillDate, od.DurationType)
		}
		if od.GtDDate == nil || *od.GtDDate != dateStr {
			t.Errorf("Expected GtDDate %s, got %v", dateStr, od.GtDDate)
		}
	})
}

func TestOrderPayloadBuilder_Defaults(t *testing.T) {
	baseReq := BaseOrderRequest{
		Uic:       123,
		AssetType: "Stock",
		Amount:    100,
		BuySell:   DirectionBuy,
		AccountKey: "accKey",
	}

	t.Run("DefaultAmountTypeAndManualOrder", func(t *testing.T) {
		payload, err := OrderPayloadBuilder(baseReq)
		if err != nil {
			t.Fatalf("OrderPayloadBuilder failed: %v", err)
		}
		if payload["AmountType"] != AmountTypeQuantity {
			t.Errorf("Expected AmountType %s, got %s", AmountTypeQuantity, payload["AmountType"])
		}
		if manualOrder, ok := payload["ManualOrder"].(bool); !ok || manualOrder != false {
			t.Errorf("Expected ManualOrder false, got %v", payload["ManualOrder"])
		}
		// Corrected Assertion: Expect OrderDuration struct
        if durVal, ok := payload["Duration"]; !ok {
            t.Error("Expected default Duration to be set")
        } else if durStruct, isDur := durVal.(OrderDuration); !isDur {
             t.Errorf("Default Duration is not an OrderDuration struct, got %T. Value: %+v", durVal, durVal)
        } else if durStruct.DurationType != DurationTypeDayOrder {
            t.Errorf("Expected default DurationType to be DayOrder, got %s", durStruct.DurationType)
        }
	})

	t.Run("FxSpotAmountType", func(t *testing.T) {
		fxReq := baseReq
		fxReq.AssetType = "FxSpot"
		payload, _ := OrderPayloadBuilder(fxReq)
		if payload["AmountType"] != AmountTypeQuantity {
			t.Errorf("Expected AmountType for FxSpot to be %s, got %s", AmountTypeQuantity, payload["AmountType"])
		}
	})
}

// boolPtrLocal removed, use boolPtr from test_utils_test.go

func TestOrderPayloadBuilder_GSL(t *testing.T) {
	gslPrice := 100.50
	reqWithGSL := BaseOrderRequest{
		IsGSL:    boolPtr(true), // Use shared helper
		GSLPrice: &gslPrice,
		Uic: 1, AssetType: "CfdOnStock", Amount: 10, BuySell: DirectionBuy, AccountKey: "key",
	}
	payload, err := OrderPayloadBuilder(reqWithGSL)
	if err != nil {
		t.Fatalf("OrderPayloadBuilder with GSL failed: %v", err)
	}

	if gslData, ok := payload["GslOrderData"].(map[string]interface{}); !ok {
		t.Error("GslOrderData not found in payload")
	} else {
		if price, priceOk := gslData["Price"].(float64); !priceOk || price != gslPrice {
			t.Errorf("GslOrderData.Price incorrect. Expected %f, got %v", gslPrice, gslData["Price"])
		}
	}
	if _, ok := payload["GSLPrice"]; ok {
		t.Error("Temporary GSLPrice field should be removed from payload")
	}

	reqNoGSL := BaseOrderRequest{IsGSL: boolPtr(false)} // Use shared helper
	payloadNoGSL, _ := OrderPayloadBuilder(reqNoGSL)
	if _, ok := payloadNoGSL["GslOrderData"]; ok {
		t.Error("GslOrderData should not be present if IsGSL is false")
	}
}

func TestOrderPayloadBuilder_AlgoOrder(t *testing.T) {
	strategyName := "MyAlgo"
	params := []AlgoParameterRequest{{ID: "param1", Value: 123.45}}
	reqAlgo := BaseOrderRequest{
		AlgoStrategyName: &strategyName,
		StrategyParameters: &params,
		Uic: 1, AssetType: "Stock", Amount: 10, BuySell: DirectionBuy, AccountKey: "key",
	}
	payload, err := OrderPayloadBuilder(reqAlgo)
	if err != nil {
		t.Fatalf("OrderPayloadBuilder with Algo failed: %v", err)
	}
	// Corrected Assertion: Expect OrderType directly from the map
	if ot, ok := payload["OrderType"].(OrderType); !ok || ot != OrderTypeAlgo {
		t.Errorf("Expected OrderType %s for Algo order, got %v (type %T)", OrderTypeAlgo, payload["OrderType"], payload["OrderType"])
	}
	if stratName, ok := payload["AlgoStrategyName"].(string); !ok || stratName != strategyName {
		t.Error("AlgoStrategyName mismatch in payload")
	}
}


func TestSpecificOrderHelpers(t *testing.T) {
	orderDurationGTC := NewOrderDuration(DurationTypeGoodTillCancel, nil)
	baseParams := BaseOrderRequest{
		Uic:       123,
		AssetType: "Stock",
		Amount:    10,
		BuySell:   DirectionBuy,
		AccountKey: "accKey1",
		Duration: &orderDurationGTC,
	}

	t.Run("LimitOrder", func(t *testing.T) {
		limitParams := LimitOrderRequest{
			BaseOrderRequest: baseParams,
			OrderPrice:       150.75,
		}
		payload, err := NewLimitOrderPayload(limitParams)
		if err != nil {t.Fatalf("NewLimitOrderPayload failed: %v", err)}

		if ot, ok := payload["OrderType"].(string); !ok || OrderType(ot) != OrderTypeLimit {
			t.Errorf("OrderType mismatch. Expected %s, Got %v", OrderTypeLimit, payload["OrderType"])
		}
		if price, ok := payload["OrderPrice"].(float64); !ok || price != 150.75 {
            t.Errorf("OrderPrice mismatch. Expected 150.75, Got %v", payload["OrderPrice"])
        }

        if durVal, ok := payload["Duration"].(map[string]interface{}); !ok {
             t.Errorf("Duration is not a map, got %T", payload["Duration"])
        } else if durType, _ := durVal["DurationType"].(string); durType != DurationTypeGoodTillCancel {
            t.Errorf("DurationType mismatch. Expected %s, Got %s", DurationTypeGoodTillCancel, durType)
        }
	})

	t.Run("MarketOrder", func(t *testing.T) {
		marketParams := MarketOrderRequest{BaseOrderRequest: baseParams}
		payload, err := NewMarketOrderPayload(marketParams)
		if err != nil {t.Fatalf("NewMarketOrderPayload failed: %v", err)}
		if ot, ok := payload["OrderType"].(string); !ok || OrderType(ot) != OrderTypeMarket {
			t.Errorf("OrderType mismatch. Expected %s, Got %v", OrderTypeMarket, payload["OrderType"])
		}
	})

	t.Run("StopOrder", func(t *testing.T) {
		stopParams := StopOrderRequest{
			BaseOrderRequest: baseParams,
			OrderPrice:       140.00,
		}
		payload, err := NewStopOrderPayload(stopParams)
		if err != nil {t.Fatalf("NewStopOrderPayload failed: %v", err)}
		if ot, ok := payload["OrderType"].(string); !ok || OrderType(ot) != OrderTypeStop {
			t.Errorf("OrderType mismatch. Expected %s, Got %v", OrderTypeStop, payload["OrderType"])
		}
		if price, ok := payload["OrderPrice"].(float64); !ok || price != 140.00 {
            t.Errorf("Stop OrderPrice (trigger) mismatch. Expected 140.00, Got %v", payload["OrderPrice"])
        }
	})
}

func TestAddStopLossAndTakeProfitToOrderRequest(t *testing.T) {
	primaryOrderBase := BaseOrderRequest{
		Uic:        789,
		AssetType:  "FxSpot",
		Amount:     10000,
		BuySell:    DirectionSell,
		AccountKey: "fxAccount",
	}
	primaryPayload, _ := OrderPayloadBuilder(MarketOrderRequest{BaseOrderRequest: primaryOrderBase})

	stopLossPrice := 1.1050
	slOrderParams := NewStopLossOrderRequest(primaryOrderBase, stopLossPrice)
	AddStopLossToOrderRequest(primaryPayload, slOrderParams)

	takeProfitPrice := 1.0900
	tpOrderParams := NewTakeProfitOrderRequest(primaryOrderBase, takeProfitPrice)
	AddTakeProfitToOrderRequest(primaryPayload, tpOrderParams)

	ordersField, ok := primaryPayload["Orders"]
	if !ok {t.Fatal("Orders field missing from primary payload")}

	relatedOrders, ok := ordersField.([]interface{})
	if !ok {t.Fatalf("Orders field is not []interface{}, but %T", ordersField)}
	if len(relatedOrders) != 2 {t.Fatalf("Expected 2 related orders, got %d", len(relatedOrders))}

	var slOrderMap OnFillOrderRequest
	slBytes, _ := json.Marshal(relatedOrders[0])
	json.Unmarshal(slBytes, &slOrderMap)

	if slOrderMap.OrderType != OrderType("Stop") {t.Error("StopLoss order type mismatch")}
	if slOrderMap.BuySell != DirectionBuy {t.Error("StopLoss direction should be Buy")}
	if slOrderMap.OrderPrice != stopLossPrice {t.Error("StopLoss price mismatch")}

	var tpOrderMap OnFillOrderRequest
	tpBytes, _ := json.Marshal(relatedOrders[1])
	json.Unmarshal(tpBytes, &tpOrderMap)

	if tpOrderMap.OrderType != OrderType("Limit") {t.Error("TakeProfit order type mismatch")}
	if tpOrderMap.BuySell != DirectionBuy {t.Error("TakeProfit direction should be Buy")}
	if tpOrderMap.OrderPrice != takeProfitPrice {t.Error("TakeProfit price mismatch")}
}
