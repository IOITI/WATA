package saxo_openapi

// Note: This file is in package saxo_openapi.

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


// --- Mock TokenManagerInterface for SaxoAuth in tests ---
type mockTokenManagerForRootServicesTests struct {
	StoreTokenFunc func(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error
	GetTokenFunc   func(tokenKey string) (*database.AuthTokenData, error)
}

func (m *mockTokenManagerForRootServicesTests) StoreToken(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error {
	if m.StoreTokenFunc != nil {
		return m.StoreTokenFunc(tokenHash, userID, encryptedPayload, expiresAt, ipAddress, userAgent, metadata)
	}
	return nil
}

func (m *mockTokenManagerForRootServicesTests) GetToken(tokenKey string) (*database.AuthTokenData, error) {
	if m.GetTokenFunc != nil {
		return m.GetTokenFunc(tokenKey)
	}
	return nil, fmt.Errorf("MockTokenManagerForRootServicesTests: GetToken called with key '%s', default is not found", tokenKey)
}


// Helper to setup client and server for root services tests
func setupRootServicesTest(t *testing.T, handler http.HandlerFunc) (*Client, *httptest.Server, func()) {
	server := httptest.NewServer(handler)

	tempDir := t.TempDir()
	tokenDirPath := filepath.Join(tempDir, "test_saxo_auth_data_for_rs_client")

	appCfg := saxo_authen.SaxoAppConfig{
		AppName:   "TestRootServicesClientApp",
		AppKey:    "testkey_rs_client",
		AppSecret: "testsecret_rs_client_must_be_32_bytes_long",
		AuthURL:   server.URL + "/auth",
		TokenURL:  server.URL + "/token",
		RedirectURL: server.URL + "/redirect",
	}

	mockTokenDB := &mockTokenManagerForRootServicesTests{}

	testAuth, err := saxo_authen.NewSaxoAuth(appCfg, tokenDirPath, mockTokenDB, nil)
	if err != nil {
		t.Fatalf("Failed to create SaxoAuth for test setup: %v", err)
	}

	testAuth.GetTokenOverride = func() (string, error) {
		return "test-token-for-rootservices", nil
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


func TestGetUser(t *testing.T) {
	expectedUser := UserResponse{ClientKey: "test_client_key", UserKey: "test_user_key", Name: "Test User"} // Types are local
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/root/v1/user" {
			t.Errorf("Expected path /root/v1/user, got %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedUser)
	})
	client, _, cleanup := setupRootServicesTest(t, handler)
	defer cleanup()

	user, err := client.GetUser(context.Background())
	if err != nil {
		t.Fatalf("GetUser failed: %v", err)
	}
	if user == nil {
		t.Fatal("GetUser returned nil user")
	}
	if user.ClientKey != expectedUser.ClientKey || user.UserKey != expectedUser.UserKey {
		t.Errorf("GetUser returned %+v, expected %+v", *user, expectedUser)
	}
}

func TestGetClient(t *testing.T) {
	expectedClientResponse := ClientResponse{ClientKey: "clientKeyValue", Name: "Test Client Name"} // Types are local
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/root/v1/clients/me" {
			t.Errorf("Expected path /root/v1/clients/me, got %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedClientResponse)
	})
	client, _, cleanup := setupRootServicesTest(t, handler)
	defer cleanup()

	clientResp, err := client.GetClient(context.Background())
	if err != nil {
		t.Fatalf("GetClient failed: %v", err)
	}
	if !reflect.DeepEqual(*clientResp, expectedClientResponse) {
		t.Errorf("GetClient response mismatch. Got %+v, Expected %+v", *clientResp, expectedClientResponse)
	}
}

func TestGetApplication(t *testing.T) {
	expectedAppResponse := ApplicationResponse{AppKey: "appKeyValue", AppName: "Test App"} // Types are local
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/root/v1/applications/me" {
			t.Errorf("Expected path /root/v1/applications/me, got %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedAppResponse)
	})
	client, _, cleanup := setupRootServicesTest(t, handler)
	defer cleanup()

	appResp, err := client.GetApplication(context.Background())
	if err != nil {
		t.Fatalf("GetApplication failed: %v", err)
	}
	if !reflect.DeepEqual(*appResp, expectedAppResponse) {
		t.Errorf("GetApplication response mismatch. Got %+v, Expected %+v", *appResp, expectedAppResponse)
	}
}

func TestGetSessionCapabilities(t *testing.T) {
	expectedCaps := SessionCapabilities{Orders: true, Positions: true} // Types are local
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/root/v1/sessions/capabilities" {
			t.Errorf("Expected path /root/v1/sessions/capabilities, got %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedCaps)
	})
	client, _, cleanup := setupRootServicesTest(t, handler)
	defer cleanup()

	caps, err := client.GetSessionCapabilities(context.Background())
	if err != nil {
		t.Fatalf("GetSessionCapabilities failed: %v", err)
	}
	if !reflect.DeepEqual(*caps, expectedCaps) {
		t.Errorf("GetSessionCapabilities response mismatch. Got %+v, Expected %+v", *caps, expectedCaps)
	}
}

