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
	"strconv" // Added import
	"testing"
	"time"
)

// --- Mock TokenManagerInterface for SaxoAuth in referencedata tests ---
type mockTokenManagerForRefDataTests struct {
	saxo_authen.TokenManagerInterface
	StoreTokenFunc func(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error
	GetTokenFunc   func(tokenKey string) (*database.AuthTokenData, error)
}

func (m *mockTokenManagerForRefDataTests) StoreToken(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error {
	if m.StoreTokenFunc != nil {
		return m.StoreTokenFunc(tokenHash, userID, encryptedPayload, expiresAt, ipAddress, userAgent, metadata)
	}
	return nil
}

func (m *mockTokenManagerForRefDataTests) GetToken(tokenKey string) (*database.AuthTokenData, error) {
	if m.GetTokenFunc != nil {
		return m.GetTokenFunc(tokenKey)
	}
	return nil, fmt.Errorf("MockTokenManagerForRefDataTests: GetToken called, default not found for key '%s'", tokenKey)
}

// Helper to setup client and server for referencedata tests
func setupRefDataTest(t *testing.T, handler http.HandlerFunc) (*Client, *httptest.Server, func()) {
	server := httptest.NewServer(handler)

	tempDir := t.TempDir()
	tokenDirPath := filepath.Join(tempDir, "test_saxo_auth_data_for_refdata_client")

	appCfg := saxo_authen.SaxoAppConfig{
		AppName:   "TestRefDataClientApp",
		AppKey:    "testkey_rd_client",
		AppSecret: "testsecret_rd_client_must_be_32_bytes_long",
		AuthURL:   server.URL + "/auth",
		TokenURL:  server.URL + "/token",
		RedirectURL: server.URL + "/redirect",
	}

	mockTokenDB := &mockTokenManagerForRefDataTests{}

	testAuth, err := saxo_authen.NewSaxoAuth(appCfg, tokenDirPath, mockTokenDB, nil)
	if err != nil {
		t.Fatalf("Failed to create SaxoAuth for test setup: %v", err)
	}

	testAuth.GetTokenOverride = func() (string, error) {
		return "test-token-for-referencedata", nil
	}

	client, err := NewClient(testAuth, EnvironmentSimulation, 10*time.Second)
	if err != nil {
		t.Fatalf("Failed to create client: %v", err)
	}
	client.SetAPIBaseURL(server.URL)

	cleanup := func() {
		server.Close()
		// os.RemoveAll(tempDir) // t.TempDir() handles this
	}
	return client, server, cleanup
}


// --- Tests for Instrument Endpoints ---

func TestGetInstruments(t *testing.T) {
	assetTypes := "Stock,FxSpot"
	keywords := "Euro"
	params := &GetInstrumentsParams{
		AssetTypes: &assetTypes,
		Keywords:   &keywords,
		Top:        intPtr(10),
	}
	expectedResponse := GetInstrumentsResponse{
		Data: []InstrumentDetail{
			{Uic: 123, AssetType: "Stock", Symbol: "EURSTOCK"},
		},
		Next: stringPtr("next_page_token"),
	}

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ref/v1/instruments" {t.Errorf("Path mismatch")}
		if r.URL.Query().Get("AssetTypes") != assetTypes {t.Errorf("AssetTypes param mismatch")}
		if r.URL.Query().Get("Keywords") != keywords {t.Errorf("Keywords param mismatch")}
		if r.URL.Query().Get("$top") != "10" {t.Errorf("$top param mismatch")}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedResponse)
	})
	client, _, cleanup := setupRefDataTest(t, handler)
	defer cleanup()

	response, err := client.GetInstruments(context.Background(), params)
	if err != nil {
		t.Fatalf("GetInstruments failed: %v", err)
	}
	if !reflect.DeepEqual(*response, expectedResponse) {
		t.Errorf("GetInstruments response mismatch.\nGot: %+v\nExp: %+v", *response, expectedResponse)
	}
}

