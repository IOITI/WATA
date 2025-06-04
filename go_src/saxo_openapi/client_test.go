package saxo_openapi

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath" // Added import
	"pymath/go_src/saxo_authen"
	"pymath/go_src/database" // Added for database.AuthTokenData
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// MockAuthTokenManager implements saxo_authen.TokenManagerInterface for client tests.
type MockAuthTokenManager struct {
	StoreTokenFunc func(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error
	GetTokenFunc   func(tokenKey string) (*database.AuthTokenData, error)
}

func (m *MockAuthTokenManager) StoreToken(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error {
	if m.StoreTokenFunc != nil {
		return m.StoreTokenFunc(tokenHash, userID, encryptedPayload, expiresAt, ipAddress, userAgent, metadata)
	}
	return nil
}

func (m *MockAuthTokenManager) GetToken(tokenKey string) (*database.AuthTokenData, error) {
	if m.GetTokenFunc != nil {
		return m.GetTokenFunc(tokenKey)
	}
	return nil, fmt.Errorf("MockAuthTokenManager: GetToken called with key '%s', default is not found", tokenKey)
}


// Helper to create a real *saxo_authen.SaxoAuth with a mockable GetToken behavior.
func newTestAuthenticator(t *testing.T, getTokenFunc func() (string, error)) *saxo_authen.SaxoAuth {
	tempDir := t.TempDir() // Create a temp dir for salt file path
	tokenDirPath := filepath.Join(tempDir, "test_auth_files")

	appCfg := saxo_authen.SaxoAppConfig{
		AppName:   "TestAppForClient",
		AppKey:    "testkey",
		AppSecret: "testsecret_must_be_32_bytes_long_for_aes256",
		AuthURL:   "http://dummy/auth",
		TokenURL:  "http://dummy/token",
		RedirectURL: "http://dummy/redirect",
	}

	mockTokenDB := &MockAuthTokenManager{ // Use the local mock
		GetTokenFunc: func(tokenKey string) (*database.AuthTokenData, error) {
			// This specific mock for GetToken on TokenManagerInterface might not be directly
			// called if SaxoAuth.GetTokenOverride is used, but it's good practice
			// to have a default behavior for the mock.
			// For example, SaxoAuth internal logic might still call s.tokenDB.GetToken
			// even if the outermost GetToken is overridden.
			// Let's simulate finding a dummy encrypted token if needed.
			dummyEncrypted := []byte("dummy-encrypted-data-for-" + tokenKey)
			return &database.AuthTokenData{EncryptedData: dummyEncrypted}, nil
		},
	}

	auth, err := saxo_authen.NewSaxoAuth(appCfg, tokenDirPath, mockTokenDB, nil)
	if err != nil {
		// Fallback for AppSecret if the long one above had issues in some context
		appCfg.AppSecret = "another_valid_secret_key_32bytes"
		auth, err = saxo_authen.NewSaxoAuth(appCfg, tokenDirPath, mockTokenDB, nil)
		if err != nil {
			t.Fatalf("Failed to create test SaxoAuth: %v. Ensure AppSecret is valid for NewCipher.", err)
		}
	}
	auth.GetTokenOverride = getTokenFunc
	return auth
}


func TestNewClient(t *testing.T) {
	auth := newTestAuthenticator(t, func() (string, error) { return "test-token", nil })

	t.Run("LiveEnvironment", func(t *testing.T) {
		client, err := NewClient(auth, EnvironmentLive, 15*time.Second)
		if err != nil {
			t.Fatalf("NewClient failed for live env: %v", err)
		}
		if client.Environment != EnvironmentLive {
			t.Errorf("Expected env %s, got %s", EnvironmentLive, client.Environment)
		}
		expectedAPIBase := liveAPIBaseURL + liveAPIOpenAPIPath
		if client.apiBaseURL != expectedAPIBase {
			t.Errorf("Expected apiBaseURL %s, got %s", expectedAPIBase, client.apiBaseURL)
		}
		if client.httpClient.Timeout != 15*time.Second {
			t.Errorf("Expected timeout 15s, got %v", client.httpClient.Timeout)
		}
	})

	t.Run("SimulationEnvironment", func(t *testing.T) {
		client, err := NewClient(auth, EnvironmentSimulation, 0)
		if err != nil {
			t.Fatalf("NewClient failed for sim env: %v", err)
		}
		if client.Environment != EnvironmentSimulation {
			t.Errorf("Expected env %s, got %s", EnvironmentSimulation, client.Environment)
		}
		expectedAPIBase := simulationAPIBaseURL + simAPIOpenAPIPath
		if client.apiBaseURL != expectedAPIBase {
			t.Errorf("Expected apiBaseURL %s, got %s", expectedAPIBase, client.apiBaseURL)
		}
		if client.httpClient.Timeout != time.Duration(defaultTimeoutSeconds)*time.Second {
			t.Errorf("Expected default timeout, got %v", client.httpClient.Timeout)
		}
	})

	t.Run("InvalidEnvironment", func(t *testing.T) {
		_, err := NewClient(auth, "invalid-env", 10*time.Second)
		if err == nil {
			t.Error("NewClient should fail for invalid environment")
		}
	})

	t.Run("NilAuthenticator", func(t *testing.T) {
		_, err := NewClient(nil, EnvironmentLive, 10*time.Second)
		if err == nil {
			t.Error("NewClient should fail for nil authenticator")
		}
	})
}

type testTargetStruct struct {
	FieldA string `json:"fieldA"`
	FieldB int    `json:"fieldB"`
}

func TestDoRequest_Success(t *testing.T) {
	auth := newTestAuthenticator(t, func() (string, error) { return "valid-token", nil })

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer valid-token" {
			t.Errorf("Auth header incorrect: got %s", r.Header.Get("Authorization"))
		}
		if r.Method != "GET" { t.Errorf("Expected GET, got %s", r.Method) }
		if r.URL.Path != "/test/endpoint" {
			t.Errorf("Expected path /test/endpoint, got %s", r.URL.Path)
		}
		if r.URL.Query().Get("param1") != "value1" {t.Error("Query param 'param1' mismatch")}

		w.Header().Set(XRateLimitSessionRemaining, "99")
		w.Header().Set(XRateLimitSessionReset, "5.0")
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintln(w, `{"fieldA": "hello", "fieldB": 123}`)
	}))
	defer server.Close()

	client, _ := NewClient(auth, EnvironmentLive, 10*time.Second)
	client.apiBaseURL = server.URL

	queryParams := url.Values{}
	queryParams.Set("param1", "value1")
	responseBodyType := reflect.TypeOf(testTargetStruct{})

	result, _, err := client.doRequest(context.Background(), "GET", "test/endpoint", queryParams, nil, responseBodyType)
	if err != nil {
		t.Fatalf("doRequest failed: %v", err)
	}

	target, ok := result.(*testTargetStruct)
	if !ok {
		t.Fatalf("doRequest result type assertion failed. Got %T", result)
	}
	if target.FieldA != "hello" {t.Errorf("target.FieldA: expected 'hello', got '%s'", target.FieldA)}
	if target.FieldB != 123 {t.Errorf("target.FieldB: expected 123, got %d", target.FieldB)}

	client.rateLimiter.mutex.Lock()
	if client.rateLimiter.sessionRemaining != 99 {
		t.Errorf("Rate limiter remaining not updated, expected 99, got %d", client.rateLimiter.sessionRemaining)
	}
	client.rateLimiter.mutex.Unlock()
}

