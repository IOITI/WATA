package saxo_openapi

import (
	"context"
	// "fmt" // Removed unused import
	"net/http"
	"net/http/httptest"
	"encoding/json" // Added import
	"path/filepath" // For test setup token dir path
	"pymath/go_src/database" // For mock TokenManagerInterface if needed by SaxoAuth mock
	"pymath/go_src/saxo_authen" // For saxo_authen.SaxoAuth type for mock setup
	"strings" // Added import
	"testing"
	"time"
	// "github.com/stretchr/testify/assert"
)


// Helper to setup client and server for util tests that might need a client
// This is similar to setupRefDataTest but specific for util tests if needed.
func setupUtilTestClient(t *testing.T, handler http.HandlerFunc) (*Client, func()) {
	server := httptest.NewServer(handler)

	tempDir := t.TempDir()
	tokenDirPath := filepath.Join(tempDir, "test_saxo_auth_data_for_util_client")

	appCfg := saxo_authen.SaxoAppConfig{
		AppName:   "TestUtilClientApp",
		AppKey:    "testkey_util_client",
		AppSecret: "testsecret_util_client_must_be_32_bytes_long",
		AuthURL:   server.URL + "/auth",
		TokenURL:  server.URL + "/token",
		RedirectURL: server.URL + "/redirect",
	}

	// Use a mock TokenManagerInterface implementation
	// We can reuse one from another test file if it's made common, or define locally.
	// For simplicity, let's assume a basic mock satisfying the interface.
	type mockTokenManagerForUtil struct {
		saxo_authen.TokenManagerInterface
		GetTokenFunc func(tokenKey string) (*database.AuthTokenData, error)
	}
	fnGetToken := func(tokenKey string) (*database.AuthTokenData, error) {
		return &database.AuthTokenData{EncryptedData: []byte("dummy-data")}, nil
	}

	mockTokenDB := &mockTokenManagerForUtil{ GetTokenFunc: fnGetToken }


	testAuth, err := saxo_authen.NewSaxoAuth(appCfg, tokenDirPath, mockTokenDB, nil)
	if err != nil {
		t.Fatalf("Failed to create SaxoAuth for test setup: %v", err)
	}

	testAuth.GetTokenOverride = func() (string, error) {
		return "test-token-for-util", nil
	}

	client, err := NewClient(testAuth, EnvironmentSimulation, 10*time.Second)
	if err != nil {
		t.Fatalf("Failed to create client: %v", err)
	}
	client.SetAPIBaseURL(server.URL)

	cleanup := func() {
		server.Close()
	}
	return client, cleanup
}


