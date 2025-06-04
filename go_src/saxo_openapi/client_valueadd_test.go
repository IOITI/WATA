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

// --- Mock TokenManagerInterface for SaxoAuth in valueadd tests ---
type mockTokenManagerForValueAddTests struct {
	saxo_authen.TokenManagerInterface
	StoreTokenFunc func(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error
	GetTokenFunc   func(tokenKey string) (*database.AuthTokenData, error)
}

func (m *mockTokenManagerForValueAddTests) StoreToken(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error {
	if m.StoreTokenFunc != nil {
		return m.StoreTokenFunc(tokenHash, userID, encryptedPayload, expiresAt, ipAddress, userAgent, metadata)
	}
	return nil
}
func (m *mockTokenManagerForValueAddTests) GetToken(tokenKey string) (*database.AuthTokenData, error) {
	if m.GetTokenFunc != nil {
		return m.GetTokenFunc(tokenKey)
	}
	return nil, fmt.Errorf("MockTokenManagerForValueAddTests: GetToken called, default not found for key '%s'", tokenKey)
}

// Helper to setup client and server for valueadd tests
func setupValueAddTest(t *testing.T, handler http.HandlerFunc) (*Client, *httptest.Server, func()) {
	server := httptest.NewServer(handler)
	tempDir := t.TempDir()
	tokenDirPath := filepath.Join(tempDir, "test_saxo_auth_data_for_vas_client")

	appCfg := saxo_authen.SaxoAppConfig{
		AppName:   "TestVASClientApp",
		AppKey:    "testkey_vas_client",
		AppSecret: "testsecret_vas_client_must_be_32_bytes_long",
		AuthURL:   server.URL + "/auth", TokenURL: server.URL + "/token", RedirectURL: server.URL + "/redirect",
	}
	mockTokenDB := &mockTokenManagerForValueAddTests{}
	testAuth, err := saxo_authen.NewSaxoAuth(appCfg, tokenDirPath, mockTokenDB, nil)
	if err != nil {
		t.Fatalf("Failed to create SaxoAuth for test setup: %v", err)
	}
	testAuth.GetTokenOverride = func() (string, error) { return "test-token-for-valueadd", nil }

	client, err := NewClient(testAuth, EnvironmentSimulation, 10*time.Second)
	if err != nil {
		t.Fatalf("Failed to create client: %v", err)
	}
	client.SetAPIBaseURL(server.URL)

	cleanup := func() {
		server.Close()
	}
	return client, server, cleanup
}


// --- Price Alerts ---
func TestGetPriceAlerts(t *testing.T) {
	accountKey := "accKey123"
	params := &GetPriceAlertsParams{AccountKey: accountKey}
	expectedResponse := GetPriceAlertsResponse{
		Data: []PriceAlert{
			{AlertDefinitionID: stringPtr("alert1"), AccountKey: accountKey, AssetType: "Stock", Uic: 123, Price: 100.0},
		},
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/vas/v1/pricealerts" {t.Errorf("Path mismatch")}
		if r.URL.Query().Get("AccountKey") != accountKey {t.Errorf("AccountKey param mismatch")}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedResponse)
	})
	client, _, cleanup := setupValueAddTest(t, handler)
	defer cleanup()

	response, err := client.GetPriceAlerts(context.Background(), params)
	if err != nil {t.Fatalf("GetPriceAlerts failed: %v", err)}
	if !reflect.DeepEqual(*response, expectedResponse) {
		t.Errorf("GetPriceAlerts response mismatch.\nGot: %+v\nExp: %+v", *response, expectedResponse)
	}
}

