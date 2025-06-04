package saxo_openapi

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"pymath/go_src/database"
	"pymath/go_src/saxo_authen"
	"reflect"
	// "strings" // Removed unused import
	"testing"
	"time"
)

// --- Mock TokenManagerInterface for SaxoAuth in trading tests ---
type mockTokenManagerForTradingTests struct {
	saxo_authen.TokenManagerInterface
	StoreTokenFunc func(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error
	GetTokenFunc   func(tokenKey string) (*database.AuthTokenData, error)
}

func (m *mockTokenManagerForTradingTests) StoreToken(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error {
	if m.StoreTokenFunc != nil {
		return m.StoreTokenFunc(tokenHash, userID, encryptedPayload, expiresAt, ipAddress, userAgent, metadata)
	}
	return nil
}
func (m *mockTokenManagerForTradingTests) GetToken(tokenKey string) (*database.AuthTokenData, error) {
	if m.GetTokenFunc != nil {
		return m.GetTokenFunc(tokenKey)
	}
	return nil, fmt.Errorf("MockTokenManagerForTradingTests: GetToken called, default not found for key '%s'", tokenKey)
}

// Helper to setup client and server for trading tests
func setupTradingTest(t *testing.T, handler http.HandlerFunc) (*Client, *httptest.Server, func()) {
	server := httptest.NewServer(handler)
	tempDir := t.TempDir()
	tokenDirPath := filepath.Join(tempDir, "test_saxo_auth_data_for_trading_client")

	appCfg := saxo_authen.SaxoAppConfig{
		AppName:   "TestTradingClientApp",
		AppKey:    "testkey_tr_client",
		AppSecret: "testsecret_tr_client_must_be_32_bytes_long",
		AuthURL:   server.URL + "/auth", TokenURL: server.URL + "/token", RedirectURL: server.URL + "/redirect",
	}
	mockTokenDB := &mockTokenManagerForTradingTests{}
	testAuth, err := saxo_authen.NewSaxoAuth(appCfg, tokenDirPath, mockTokenDB, nil)
	if err != nil {
		t.Fatalf("Failed to create SaxoAuth for test setup: %v", err)
	}
	testAuth.GetTokenOverride = func() (string, error) { return "test-token-for-trading", nil }

	client, err := NewClient(testAuth, EnvironmentSimulation, 10*time.Second)
	if err != nil {
		t.Fatalf("Failed to create client: %v", err)
	}
	client.SetAPIBaseURL(server.URL)

	cleanup := func() {
		server.Close()
		// os.RemoveAll(tempDir) // Handled by t.TempDir()
	}
	return client, server, cleanup
}


// --- Tests for Order Endpoints ---

func TestPlaceOrder(t *testing.T) {
	expectedOrderID := "order123"
	orderReqPayload := map[string]interface{}{ // Example payload from a contrib_order helper
		"Uic":        123,
		"AssetType":  "Stock",
		"Amount":     100,
		"BuySell":    "Buy",
		"OrderType":  "Market",
		"AccountKey": "accKey",
		"Duration":   map[string]string{"DurationType": "DayOrder"},
		"AmountType": "Quantity",
		"ManualOrder":false,
	}
	expectedResponse := PlaceOrderResponse{OrderID: expectedOrderID}

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/trade/v1/orders" {t.Errorf("Path mismatch, got %s", r.URL.Path)}
		if r.Method != "POST" {t.Errorf("Expected POST, got %s", r.Method)}

		var receivedBody map[string]interface{}
		if err := json.NewDecoder(r.Body).Decode(&receivedBody); err != nil {
			t.Fatalf("Failed to decode request body: %v", err)
		}
		// Compare relevant fields, not all due to potential defaults added by builder
		// Numbers from JSON unmarshal into map[string]interface{} become float64
		expectedUicFloat := float64(orderReqPayload["Uic"].(int))
		if receivedUic, ok := receivedBody["Uic"].(float64); !ok || receivedUic != expectedUicFloat {
			t.Errorf("Request Uic mismatch. Got %v (type %T), Expected %v", receivedBody["Uic"], receivedBody["Uic"], expectedUicFloat)
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated) // 201 for successful placement
		json.NewEncoder(w).Encode(expectedResponse)
	})
	client, _, cleanup := setupTradingTest(t, handler)
	defer cleanup()

	response, err := client.PlaceOrder(context.Background(), orderReqPayload)
	if err != nil {
		t.Fatalf("PlaceOrder failed: %v", err)
	}
	if response.OrderID != expectedOrderID {
		t.Errorf("PlaceOrder OrderID mismatch. Got %s, Exp %s", response.OrderID, expectedOrderID)
	}
}

