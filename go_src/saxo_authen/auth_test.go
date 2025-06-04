package saxo_authen

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"pymath/go_src/database"
	"strings"
	"sync"
	"testing"
	"time"
	// "pymath/go_src/configuration" // No longer needed after NewSaxoAuth changes
)

// --- Mocking Dependencies ---

// MockTokenManager implements saxo_authen.TokenManagerInterface for testing.
type MockTokenManager struct {
	StoreTokenFunc func(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error
	GetTokenFunc   func(tokenKey string) (*database.AuthTokenData, error)
}

func (m *MockTokenManager) StoreToken(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error {
	if m.StoreTokenFunc != nil {
		return m.StoreTokenFunc(tokenHash, userID, encryptedPayload, expiresAt, ipAddress, userAgent, metadata)
	}
	return nil
}

func (m *MockTokenManager) GetToken(tokenKey string) (*database.AuthTokenData, error) {
	if m.GetTokenFunc != nil {
		return m.GetTokenFunc(tokenKey)
	}
	return nil, fmt.Errorf("MockTokenManager: GetToken called with key '%s', default is not found", tokenKey)
}

var (
	mockMqSendCount   int
	mockMqLastMessage string
	mockMqShouldError bool
	mockMqError       error
	mockMqMutex       sync.Mutex
)

func resetMockMqTelegran() {
	mockMqMutex.Lock()
	defer mockMqMutex.Unlock()
	mockMqSendCount = 0
	mockMqLastMessage = ""
	mockMqShouldError = false
	mockMqError = nil
}

// --- Test Setup Helper ---
func setupSaxoAuthTest(t *testing.T, appName string, server *httptest.Server, mockTokenDB TokenManagerInterface) (*SaxoAuth, func()) {
	tempDir := t.TempDir()

	appCfg := SaxoAppConfig{
		AppName:      appName,
		AppKey:       "test_app_key_direct",
		AppSecret:    "test_app_secret_direct_for_cipher_32bytes",
		AuthURL:      server.URL + "/auth",
		TokenURL:     server.URL + "/token",
		RedirectURL:  server.URL + "/redirect",
		CodeVerifier: "test_verifier_direct",
	}
	tokenDirPath := filepath.Join(tempDir, "saxo_tokens_for_"+appName)

	auth, err := NewSaxoAuth(appCfg, tokenDirPath, mockTokenDB, nil)
	if err != nil {
		t.Fatalf("NewSaxoAuth failed: %v", err)
	}

	cleanup := func() {
		server.Close()
	}
	return auth, cleanup
}

func TestNewSaxoAuth(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	appName := "PrimaryApp"
	mockTokenDB := &MockTokenManager{}
	auth, cleanup := setupSaxoAuthTest(t, appName, server, mockTokenDB)
	defer cleanup()

	if auth.appConfig.AppName != appName {
		t.Errorf("Expected AppName %s, got %s", appName, auth.appConfig.AppName)
	}
	if auth.aead == nil {
		t.Error("AEAD cipher not initialized")
	}
	if auth.state == "" {
		t.Error("State not generated")
	}

	expectedSubPath := filepath.Join("saxo_tokens_for_"+appName, appName+"_"+defaultAuthCodeFileName)
	if !strings.Contains(auth.authCodePath, expectedSubPath) {
		t.Errorf("authCodePath '%s' does not seem to contain expected subpath '%s'",
			auth.authCodePath, expectedSubPath)
	}

	badAppCfg := SaxoAppConfig{AppName: "BadApp"}
	tokenDirPathBad := filepath.Join(t.TempDir(), "tokens_bad")
	_, err := NewSaxoAuth(badAppCfg, tokenDirPathBad, &MockTokenManager{}, nil)
	if err == nil {
		t.Error("Expected error for bad app config (missing fields)")
	} else if !strings.Contains(err.Error(), "missing required fields") {
		t.Errorf("Unexpected error for bad app config: %v", err)
	}

	emptyNameCfg := SaxoAppConfig{
		AppKey:      "a_key",
		AppSecret:   "a_secret_for_empty_appname_test",
		AuthURL:     "an_auth_url",
		TokenURL:    "a_token_url",
		RedirectURL: "a_redirect_url",
		AppName:     "",
	}
	tokenDirPathEmptyName := filepath.Join(t.TempDir(), "tokens_empty_name")
	_, err = NewSaxoAuth(emptyNameCfg, tokenDirPathEmptyName, &MockTokenManager{}, nil)
	if err == nil || !strings.Contains(err.Error(), "SaxoAppConfig.AppName cannot be empty") {
		t.Errorf("Expected error for empty AppName ('SaxoAppConfig.AppName cannot be empty'), got: %v", err)
	}
}


func TestGetAuthorizationURL(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	mockTokenDB := &MockTokenManager{}
	auth, cleanup := setupSaxoAuthTest(t, "TestApp", server, mockTokenDB)
	defer cleanup()

	authURL, err := auth.GetAuthorizationURL()
	if err != nil {
		t.Fatalf("GetAuthorizationURL failed: %v", err)
	}

	if !strings.HasPrefix(authURL, auth.appConfig.AuthURL) {
		t.Errorf("URL does not start with AuthURL. Got: %s", authURL)
	}
	parsed, _ := url.Parse(authURL)
	queryParams := parsed.Query()
	if queryParams.Get("response_type") != "code" {
		t.Error("response_type not 'code'")
	}
	if queryParams.Get("client_id") != auth.appConfig.AppKey {
		t.Error("client_id mismatch")
	}
	if queryParams.Get("state") != auth.state {
		t.Error("state mismatch")
	}
	if queryParams.Get("redirect_uri") != auth.appConfig.RedirectURL {
		t.Error("redirect_uri mismatch")
	}
}


func TestReadAuthCodeFromFile(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	mockTokenDB := &MockTokenManager{}
	auth, cleanup := setupSaxoAuthTest(t, "TestAppFile", server, mockTokenDB)
	defer cleanup()

	code, err := auth.readAuthCodeFromFile()
	if err != nil {
		t.Fatalf("readAuthCodeFromFile failed when no file: %v", err)
	}
	if code != "" {
		t.Errorf("Expected empty code when no file, got '%s'", code)
	}

	expectedCode := "sample_auth_code_123"
	if err := os.WriteFile(auth.authCodePath, []byte(expectedCode+"\n  "), 0600); err != nil {
		t.Fatalf("Failed to write auth code file for test: %v", err)
	}
	code, err = auth.readAuthCodeFromFile()
	if err != nil {
		t.Fatalf("readAuthCodeFromFile failed with existing file: %v", err)
	}
	if code != expectedCode {
		t.Errorf("Expected code '%s', got '%s'", expectedCode, code)
	}
	if _, errStat := os.Stat(auth.authCodePath); !os.IsNotExist(errStat) {
		t.Error("Auth code file was not deleted after reading")
	}

	if err := os.WriteFile(auth.authCodePath, []byte("   \n  "), 0600); err != nil {
		t.Fatalf("Failed to write empty auth code file: %v", err)
	}
	code, err = auth.readAuthCodeFromFile()
	if err == nil || !strings.Contains(err.Error(), "auth code file was empty") {
		t.Errorf("Expected error for empty auth code file, got %v", err)
	}
	if code != "" {
		t.Error("Code should be empty on error")
	}
	if _, errStat := os.Stat(auth.authCodePath); !os.IsNotExist(errStat) {
		t.Error("Empty auth code file was not deleted")
	}
}

func TestGetAuthorizationCode_FileExists(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	mockTokenDB := &MockTokenManager{}
	auth, cleanup := setupSaxoAuthTest(t, "TestAppAuthCode", server, mockTokenDB)
	defer cleanup()

	auth.rabbitConnection = nil

	expectedCode := "code_from_file_at_start"
	if err := os.WriteFile(auth.authCodePath, []byte(expectedCode), 0600); err != nil {
		t.Fatalf("Failed to write auth code file for test: %v", err)
	}

	resetMockMqTelegran()
	code, err := auth.GetAuthorizationCode()
	if err != nil {
		t.Fatalf("GetAuthorizationCode failed: %v", err)
	}
	if code != expectedCode {
		t.Errorf("Expected code '%s', got '%s'", expectedCode, code)
	}
	mockMqMutex.Lock()
	if mockMqSendCount > 0 {
		t.Error("MQ should not have been called if code was found immediately in file")
	}
	mockMqMutex.Unlock()
}

func TestTokenExpiryChecks(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	mockTokenDB := &MockTokenManager{}
	auth, cleanup := setupSaxoAuthTest(t, "ExpiryTestApp", server, mockTokenDB)
	defer cleanup()

	now := time.Now().UTC()

	t.Run("AccessTokenNotExpired", func(t *testing.T) {
		tokenData := map[string]interface{}{
			"access_token": "valid_token",
			"expires_in":   float64(3600),
			"date_saved":   now.Format(time.RFC3339Nano),
		}
		if auth.isTokenExpired(tokenData) {
			t.Error("Token should not be expired")
		}
	})
	t.Run("AccessTokenExpired", func(t *testing.T) {
		tokenData := map[string]interface{}{
			"access_token": "expired_token",
			"expires_in":   float64(3600),
			"date_saved":   now.Add(-2 * time.Hour).Format(time.RFC3339Nano),
		}
		if !auth.isTokenExpired(tokenData) {
			t.Error("Token should be expired")
		}
	})
	t.Run("AccessTokenMissingFields", func(t *testing.T) {
		if !auth.isTokenExpired(map[string]interface{}{"access_token": "token"}) {
			t.Error("Token should be considered expired if fields are missing")
		}
	})

	t.Run("RefreshTokenNotExpired", func(t *testing.T) {
		tokenData := map[string]interface{}{
			"refresh_token": "valid_refresh",
			"refresh_token_expires_in": float64(90 * 24 * 3600),
			"date_saved": now.Format(time.RFC3339Nano),
		}
		if auth.isRefreshTokenExpired(tokenData) {
			t.Error("Refresh token should not be expired")
		}
	})
	t.Run("RefreshTokenExpired", func(t *testing.T) {
		tokenData := map[string]interface{}{
			"refresh_token": "expired_refresh",
			"refresh_token_expires_in": float64(90 * 24 * 3600),
			"date_saved": now.AddDate(0, -4, 0).Format(time.RFC3339Nano),
		}
		if !auth.isRefreshTokenExpired(tokenData) {
			t.Error("Refresh token should be expired")
		}
	})
}
