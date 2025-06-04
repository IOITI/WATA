package saxo_openapi

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath" // For test setup if needed
	"pymath/go_src/database" // For mock token manager if used by SaxoAuth mock
	"pymath/go_src/saxo_authen"
	"reflect"
	"testing"
	"time"
)

// --- Mock TokenManagerInterface for SaxoAuth in portfolio tests ---
// Re-defining a minimal mock here. Ideally, this would be a shared test utility.
type mockTokenManagerForPortfolioTests struct {
	saxo_authen.TokenManagerInterface // Embed interface for type safety
	StoreTokenFunc func(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error
	GetTokenFunc   func(tokenKey string) (*database.AuthTokenData, error)
}

func (m *mockTokenManagerForPortfolioTests) StoreToken(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error {
	if m.StoreTokenFunc != nil {
		return m.StoreTokenFunc(tokenHash, userID, encryptedPayload, expiresAt, ipAddress, userAgent, metadata)
	}
	return nil
}

func (m *mockTokenManagerForPortfolioTests) GetToken(tokenKey string) (*database.AuthTokenData, error) {
	if m.GetTokenFunc != nil {
		return m.GetTokenFunc(tokenKey)
	}
	return nil, fmt.Errorf("MockTokenManagerForPortfolioTests: GetToken called, default not found for key '%s'", tokenKey)
}


// Helper to setup client and server for portfolio tests
func setupPortfolioTest(t *testing.T, handler http.HandlerFunc) (*Client, *httptest.Server, func()) {
	server := httptest.NewServer(handler)

	tempDir := t.TempDir()
	tokenDirPath := filepath.Join(tempDir, "test_saxo_auth_data_for_portfolio_client")

	appCfg := saxo_authen.SaxoAppConfig{
		AppName:   "TestPortfolioClientApp",
		AppKey:    "testkey_port_client",
		AppSecret: "testsecret_port_client_must_be_32_bytes_long",
		AuthURL:   server.URL + "/auth",
		TokenURL:  server.URL + "/token",
		RedirectURL: server.URL + "/redirect",
	}

	mockTokenDB := &mockTokenManagerForPortfolioTests{}

	testAuth, err := saxo_authen.NewSaxoAuth(appCfg, tokenDirPath, mockTokenDB, nil)
	if err != nil {
		t.Fatalf("Failed to create SaxoAuth for test setup: %v", err)
	}

	testAuth.GetTokenOverride = func() (string, error) {
		return "test-token-for-portfolio", nil
	}

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


// --- Tests for Account Endpoints ---

func TestGetAccount(t *testing.T) {
	accountKey := "testAccountKey123"
	expectedAccount := Account{AccountKey: accountKey, AccountID: "TestAccID", Currency: "USD"}

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		expectedPath := fmt.Sprintf("/port/v1/accounts/%s", accountKey)
		if r.URL.Path != expectedPath {
			t.Errorf("Expected path %s, got %s", expectedPath, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedAccount)
	})
	client, _, cleanup := setupPortfolioTest(t, handler)
	defer cleanup()

	account, err := client.GetAccount(context.Background(), accountKey)
	if err != nil {
		t.Fatalf("GetAccount failed: %v", err)
	}
	if !reflect.DeepEqual(*account, expectedAccount) {
		t.Errorf("GetAccount response mismatch.\nGot: %+v\nExp: %+v", *account, expectedAccount)
	}
}

func TestGetAccounts(t *testing.T) {
	clientKey := "testClientKey"
	expectedResponse := GetAccountsResponse{
		Data: []Account{
			{AccountKey: "acc1", ClientID: clientKey, Currency: "EUR"},
			{AccountKey: "acc2", ClientID: clientKey, Currency: "USD"},
		},
	}
	params := &GetAccountsParams{ClientKey: clientKey}

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/port/v1/accounts" {t.Errorf("Path mismatch")}
		if r.URL.Query().Get("ClientKey") != clientKey {t.Errorf("ClientKey param mismatch")}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedResponse)
	})
	client, _, cleanup := setupPortfolioTest(t, handler)
	defer cleanup()

	response, err := client.GetAccounts(context.Background(), params)
	if err != nil {
		t.Fatalf("GetAccounts failed: %v", err)
	}
	if !reflect.DeepEqual(*response, expectedResponse) {
		t.Errorf("GetAccounts response mismatch.\nGot: %+v\nExp: %+v", *response, expectedResponse)
	}
}