func TestInstrumentToUic(t *testing.T) {
	ctx := context.Background()

	t.Run("InputIsIntUIC", func(t *testing.T) {
		uic, assetType, err := InstrumentToUic(ctx, nil, 123, nil) // No client needed
		if err != nil {
			t.Errorf("Unexpected error for int input: %v", err)
		}
		if uic != 123 {
			t.Errorf("Expected UIC 123, got %d", uic)
		}
		if assetType != "" { // Current impl returns empty AssetType for direct UIC
			t.Errorf("Expected empty AssetType for direct UIC input, got %s", assetType)
		}
	})

	t.Run("InputIsStringUIC", func(t *testing.T) {
		uic, assetType, err := InstrumentToUic(ctx, nil, "456", nil) // No client needed
		if err != nil {
			t.Errorf("Unexpected error for string UIC input: %v", err)
		}
		if uic != 456 {
			t.Errorf("Expected UIC 456, got %d", uic)
		}
		if assetType != "" {
			t.Errorf("Expected empty AssetType for string UIC input, got %s", assetType)
		}
	})

	t.Run("InputIsSymbol_Success", func(t *testing.T) {
		symbol := "AAPL.O"
		assetTypes := "Stock"
		expectedUic := 211
		expectedAssetType := "Stock"

		handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/ref/v1/instruments" {t.Errorf("Path mismatch")}
			if r.URL.Query().Get("Keywords") != symbol {t.Errorf("Keywords param mismatch")}
			if r.URL.Query().Get("AssetTypes") != assetTypes {t.Errorf("AssetTypes param mismatch")}

			response := GetInstrumentsResponse{
				Data: []InstrumentDetail{
					{Uic: expectedUic, AssetType: expectedAssetType, Symbol: symbol, Description: "Apple Inc."},
				},
			}
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(response)
		})
		client, cleanup := setupUtilTestClient(t, handler)
		defer cleanup()

		uic, resolvedAssetType, err := InstrumentToUic(ctx, client, symbol, &assetTypes)
		if err != nil {
			t.Fatalf("InstrumentToUic failed for symbol '%s': %v", symbol, err)
		}
		if uic != expectedUic {
			t.Errorf("Expected UIC %d, got %d", expectedUic, uic)
		}
		if resolvedAssetType != expectedAssetType {
			t.Errorf("Expected AssetType '%s', got '%s'", expectedAssetType, resolvedAssetType)
		}
	})

	t.Run("InputIsSymbol_NoClientProvided", func(t *testing.T) {
		symbol := "MSFT.O"
		assetTypes := "Stock"
		_, _, err := InstrumentToUic(ctx, nil, symbol, &assetTypes)
		if err == nil {
			t.Error("Expected error when client is nil for symbol lookup")
		} else if !strings.Contains(err.Error(), "client is required to search for instrument by symbol") {
			t.Errorf("Unexpected error message: %v", err)
		}
	})

	t.Run("InputIsSymbol_APIReturnsNoMatch", func(t *testing.T) {
		symbol := "NONEXISTENT.SYM"
		assetTypes := "Stock"
		handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			response := GetInstrumentsResponse{Data: []InstrumentDetail{}} // Empty data
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(response)
		})
		client, cleanup := setupUtilTestClient(t, handler)
		defer cleanup()

		_, _, err := InstrumentToUic(ctx, client, symbol, &assetTypes)
		if err == nil {
			t.Error("Expected error when API returns no match")
		} else if !strings.Contains(err.Error(), "no instrument found matching symbol") {
			t.Errorf("Unexpected error message: %v", err)
		}
	})

	t.Run("InputIsSymbol_APIReturnsMultipleMatches", func(t *testing.T) {
		symbol := "GENERIC.SYM"
		assetTypes := "Stock"
		handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			response := GetInstrumentsResponse{
				Data: []InstrumentDetail{
					{Uic: 1, AssetType: "Stock", Symbol: "GENERIC.SYMA"},
					{Uic: 2, AssetType: "Stock", Symbol: "GENERIC.SYMB"},
				},
			}
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(response)
		})
		client, cleanup := setupUtilTestClient(t, handler)
		defer cleanup()

		_, _, err := InstrumentToUic(ctx, client, symbol, &assetTypes)
		if err == nil {
			t.Error("Expected error when API returns multiple matches (current strict behavior)")
		} else if !strings.Contains(err.Error(), "multiple instruments (2) found for symbol") {
			t.Errorf("Unexpected error message for multiple matches: %v", err)
		}
	})


	t.Run("InputIsNil", func(t *testing.T) {
		_, _, err := InstrumentToUic(ctx, nil, nil, nil)
		if err == nil || !strings.Contains(err.Error(), "instrument identifier cannot be nil") {
			t.Errorf("Expected 'cannot be nil' error, got %v", err)
		}
	})

	t.Run("InputIsUnsupportedType", func(t *testing.T) {
		_, _, err := InstrumentToUic(ctx, nil, 123.45, nil) // float64
		if err == nil || !strings.Contains(err.Error(), "unsupported instrument identifier type") {
			t.Errorf("Expected 'unsupported type' error, got %v", err)
		}
	})
}

// Helper for int pointers, if needed for GetInstrumentsParams
// func intPtr(i int) *int { return &i }
// func stringPtr(s string) *string { return &s }
// These are defined in other _test files. Ensure they are accessible if running tests together
// or define locally/in shared test util.
// For InstrumentToUic, params for GetInstruments are created internally, so these helpers not directly used by this test.