func TestDoRequest_HttpError_WithSaxoErrorParsing(t *testing.T) {
	auth := newTestAuthenticator(t, func() (string, error) { return "test-token", nil })
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		fmt.Fprintln(w, `{"MessageId": "guid-msg-id", "ErrorCode": "IllegalInput", "Message": "The input was bad."}`)
	}))
	defer server.Close()
	client, _ := NewClient(auth, EnvironmentLive, 10*time.Second)
	client.apiBaseURL = server.URL

	_, _, err := client.doRequest(context.Background(), "GET", "error/endpoint", nil, nil, nil)
	if err == nil {t.Fatal("doRequest should have failed")}

	openAPIErr, ok := err.(*OpenAPIError)
	if !ok {t.Fatalf("Expected OpenAPIError, got %T: %v", err, err)}
	if openAPIErr.Code != http.StatusBadRequest {t.Errorf("Expected code 400, got %d", openAPIErr.Code)}
	if openAPIErr.ErrorCode != "IllegalInput" {t.Errorf("Expected ErrorCode 'IllegalInput', got '%s'", openAPIErr.ErrorCode)}
	if openAPIErr.MessageID != "guid-msg-id" {t.Errorf("Expected MessageID 'guid-msg-id', got '%s'", openAPIErr.MessageID)}
	if !strings.Contains(openAPIErr.ErrorMessage, "The input was bad.") {t.Errorf("Expected ErrorMessage, got '%s'", openAPIErr.ErrorMessage)}
	if !strings.Contains(openAPIErr.RawContent, "The input was bad.") {t.Errorf("RawContent mismatch")}
}