func TestUpdateAccount(t *testing.T) {
    accountKey := "accToUpdate"
    updates := map[string]interface{}{"AccountValueProtectionLimit": 5000.0}
    // Assume PATCH returns the updated account or 204. If 204, GetAccount is called.
    // For this test, let's assume it returns the updated account.
    expectedAccount := Account{AccountKey: accountKey, AccountValueProtectionLimit: floatPtr(5000.0)}

    handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        if r.Method != "PATCH" {t.Errorf("Expected PATCH, got %s", r.Method)}
        expectedPath := fmt.Sprintf("/port/v1/accounts/%s", accountKey)
        if r.URL.Path != expectedPath {t.Errorf("Path mismatch")}

        var receivedBody map[string]interface{}
        if err := json.NewDecoder(r.Body).Decode(&receivedBody); err != nil {
            t.Fatalf("Failed to decode request body: %v", err)
        }
        if !reflect.DeepEqual(receivedBody, updates) {
            t.Errorf("Request body mismatch. Got %+v, Expected %+v", receivedBody, updates)
        }
        w.Header().Set("Content-Type", "application/json")
        json.NewEncoder(w).Encode(expectedAccount) // Return updated account
    })
    client, _, cleanup := setupPortfolioTest(t, handler)
    defer cleanup()

    account, err := client.UpdateAccount(context.Background(), accountKey, updates)
    if err != nil {
        t.Fatalf("UpdateAccount failed: %v", err)
    }
    if account == nil {
        t.Fatal("UpdateAccount returned nil account, expected updated account data.")
    }
    if account.AccountValueProtectionLimit == nil || *account.AccountValueProtectionLimit != 5000.0 {
        t.Errorf("AccountValueProtectionLimit not updated as expected. Got: %v", account.AccountValueProtectionLimit)
    }
}


// --- Tests for Balance Endpoints ---
func TestGetAccountBalance(t *testing.T) {
	accountKey := "accForBalance"
	expectedBalance := Balance{CashBalance: 10000.00, Currency: "USD"}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		expectedPath := fmt.Sprintf("/port/v1/balances/%s", accountKey)
		if r.URL.Path != expectedPath {t.Errorf("Path mismatch")}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedBalance)
	})
	client, _, cleanup := setupPortfolioTest(t, handler)
	defer cleanup()

	balance, err := client.GetAccountBalance(context.Background(), accountKey)
	if err != nil {
		t.Fatalf("GetAccountBalance failed: %v", err)
	}
	if !reflect.DeepEqual(*balance, expectedBalance) {
		t.Errorf("GetAccountBalance response mismatch.\nGot: %+v\nExp: %+v", *balance, expectedBalance)
	}
}

// --- Tests for Client (portfolio) Endpoints ---
func TestUpdateClientDetails(t *testing.T) {
    updates := map[string]interface{}{"AccountValueProtectionLimit": 7500.0}

    handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        if r.Method != "PATCH" {t.Errorf("Expected PATCH, got %s", r.Method)}
        if r.URL.Path != "/port/v1/clients/me" {t.Errorf("Path mismatch")}
        // Check body if needed
        w.WriteHeader(http.StatusNoContent) // Common response for PATCH
    })
    client, _, cleanup := setupPortfolioTest(t, handler)
    defer cleanup()

    err := client.UpdateClientDetails(context.Background(), updates)
    if err != nil {
        t.Fatalf("UpdateClientDetails failed: %v", err)
    }
    // To verify, one might call GetClientDetails if the API doesn't return the body.
}