func TestUpdateSessionCapabilities(t *testing.T) {
	requestBody := map[string]bool{"Orders": true}
	expectedResponse := SessionCapabilities{Orders: true, Positions: false} // Types are local

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "PATCH" {t.Errorf("Expected PATCH, got %s", r.Method)}
		if r.URL.Path != "/root/v1/sessions/capabilities" {t.Errorf("Path mismatch")}

		var receivedBody map[string]bool
		if err := json.NewDecoder(r.Body).Decode(&receivedBody); err != nil {
			t.Fatalf("Failed to decode request body: %v", err)
		}
		if !reflect.DeepEqual(receivedBody, requestBody) {
			t.Errorf("Request body mismatch. Got %+v, Expected %+v", receivedBody, requestBody)
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedResponse)
	})
	client, _, cleanup := setupRootServicesTest(t, handler)
	defer cleanup()

	updatedCaps, err := client.UpdateSessionCapabilities(context.Background(), requestBody)
	if err != nil {
		t.Fatalf("UpdateSessionCapabilities failed: %v", err)
	}
	if !reflect.DeepEqual(*updatedCaps, expectedResponse) {
		t.Errorf("UpdateSessionCapabilities response mismatch. Got %+v, Expected %+v", *updatedCaps, expectedResponse)
	}
}


func TestGetDiagnostics(t *testing.T) {
	expectedDiag := DiagnosticsResponse{AppName: "TestDiagnostics", ServiceReady: true} // Types are local
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/root/v1/diagnostics" {t.Errorf("Path mismatch")}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(expectedDiag)
	})
	client, _, cleanup := setupRootServicesTest(t, handler)
	defer cleanup()

	diag, err := client.GetDiagnostics(context.Background())
	if err != nil {
		t.Fatalf("GetDiagnostics failed: %v", err)
	}
	if !reflect.DeepEqual(*diag, expectedDiag) {
		t.Errorf("GetDiagnostics response mismatch. Got %+v, Expected %+v", *diag, expectedDiag)
	}
}

func TestGetFeatures(t *testing.T) {
	expectedFeatures := FeaturesResponse{Data: []Feature{{Name: "FeatureA", Enabled: true}}} // Types are local

	t.Run("NoParams", func(t *testing.T) {
		handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/root/v1/features" {t.Errorf("Path mismatch")}
			if r.URL.RawQuery != "" {t.Errorf("Expected no query params, got %s", r.URL.RawQuery)}
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(expectedFeatures)
		})
		client, _, cleanup := setupRootServicesTest(t, handler)
		defer cleanup()
		features, err := client.GetFeatures(context.Background(), nil, nil)
		if err != nil {t.Fatalf("GetFeatures (no params) failed: %v", err)}
		if !reflect.DeepEqual(*features, expectedFeatures) {t.Errorf("Response mismatch")}
	})

	t.Run("WithParams", func(t *testing.T) {
		group := "Group1"
		namesCSV := "FeatureA,FeatureB"
		handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/root/v1/features" {t.Errorf("Path mismatch")}
			if r.URL.Query().Get("Group") != group {t.Errorf("Group param mismatch")}
			if r.URL.Query().Get("NamesCSV") != namesCSV {t.Errorf("NamesCSV param mismatch")}
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(expectedFeatures)
		})
		client, _, cleanup := setupRootServicesTest(t, handler)
		defer cleanup()
		features, err := client.GetFeatures(context.Background(), &group, &namesCSV)
		if err != nil {t.Fatalf("GetFeatures (with params) failed: %v", err)}
		if !reflect.DeepEqual(*features, expectedFeatures) {t.Errorf("Response mismatch")}
	})
}
