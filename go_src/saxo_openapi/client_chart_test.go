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
	"strconv"
	"strings" // Added import
	"testing"
	"time"
)

// --- Mock TokenManagerInterface for SaxoAuth in chart tests ---
type mockTokenManagerForChartTests struct {
	saxo_authen.TokenManagerInterface
	StoreTokenFunc func(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error
	GetTokenFunc   func(tokenKey string) (*database.AuthTokenData, error)
}

func (m *mockTokenManagerForChartTests) StoreToken(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error {
	if m.StoreTokenFunc != nil {
		return m.StoreTokenFunc(tokenHash, userID, encryptedPayload, expiresAt, ipAddress, userAgent, metadata)
	}
	return nil
}
func (m *mockTokenManagerForChartTests) GetToken(tokenKey string) (*database.AuthTokenData, error) {
	if m.GetTokenFunc != nil {
		return m.GetTokenFunc(tokenKey)
	}
	return nil, fmt.Errorf("MockTokenManagerForChartTests: GetToken called, default not found for key '%s'", tokenKey)
}

// Helper to setup client and server for chart tests
func setupChartTest(t *testing.T, handler http.HandlerFunc) (*Client, *httptest.Server, func()) {
	server := httptest.NewServer(handler)
	tempDir := t.TempDir()
	tokenDirPath := filepath.Join(tempDir, "test_saxo_auth_data_for_chart_client")

	appCfg := saxo_authen.SaxoAppConfig{
		AppName:   "TestChartClientApp",
		AppKey:    "testkey_chart_client",
		AppSecret: "testsecret_chart_client_must_be_32_bytes_long",
		AuthURL:   server.URL + "/auth", TokenURL: server.URL + "/token", RedirectURL: server.URL + "/redirect",
	}
	mockTokenDB := &mockTokenManagerForChartTests{}
	testAuth, err := saxo_authen.NewSaxoAuth(appCfg, tokenDirPath, mockTokenDB, nil)
	if err != nil {
		t.Fatalf("Failed to create SaxoAuth for test setup: %v", err)
	}
	testAuth.GetTokenOverride = func() (string, error) { return "test-token-for-chart", nil }

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

func TestGetChartData(t *testing.T) {
	now := time.Now().UTC()
	timeParam := now.Add(-time.Hour)          // For Mode="UpTo"
	startTimeParam := now.Add(-24 * time.Hour) // For Mode="FromTo"
	endTimeParam := now                       // For Mode="FromTo"

	paramsUpTo := &GetChartDataParams{
		AssetType: "Stock",
		Uic:       211, // AAPL.O
		Horizon:   100,
		Mode:      "UpTo",
		Time:      &timeParam,
	}
	paramsFromTo := &GetChartDataParams{
		AssetType: "Stock",
		Uic:       211,
		Horizon:   50, // Horizon might be ignored if Start/EndTime are used for count
		Mode:      "FromTo",
		StartTime: &startTimeParam,
		EndTime:   &endTimeParam,
	}

	expectedResponse := ChartData{
		Data: [][]interface{}{
			{float64(timeParam.Add(-2*time.Minute).UnixMilli()), 150.0, 151.0, 149.0, 150.5, 1000.0},
			{float64(timeParam.Add(-1*time.Minute).UnixMilli()), 150.5, 152.0, 150.0, 151.5, 1200.0},
		},
		ChartInfo: &ChartInfo{ExchangeID: stringPtr("NASDAQ")},
	}

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/chart/v1/charts" {t.Errorf("Path mismatch, got %s", r.URL.Path)}

		query := r.URL.Query()
		if query.Get("AssetType") != "Stock" {t.Error("AssetType param mismatch")}
		if query.Get("Uic") != "211" {t.Error("Uic param mismatch")}

		mode := query.Get("Mode")
		if mode == "UpTo" {
			if query.Get("Horizon") != "100" {t.Error("Horizon param mismatch for UpTo")}
			if query.Get("Time") == "" {t.Error("Time param missing for UpTo")}
			// Further validation of Time format if needed
		} else if mode == "FromTo" {
			// Horizon might be present or not, depending on API behavior with FromTo
			// if query.Get("Horizon") != "50" {t.Error("Horizon param mismatch for FromTo")}
			if query.Get("StartTime") == "" {t.Error("StartTime param missing for FromTo")}
			if query.Get("EndTime") == "" {t.Error("EndTime param missing for FromTo")}
		} else {
			t.Errorf("Unexpected Mode: %s", mode)
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedResponse)
	})

	client, _, cleanup := setupChartTest(t, handler)
	defer cleanup()

	t.Run("ModeUpTo", func(t *testing.T) {
		response, err := client.GetChartData(context.Background(), paramsUpTo)
		if err != nil {
			t.Fatalf("GetChartData (UpTo) failed: %v", err)
		}
		if !reflect.DeepEqual(response.ChartInfo, expectedResponse.ChartInfo) { // Compare parts due to complex Data
			t.Errorf("GetChartData (UpTo) ChartInfo mismatch.\nGot: %+v\nExp: %+v", response.ChartInfo, expectedResponse.ChartInfo)
		}
		if len(response.Data) != len(expectedResponse.Data) {
			t.Errorf("GetChartData (UpTo) Data length mismatch. Got %d, Exp %d", len(response.Data), len(expectedResponse.Data))
		}
	})

	t.Run("ModeFromTo", func(t *testing.T) {
		response, err := client.GetChartData(context.Background(), paramsFromTo)
		if err != nil {
			t.Fatalf("GetChartData (FromTo) failed: %v", err)
		}
		if !reflect.DeepEqual(response.ChartInfo, expectedResponse.ChartInfo) {
			t.Errorf("GetChartData (FromTo) ChartInfo mismatch.\nGot: %+v\nExp: %+v", response.ChartInfo, expectedResponse.ChartInfo)
		}
	})

	t.Run("MissingRequiredParams", func(t *testing.T) {
		_, err := client.GetChartData(context.Background(), &GetChartDataParams{AssetType: "Stock"}) // Missing Uic, Horizon, Mode
		if err == nil {t.Error("Expected error for missing params")}
		if !strings.Contains(err.Error(), "Uic, AssetType, Horizon, and Mode are required") {
			t.Errorf("Unexpected error message for missing params: %v", err)
		}
	})
}

// Helper functions (stringPtr, intPtr, etc.) are now in test_utils_test.go

// strconv is used by paramsToQueryValuesTime
var _ = strconv.Itoa // Check if strconv is imported