func TestGetOrder(t *testing.T) {
	orderID := "ord1"
	accKey := "accKey1"
	params := &GetOrderParams{AccountKey: accKey}
	expectedOrder := Order{OrderID: orderID, AccountKey: accKey, Status: "Working"}

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		expectedPath := fmt.Sprintf("/trade/v1/orders/%s", orderID)
		if r.URL.Path != expectedPath {t.Errorf("Path mismatch, got %s", r.URL.Path)}
		if r.URL.Query().Get("AccountKey") != accKey {t.Errorf("AccountKey param error")}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedOrder)
	})
	client, _, cleanup := setupTradingTest(t, handler)
	defer cleanup()

	order, err := client.GetOrder(context.Background(), orderID, params)
	if err != nil {t.Fatalf("GetOrder failed: %v", err)}
	if !reflect.DeepEqual(*order, expectedOrder) {
		t.Errorf("GetOrder response mismatch.\nGot: %+v\nExp: %+v", *order, expectedOrder)
	}
}

func TestCancelOrder(t *testing.T) {
	orderID := "orderToCancel"
	accountKey := "accKeyForCancel"
	expectedResponse := CancelOrderResponse{OrderID: orderID, Status: "Cancelled"}

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		expectedPath := fmt.Sprintf("/trade/v1/orders/%s", orderID)
		if r.URL.Path != expectedPath {t.Errorf("Path mismatch, got %s", r.URL.Path)}
		if r.Method != "DELETE" {t.Errorf("Expected DELETE method, got %s", r.Method)}
		if r.URL.Query().Get("AccountKey") != accountKey {t.Errorf("AccountKey param mismatch")}

		w.Header().Set("Content-Type", "application/json")
		// Saxo might return 200 with details or 204 No Content.
		// Let's assume 200 with details for this test.
		json.NewEncoder(w).Encode(expectedResponse)
	})
	client, _, cleanup := setupTradingTest(t, handler)
	defer cleanup()

	response, err := client.CancelOrder(context.Background(), orderID, accountKey)
	if err != nil {t.Fatalf("CancelOrder failed: %v", err)}
	if !reflect.DeepEqual(*response, expectedResponse) {
		t.Errorf("CancelOrder response mismatch.\nGot: %+v\nExp: %+v", *response, expectedResponse)
	}
}


// --- Test for Prices ---
func TestGetPrices(t *testing.T) {
	params := &GetPricesParams{
		AccountKey: "accKey",
		AssetType: "Stock",
		Uics:      "123,456",
	}
	expectedResponse := GetPricesResponse{
		Data: []Price{
			{Uic: 123, AssetType: "Stock", Ask: floatPtr(100.5)},
			{Uic: 456, AssetType: "Stock", Ask: floatPtr(200.75)},
		},
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/trade/v1/prices" {t.Errorf("Path mismatch")}
		if r.URL.Query().Get("AccountKey") != params.AccountKey {t.Error("AccountKey param mismatch")}
		if r.URL.Query().Get("AssetType") != params.AssetType {t.Error("AssetType param mismatch")}
		if r.URL.Query().Get("Uics") != params.Uics {t.Error("Uics param mismatch")}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedResponse)
	})
	client, _, cleanup := setupTradingTest(t, handler)
	defer cleanup()

	response, err := client.GetPrices(context.Background(), params)
	if err != nil {t.Fatalf("GetPrices failed: %v", err)}
	if !reflect.DeepEqual(*response, expectedResponse) {
		t.Errorf("GetPrices response mismatch.\nGot: %+v\nExp: %+v", *response, expectedResponse)
	}
}

// floatPtr is defined in client_portfolio_test.go. Assuming it's available if tests run together.
// TODO: Move helpers to a shared test_utils package.

// Add more tests for:
// - GetMultiLegOrder
// - GetOrders_deprecated
// - ModifyOrder (various scenarios, including 202/204 responses)
// - GetTradingPositions
// - GetPrice (single)
// - GetInfoPricesList
// - GetOptionChain
// - GetAllocationKeys
// - GetTradeMessages
// - GetScreenerItems
// - Error handling for all endpoints
// - Specific validation of request bodies created by contrib_orders helpers (in conjunction with PlaceOrder/ModifyOrder)