func TestCreatePriceAlert(t *testing.T) {
	alertReq := CreatePriceAlertRequest{
		AccountKey: "accKey1", AssetType: "FxSpot", Uic: 7, Operator: "LessThanOrEqual", Price: 1.1000,
		ExpiryDateTime: time.Now().Add(24 * time.Hour).Format("2006-01-02T15:04:05Z"), PriceTypeToWatch: "Bid",
	}
	expectedAlertID := "newAlertID789"
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/vas/v1/pricealerts" {t.Errorf("Path mismatch")}
		if r.Method != "POST" {t.Errorf("Expected POST method")}
		var receivedBody CreatePriceAlertRequest
		if err := json.NewDecoder(r.Body).Decode(&receivedBody); err != nil {t.Fatalf("Decode error: %v", err)}
		// Compare relevant fields. ExpiryDateTime string format might vary slightly if not careful.
		if receivedBody.Uic != alertReq.Uic || receivedBody.Price != alertReq.Price {
			t.Errorf("Request body mismatch. Got %+v, Expected relevant parts of %+v", receivedBody, alertReq)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(CreatePriceAlertResponse{AlertDefinitionID: expectedAlertID})
	})
	client, _, cleanup := setupValueAddTest(t, handler)
	defer cleanup()

	response, err := client.CreatePriceAlert(context.Background(), alertReq)
	if err != nil {t.Fatalf("CreatePriceAlert failed: %v", err)}
	if response.AlertDefinitionID != expectedAlertID {
		t.Errorf("Expected AlertDefinitionID %s, got %s", expectedAlertID, response.AlertDefinitionID)
	}
}

func TestDeletePriceAlert(t *testing.T) {
	alertID := "alertToDelete"
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		expectedPath := fmt.Sprintf("/vas/v1/pricealerts/%s", alertID)
		if r.URL.Path != expectedPath {t.Errorf("Path mismatch: got %s, exp %s", r.URL.Path, expectedPath)}
		if r.Method != "DELETE" {t.Errorf("Expected DELETE method")}
		w.WriteHeader(http.StatusNoContent)
	})
	client, _, cleanup := setupValueAddTest(t, handler)
	defer cleanup()

	err := client.DeletePriceAlert(context.Background(), alertID)
	if err != nil {t.Fatalf("DeletePriceAlert failed: %v", err)}
}

// --- User Settings (VAS) ---
func TestGetPriceAlertUserSettings(t *testing.T) {
	expectedSettings := PriceAlertsUserSettings{PriceAlertsEnabled: true}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/vas/v1/users/me/pricealertusersettings" {t.Errorf("Path mismatch")}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedSettings)
	})
	client, _, cleanup := setupValueAddTest(t, handler)
	defer cleanup()

	settings, err := client.GetPriceAlertUserSettings(context.Background())
	if err != nil {t.Fatalf("GetPriceAlertUserSettings failed: %v", err)}
	if !reflect.DeepEqual(*settings, expectedSettings) {
		t.Errorf("Response mismatch. Got %+v, Exp %+v", *settings, expectedSettings)
	}
}

func TestUpdatePriceAlertUserSettings(t *testing.T) {
	settingsUpdate := PriceAlertsUserSettings{PriceAlertsEnabled: false}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/vas/v1/users/me/pricealertusersettings" {t.Errorf("Path mismatch")}
		if r.Method != "PUT" {t.Errorf("Expected PUT method")}
		var receivedBody PriceAlertsUserSettings
		if err := json.NewDecoder(r.Body).Decode(&receivedBody); err != nil {t.Fatalf("Decode error: %v", err)}
		if !reflect.DeepEqual(receivedBody, settingsUpdate) {t.Errorf("Request body mismatch")}
		w.WriteHeader(http.StatusNoContent)
	})
	client, _, cleanup := setupValueAddTest(t, handler)
	defer cleanup()

	err := client.UpdatePriceAlertUserSettings(context.Background(), settingsUpdate)
	if err != nil {t.Fatalf("UpdatePriceAlertUserSettings failed: %v", err)}
}


// Helper for string pointers in tests (if not using a shared test util package)
// func stringPtr(s string) *string { return &s }
// func intPtr(i int) *int       { return &i }
// func boolPtr(b bool) *bool    { return &b }
// These are defined in other _test.go files.
// For this file, only stringPtr was used in definitions_valueadd for PriceAlert.AlertDefinitionID
// and in definitions_chart for ChartInfo.ExchangeID.
// If those structs are used directly in tests, they might need these helpers.
// The current tests here don't directly construct structs that use these pointer helpers.
// Added one for PriceAlert.AlertDefinitionID in definitions_valueadd.go
func stringPtrHelper(s string) *string { return &s } // Renamed to avoid conflict
