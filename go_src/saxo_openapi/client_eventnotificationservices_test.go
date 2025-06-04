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
	"testing"
	"time"
)

// --- Mock TokenManagerInterface for SaxoAuth in ENS tests ---
type mockTokenManagerForENSTests struct {
	saxo_authen.TokenManagerInterface
	StoreTokenFunc func(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error
	GetTokenFunc   func(tokenKey string) (*database.AuthTokenData, error)
}

func (m *mockTokenManagerForENSTests) StoreToken(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error {
	if m.StoreTokenFunc != nil {
		return m.StoreTokenFunc(tokenHash, userID, encryptedPayload, expiresAt, ipAddress, userAgent, metadata)
	}
	return nil
}
func (m *mockTokenManagerForENSTests) GetToken(tokenKey string) (*database.AuthTokenData, error) {
	if m.GetTokenFunc != nil {
		return m.GetTokenFunc(tokenKey)
	}
	return nil, fmt.Errorf("MockTokenManagerForENSTests: GetToken called, default not found for key '%s'", tokenKey)
}

// Helper to setup client and server for ENS tests
func setupENSTest(t *testing.T, handler http.HandlerFunc) (*Client, *httptest.Server, func()) {
	server := httptest.NewServer(handler)
	tempDir := t.TempDir()
	tokenDirPath := filepath.Join(tempDir, "test_saxo_auth_data_for_ens_client")

	appCfg := saxo_authen.SaxoAppConfig{
		AppName:   "TestENSClientApp",
		AppKey:    "testkey_ens_client",
		AppSecret: "testsecret_ens_client_must_be_32_bytes_long",
		AuthURL:   server.URL + "/auth", TokenURL: server.URL + "/token", RedirectURL: server.URL + "/redirect",
	}
	mockTokenDB := &mockTokenManagerForENSTests{}
	testAuth, err := saxo_authen.NewSaxoAuth(appCfg, tokenDirPath, mockTokenDB, nil)
	if err != nil {
		t.Fatalf("Failed to create SaxoAuth for test setup: %v", err)
	}
	testAuth.GetTokenOverride = func() (string, error) { return "test-token-for-ens", nil }

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

func TestCreateActivitySubscription(t *testing.T) {
	args := ENSActivitySubscriptionRequest{
		ContextID:   "TestCtx123",
		ReferenceID: "MyRef456",
		Activities:  []string{"AccountOrders", "Positions"},
		AccountKey:  "AccKey789",
		Format:      "application/json",
		RefreshRate: 5000,
	}
	expectedResponse := ENSActivitySubscriptionResponse{
		ContextID: args.ContextID, ReferenceID: args.ReferenceID, State: "Active",
		InactivityTimeout: 60, // Example server-assigned value
		RefreshRate: args.RefreshRate,
		Format: args.Format,
	}

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ens/v1/activities/subscriptions" {
			t.Errorf("Path mismatch, got %s", r.URL.Path)
		}
		if r.Method != "POST" {
			t.Errorf("Expected POST, got %s", r.Method)
		}

		var receivedBody ENSActivitySubscriptionRequest
		if err := json.NewDecoder(r.Body).Decode(&receivedBody); err != nil {
			t.Fatalf("Failed to decode request body: %v", err)
		}
		if !reflect.DeepEqual(receivedBody, args) {
			t.Errorf("Request body mismatch.\nGot: %+v\nExp: %+v", receivedBody, args)
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated) // 201 for create
		json.NewEncoder(w).Encode(expectedResponse)
	})
	client, _, cleanup := setupENSTest(t, handler)
	defer cleanup()

	response, err := client.CreateActivitySubscription(context.Background(), args)
	if err != nil {
		t.Fatalf("CreateActivitySubscription failed: %v", err)
	}
	if !reflect.DeepEqual(*response, expectedResponse) {
		t.Errorf("CreateActivitySubscription response mismatch.\nGot: %+v\nExp: %+v", *response, expectedResponse)
	}
}

func TestRemoveENSSubscriptionsByTag(t *testing.T) {
	contextID := "CtxToRemove"
	tag := "TagToRemove"

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		expectedPath := fmt.Sprintf("/ens/v1/activities/subscriptions/%s", contextID)
		if r.URL.Path != expectedPath {
			t.Errorf("Path mismatch, got %s, expected %s", r.URL.Path, expectedPath)
		}
		if r.Method != "DELETE" {
			t.Errorf("Expected DELETE, got %s", r.Method)
		}
		if r.URL.Query().Get("Tag") != tag {
			t.Errorf("Query param Tag mismatch. Got %s, expected %s", r.URL.Query().Get("Tag"), tag)
		}
		w.WriteHeader(http.StatusAccepted) // 202
	})
	client, _, cleanup := setupENSTest(t, handler)
	defer cleanup()

	err := client.RemoveENSSubscriptionsByTag(context.Background(), contextID, tag)
	if err != nil {
		t.Fatalf("RemoveENSSubscriptionsByTag failed: %v", err)
	}
}

func TestRemoveENSSubscriptionByID(t *testing.T) {
	contextID := "CtxForIDRemove"
	referenceID := "RefIDToRemove"

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		expectedPath := fmt.Sprintf("/ens/v1/activities/subscriptions/%s/%s", contextID, referenceID)
		if r.URL.Path != expectedPath {
			t.Errorf("Path mismatch, got %s, expected %s", r.URL.Path, expectedPath)
		}
		if r.Method != "DELETE" {
			t.Errorf("Expected DELETE, got %s", r.Method)
		}
		w.WriteHeader(http.StatusNoContent) // 204
	})
	client, _, cleanup := setupENSTest(t, handler)
	defer cleanup()

	err := client.RemoveENSSubscriptionByID(context.Background(), contextID, referenceID)
	if err != nil {
		t.Fatalf("RemoveENSSubscriptionByID failed: %v", err)
	}
}