func TestDoRequest_RateLimitRetry(t *testing.T) {
	auth := newTestAuthenticator(t, func() (string, error) { return "test-token", nil })
	var requestCount int32 = 0

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		currentCount := atomic.AddInt32(&requestCount, 1)
		if currentCount == 1 {
			w.Header().Set(XRateLimitSessionRemaining, "0")
			w.Header().Set(XRateLimitSessionReset, "0.05")
			w.WriteHeader(http.StatusTooManyRequests)
			fmt.Fprintln(w, `{"message": "Rate limit exceeded"}`)
		} else {
			w.Header().Set(XRateLimitSessionRemaining, "10")
			w.Header().Set(XRateLimitSessionReset, "10.0")
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprintln(w, `{"fieldA": "success_after_retry", "fieldB": 200}`)
		}
	}))
	defer server.Close()

	client, _ := NewClient(auth, EnvironmentLive, 10*time.Second)
	client.apiBaseURL = server.URL
	client.rateLimiter = NewRateLimiter(1)
	client.rateLimiter.sessionRemaining = 5

	responseBodyType := reflect.TypeOf(testTargetStruct{})
	result, _, err := client.doRequest(context.Background(), "GET", "retry/endpoint", nil, nil, responseBodyType)
	if err != nil {
		t.Fatalf("doRequest failed after expected retry: %v", err)
	}
	if atomic.LoadInt32(&requestCount) != 2 {
		t.Errorf("Expected 2 requests, got %d", requestCount)
	}
	target, _ := result.(*testTargetStruct)
	if target.FieldA != "success_after_retry" {
		t.Errorf("Expected target.FieldA 'success_after_retry', got: %s", target.FieldA)
	}
}

func TestDoRequest_AuthTokenFailure(t *testing.T) {
	auth := newTestAuthenticator(t, func() (string, error) { return "", fmt.Errorf("mock auth failed") })
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("Server handler should not be called when GetToken fails")
	}))
	defer server.Close()
	client, _ := NewClient(auth, EnvironmentLive, 10*time.Second)
	client.apiBaseURL = server.URL

	_, _, err := client.doRequest(context.Background(), "GET", "some/endpoint", nil, nil, nil)
	if err == nil {t.Fatal("doRequest should have failed")}
	if !strings.Contains(err.Error(), "failed to get authentication token") {
		t.Errorf("Expected error about auth token failure, got: %v", err)
	}
}

func TestDoRequest_NoContent_And_Unmarshal(t *testing.T) {
	auth := newTestAuthenticator(t, func() (string, error) { return "test-token", nil })
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	client, _ := NewClient(auth, EnvironmentLive, 10*time.Second)
	client.apiBaseURL = server.URL

	responseBodyType := reflect.TypeOf(testTargetStruct{})
	result, _, err := client.doRequest(context.Background(), "POST", "no_content_endpoint", nil, bytes.NewBufferString("{}"), responseBodyType)
	if err != nil {
		t.Fatalf("doRequest failed for 204 No Content: %v", err)
	}

	target, ok := result.(*testTargetStruct)
	if !ok {t.Fatalf("Type assertion failed for 204 No Content result. Got %T", result)}

	if target.FieldA != "" || target.FieldB != 0 {
		t.Errorf("targetStruct should be zero-value after 204 No Content, got %+v", target)
	}
}

func TestDoRequest_ContextCancelled(t *testing.T) {
	auth := newTestAuthenticator(t, func() (string, error) { return "test-token", nil })
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(100 * time.Millisecond)
		t.Log("Server received request, but context should be cancelled soon.")
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	client, _ := NewClient(auth, EnvironmentLive, 200*time.Millisecond)
	client.apiBaseURL = server.URL

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	_, _, err := client.doRequest(ctx, "GET", "timeout/endpoint", nil, nil, nil)
	if err == nil {
		t.Fatal("doRequest should have failed due to context cancellation")
	}
	if !errors.Is(err, context.DeadlineExceeded) && !strings.Contains(err.Error(), "context deadline exceeded") && !strings.Contains(err.Error(), "context cancelled") {
		t.Errorf("Expected context deadline exceeded or cancelled error, got: %v", err)
	}
}