// --- Tests for Positions Endpoints (Example for GetPositions) ---
func TestGetPositions(t *testing.T) {
	clientKey := "clientForPositions"
	params := &GetPositionParams{ClientKey: &clientKey}
	expectedResponse := GetPositionsResponse{
		Data: []Position{
			{PositionID: "pos1", PositionBase: PositionBase{AssetType: "Stock", Uic: 123}},
		},
		Count: 1,
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/port/v1/positions" {t.Errorf("Path mismatch")}
		if r.URL.Query().Get("ClientKey") != clientKey {t.Errorf("ClientKey param mismatch")}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedResponse)
	})
	client, _, cleanup := setupPortfolioTest(t, handler)
	defer cleanup()

	response, err := client.GetPositions(context.Background(), params)
	if err != nil {
		t.Fatalf("GetPositions failed: %v", err)
	}
	if !reflect.DeepEqual(*response, expectedResponse) {
		t.Errorf("GetPositions response mismatch.\nGot: %+v\nExp: %+v", *response, expectedResponse)
	}
}

// --- Tests for Subscriptions ---
func TestCreatePortfolioSubscription(t *testing.T) {
	subType := "accounts"
	args := CreateSubscriptionArgs{
		ContextID: "TestContextID",
		ReferenceID: "TestRefID",
		RefreshRate: 1000,
		Arguments: map[string]interface{}{"ClientKey": "Client123"},
	}
	expectedResponse := CreateSubscriptionResponse{ // Assuming it echoes back or provides similar fields
		ContextID: args.ContextID,
		ReferenceID: args.ReferenceID,
		RefreshRate: args.RefreshRate,
		State: "Active", // Example state
	}

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		expectedPath := fmt.Sprintf("/port/v1/%s/subscriptions", subType)
		if r.Method != "POST" {t.Errorf("Expected POST, got %s", r.Method)}
		if r.URL.Path != expectedPath {t.Errorf("Path mismatch, got %s, expected %s", r.URL.Path, expectedPath)}

		var receivedBody CreateSubscriptionArgs
        if err := json.NewDecoder(r.Body).Decode(&receivedBody); err != nil {
            t.Fatalf("Failed to decode request body: %v", err)
        }
        if !reflect.DeepEqual(receivedBody, args) {
            t.Errorf("Request body mismatch. Got %+v, Expected %+v", receivedBody, args)
        }
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated) // Typically 201 for create
		json.NewEncoder(w).Encode(expectedResponse)
	})
	client, _, cleanup := setupPortfolioTest(t, handler)
	defer cleanup()

	response, err := client.CreatePortfolioSubscription(context.Background(), subType, args)
	if err != nil {
		t.Fatalf("CreatePortfolioSubscription failed: %v", err)
	}
	if response.ContextID != expectedResponse.ContextID || response.State != "Active" {
		t.Errorf("CreatePortfolioSubscription response mismatch.\nGot: %+v\nExp: %+v", *response, expectedResponse)
	}
}

func TestRemovePortfolioSubscriptionsByTag(t *testing.T) {
	subType := "positions"
	contextID := "Ctx1"
	tag := "MyTag"

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		expectedPath := fmt.Sprintf("/port/v1/%s/subscriptions/%s/%s", subType, contextID, tag)
		if r.Method != "DELETE" {t.Errorf("Expected DELETE, got %s", r.Method)}
		if r.URL.Path != expectedPath {t.Errorf("Path mismatch, got %s, expected %s", r.URL.Path, expectedPath)}
		w.WriteHeader(http.StatusAccepted) // 202
	})
	client, _, cleanup := setupPortfolioTest(t, handler)
	defer cleanup()

	err := client.RemovePortfolioSubscriptionsByTag(context.Background(), subType, contextID, tag)
	if err != nil {
		t.Fatalf("RemovePortfolioSubscriptionsByTag failed: %v", err)
	}
}


// Helper functions like floatPtr, stringPtr, boolPtr are now in test_utils_test.go

// Add more tests for other portfolio endpoints (NetPositions, ClosedPositions, AccountGroups, Exposure)
// following the patterns above.
// Remember to test error cases from the API (e.g., 404 Not Found, 400 Bad Request).