func TestGetInstrumentDetails(t *testing.T) {
	uic := 12345
	assetType := "Stock"
	params := &GetInstrumentDetailsParams{
		Uic:       uic,
		AssetType: assetType,
	}
	expectedResponse := InstrumentDetail{Uic: uic, AssetType: assetType, Symbol: "TESTSTOCK"}

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ref/v1/instruments/details" {t.Errorf("Path mismatch")}
		if r.URL.Query().Get("Uic") != strconv.Itoa(uic) {t.Errorf("Uic param mismatch")}
		if r.URL.Query().Get("AssetType") != assetType {t.Errorf("AssetType param mismatch")}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedResponse)
	})
	client, _, cleanup := setupRefDataTest(t, handler)
	defer cleanup()

	response, err := client.GetInstrumentDetails(context.Background(), params)
	if err != nil {
		t.Fatalf("GetInstrumentDetails failed: %v", err)
	}
	if !reflect.DeepEqual(*response, expectedResponse) {
		t.Errorf("GetInstrumentDetails response mismatch.\nGot: %+v\nExp: %+v", *response, expectedResponse)
	}
}


// --- Tests for Exchange Endpoints ---
func TestGetExchanges(t *testing.T) {
	marketType := "Stocks"
	params := &GetExchangesParams{MarketType: &marketType}
	expectedResponse := GetExchangesResponse{
		Data: []Exchange{
			{ExchangeID: "NYSE", Name: "New York Stock Exchange"},
		},
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ref/v1/exchanges" {t.Errorf("Path mismatch")}
		if r.URL.Query().Get("MarketType") != marketType {t.Errorf("MarketType param mismatch")}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedResponse)
	})
	client, _, cleanup := setupRefDataTest(t, handler)
	defer cleanup()

	response, err := client.GetExchanges(context.Background(), params)
	if err != nil {
		t.Fatalf("GetExchanges failed: %v", err)
	}
	if !reflect.DeepEqual(*response, expectedResponse) {
		t.Errorf("GetExchanges response mismatch.\nGot: %+v\nExp: %+v", *response, expectedResponse)
	}
}


// --- Tests for Currencies Endpoints ---
func TestGetCurrencies(t *testing.T) {
	expectedResponse := GetCurrenciesResponse{
		Data: []Currency{
			{Code: "USD", Symbol: "$", Description: "US Dollar"},
		},
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ref/v1/currencies" {t.Errorf("Path mismatch")}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedResponse)
	})
	client, _, cleanup := setupRefDataTest(t, handler)
	defer cleanup()

	response, err := client.GetCurrencies(context.Background())
	if err != nil {
		t.Fatalf("GetCurrencies failed: %v", err)
	}
	if !reflect.DeepEqual(*response, expectedResponse) {
		t.Errorf("GetCurrencies response mismatch.\nGot: %+v\nExp: %+v", *response, expectedResponse)
	}
}

// Helper functions (intPtr, stringPtr, etc.) are now in test_utils_test.go
// stringPtr is defined in client_portfolio_test.go, assuming it's available or tests are run together. // This comment is now outdated
// If running this file standalone, it would need its own or a shared test utility. // This comment is now outdated
// For now, remove to avoid redeclaration if tests are run with ./...
// func stringPtr(s string) *string { return &s }
// func boolPtr(b bool) *bool { return &b }

// Add more tests for:
// - GetInstrumentDetailsByUicAssetType
// - GetExchange (single by MIC)
// - GetCurrencyPairs
// - GetCultures
// - GetLanguages
// - GetTimezones
// - GetStandardDates
// - GetAlgoStrategies
// - GetCountries
// - GetCountry (single by code)
// - Error cases for all endpoints (400, 401, 404, etc.)
// - Pagination parameters ($top, $skip, $inlinecount) where applicable
// - Different combinations of query parameters.
// - Correct handling of optional parameters (omitempty behavior).
