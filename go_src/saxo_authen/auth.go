package saxo_authen

import (
	"crypto/cipher"
	"crypto/rand"
	// "crypto/sha256" // No longer needed after changing token key to AppName
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"pymath/go_src/configuration"
	"pymath/go_src/database"
	"pymath/go_src/mq_telegram"
	"pymath/go_src/trade_exceptions"

	amqp "github.com/rabbitmq/amqp091-go"
	"github.com/sirupsen/logrus"
)

const (
	defaultTokenFileName = "saxo_token.json.enc"
	defaultAuthCodeFileName = "saxo_auth_code.txt"
	defaultSaltFileName  = "secret.salt"
	pollInterval         = 2 * time.Second
	pollTimeout          = 2 * time.Minute
)

type SaxoAppConfig struct {
	AppName      string `json:"AppName"`
	AppKey       string `json:"AppKey"`
	AppSecret    string `json:"AppSecret"`
	AuthURL      string `json:"AuthUrl"`
	TokenURL     string `json:"TokenUrl"`
	RedirectURL  string `json:"RedirectUrls"`
	CodeVerifier string `json:"CodeVerifier"`
}

type TokenManagerInterface interface {
	StoreToken(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error
	GetToken(tokenKey string) (*database.AuthTokenData, error)
}

type SaxoAuth struct {
	config           *configuration.Config
	tokenDB          TokenManagerInterface
	appConfig        SaxoAppConfig
	tokenFilePath    string
	authCodePath     string
	saltFilePath     string
	state            string
	aead             cipher.AEAD
	rabbitConnection *amqp.Connection
	httpClient       *http.Client
	GetTokenOverride func() (string, error) // For testing: if set, GetToken calls this.
}

func NewSaxoAuth(appCfg SaxoAppConfig, tokenDirPath string, tokenDB TokenManagerInterface, rabbitConn *amqp.Connection) (*SaxoAuth, error) {
	if appCfg.AppKey == "" || appCfg.AppSecret == "" || appCfg.AuthURL == "" || appCfg.TokenURL == "" || appCfg.RedirectURL == "" {
		return nil, fmt.Errorf("missing required fields in SaxoAppConfig (AppKey, AppSecret, AuthUrl, TokenUrl, RedirectUrls)")
	}
	if appCfg.AppName == "" {
		return nil, errors.New("SaxoAppConfig.AppName cannot be empty")
	}
	if tokenDirPath == "" {
		return nil, errors.New("tokenDirPath cannot be empty")
	}
	if err := os.MkdirAll(tokenDirPath, 0700); err != nil {
        return nil, fmt.Errorf("failed to create token directory '%s': %w", tokenDirPath, err)
    }
	if tokenDB == nil {
		logrus.Warnf("SaxoAuth initialized for app '%s' with a nil TokenManagerInterface. Database operations for tokens will fail.", appCfg.AppName)
	}

	authCodeFileName := fmt.Sprintf("%s_%s", appCfg.AppName, defaultAuthCodeFileName)
	authCodePath := filepath.Join(tokenDirPath, authCodeFileName)

	saltFileName := fmt.Sprintf("%s_%s", appCfg.AppName, defaultSaltFileName)
	saltFilePath := filepath.Join(tokenDirPath, saltFileName)

	tokenFileName := fmt.Sprintf("%s_%s", appCfg.AppName, defaultTokenFileName)
	tokenFilePath := filepath.Join(tokenDirPath, tokenFileName)

	aead, err := NewCipher(appCfg.AppSecret, saltFilePath)
	if err != nil {
		return nil, fmt.Errorf("failed to initialize cipher: %w", err)
	}

	randomBytes := make([]byte, 16)
	if _, err := rand.Read(randomBytes); err != nil {
		return nil, fmt.Errorf("failed to generate random bytes for state: %w", err)
	}
	state := hex.EncodeToString(randomBytes)

	return &SaxoAuth{
		tokenDB:          tokenDB,
		appConfig:        appCfg,
		tokenFilePath:    tokenFilePath,
		authCodePath:     authCodePath,
		saltFilePath:     saltFilePath,
		state:            state,
		aead:             aead,
		rabbitConnection: rabbitConn,
		httpClient:       &http.Client{Timeout: 30 * time.Second},
	}, nil
}

func (s *SaxoAuth) SetTokenDB(tokenDB TokenManagerInterface) {
	s.tokenDB = tokenDB
}

func (s *SaxoAuth) loadTokenDataFromDB() (map[string]interface{}, error) {
	if s.tokenDB == nil { return nil, errors.New("TokenDB not initialized") }

	appSpecificTokenKey := s.appConfig.AppName
	authTokenData, err := s.tokenDB.GetToken(appSpecificTokenKey)

	if err != nil {
		if strings.Contains(strings.ToLower(err.Error()), "not found") || strings.Contains(strings.ToLower(err.Error()), "no rows") {
			logrus.Debugf("No token found in DB for key (AppName) '%s'", appSpecificTokenKey)
			return nil, nil
		}
		return nil, fmt.Errorf("error retrieving token from DB for app %s (key: %s): %w", s.appConfig.AppName, appSpecificTokenKey, err)
	}
	if authTokenData == nil || len(authTokenData.EncryptedData) == 0 {
		logrus.Debugf("Token data for key '%s' is nil or has empty encrypted data.", appSpecificTokenKey)
		return nil, nil
	}

	decryptedJSON, err := Decrypt(s.aead, authTokenData.EncryptedData)
	if err != nil {
		return nil, fmt.Errorf("failed to decrypt token from DB: %w", err)
	}

	var tokenData map[string]interface{}
	if err := json.Unmarshal(decryptedJSON, &tokenData); err != nil {
		return nil, fmt.Errorf("failed to unmarshal decrypted token from DB: %w", err)
	}
	return tokenData, nil
}

func (s *SaxoAuth) GetAuthorizationURL() (string, error) {
	params := url.Values{}
	params.Set("response_type", "code")
	params.Set("client_id", s.appConfig.AppKey)
	params.Set("state", s.state)
	params.Set("redirect_uri", s.appConfig.RedirectURL)
	return fmt.Sprintf("%s?%s", s.appConfig.AuthURL, params.Encode()), nil
}

func (s *SaxoAuth) readAuthCodeFromFile() (string, error) {
	if _, err := os.Stat(s.authCodePath); os.IsNotExist(err) {
		return "", nil
	} else if err != nil {
		return "", fmt.Errorf("error checking auth code file %s: %w", s.authCodePath, err)
	}
	authCodeBytes, err := os.ReadFile(s.authCodePath)
	if err != nil {
		return "", fmt.Errorf("failed to read auth code from file %s: %w", s.authCodePath, err)
	}
	if err := os.Remove(s.authCodePath); err != nil {
		logrus.Warnf("Failed to delete auth code file %s: %v", s.authCodePath, err)
	}
	authCode := strings.TrimSpace(string(authCodeBytes))
	if authCode == "" {
		return "", errors.New("auth code file was empty")
	}
	return authCode, nil
}

func (s *SaxoAuth) GetAuthorizationCode() (string, error) {
	authCode, err := s.readAuthCodeFromFile()
	if err != nil {
		logrus.Errorf("Error reading auth code file initially: %v", err)
	}
	if authCode != "" {
		logrus.Infof("Authorization code successfully read from file: %s", s.authCodePath)
		return authCode, nil
	}

	authURL, err := s.GetAuthorizationURL()
	if err != nil {
		return "", fmt.Errorf("failed to generate authorization URL: %w", err)
	}

	message := fmt.Sprintf("ACTION REQUIRED: Saxo authentication needed for app '%s'.\n"+
		"1. Click the link below to authorize.\n"+
		"2. After authorization, Saxo will redirect you to a URL.\n"+
		"3. Copy the ENTIRE redirect URL.\n"+
		"4. Paste it into the %s CLI prompt.\n\n"+
		"Authorization URL: %s\n\n"+
		"Alternatively, if using the CLI tool `watasaxoauth`, it will prompt you for the code/URL.",
		s.appConfig.AppName, s.appConfig.AppName, authURL)

	logrus.Info("Sending Telegram notification for Saxo authentication...")
	if s.rabbitConnection != nil && !s.rabbitConnection.IsClosed() {
		errMq := mq_telegram.SendMessageToMQForTelegram(s.rabbitConnection, message)
		if errMq != nil {
			logrus.Errorf("Failed to send Saxo auth notification to Telegram via MQ: %v", errMq)
		} else {
			logrus.Info("Saxo auth notification sent to Telegram MQ successfully.")
		}
	} else {
		logrus.Warn("RabbitMQ connection not available or closed. Skipping Telegram notification for Saxo auth.")
	}
	fmt.Println("\n==============================================================================")
	fmt.Println("ACTION REQUIRED: Saxo Authentication")
	fmt.Printf("Application: %s\n", s.appConfig.AppName)
	fmt.Println("1. Open the following URL in your browser:")
	fmt.Printf("   %s\n", authURL)
	fmt.Println("2. Authorize the application.")
	fmt.Println("3. Saxo Bank will redirect you to a URL. Copy that ENTIRE URL.")
	fmt.Printf("4. The application is waiting for you to provide this URL/code via the `watasaxoauth` CLI tool or by creating the auth code file.\n")
	fmt.Printf("   Auth code file expected at: %s\n", s.authCodePath)
	fmt.Println("==============================================================================")

	logrus.Infof("Waiting for authorization code to appear in file: %s (Timeout: %v)", s.authCodePath, pollTimeout)
	startTime := time.Now()
	for {
		authCode, errFileRead := s.readAuthCodeFromFile()
		if errFileRead != nil {
			logrus.Warnf("Error reading auth code file during polling: %v", errFileRead)
		}
		if authCode != "" {
			logrus.Infof("Authorization code found in file after polling.")
			return authCode, nil
		}
		if time.Since(startTime) > pollTimeout {
			return "", fmt.Errorf("timeout waiting for authorization code file %s", s.authCodePath)
		}
		time.Sleep(pollInterval)
	}
}

func (s *SaxoAuth) exchangeCodeForToken(code string) (map[string]interface{}, error) {
	payload := url.Values{}
	payload.Set("grant_type", "authorization_code")
	payload.Set("code", code)
	payload.Set("redirect_uri", s.appConfig.RedirectURL)
	if s.appConfig.CodeVerifier != "" {
		payload.Set("code_verifier", s.appConfig.CodeVerifier)
	}

	req, err := http.NewRequest("POST", s.appConfig.TokenURL, strings.NewReader(payload.Encode()))
	if err != nil {
		return nil, fmt.Errorf("failed to create token request: %w", err)
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.SetBasicAuth(s.appConfig.AppKey, s.appConfig.AppSecret)

	logrus.Debugf("Exchanging auth code for token. URL: %s, Payload: %s", s.appConfig.TokenURL, payload.Encode())

	resp, err := s.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("token request failed: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read token response body: %w", err)
	}
	logrus.Debugf("Token exchange response status: %s, body: %s", resp.Status, string(body))

	if resp.StatusCode != http.StatusOK {
		var saxoErr trade_exceptions.SaxoApiError
		if json.Unmarshal(body, &saxoErr) == nil && len(saxoErr.SaxoErrorDetails) > 0 {
			saxoErr.StatusCode = resp.StatusCode
			return nil, &saxoErr
		}
		return nil, fmt.Errorf("token exchange failed: status %s, body: %s", resp.Status, string(body))
	}
	var tokenData map[string]interface{}
	if err := json.Unmarshal(body, &tokenData); err != nil {
		return nil, fmt.Errorf("failed to unmarshal token response: %w. Body: %s", err, string(body))
	}
	return tokenData, nil
}

func (s *SaxoAuth) refreshToken(existingRefreshToken string) (map[string]interface{}, error) {
	if existingRefreshToken == "" {
		return nil, errors.New("existing refresh token is empty")
	}
	payload := url.Values{}
	payload.Set("grant_type", "refresh_token")
	payload.Set("refresh_token", existingRefreshToken)

	req, err := http.NewRequest("POST", s.appConfig.TokenURL, strings.NewReader(payload.Encode()))
	if err != nil {
		return nil, fmt.Errorf("failed to create refresh token request: %w", err)
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.SetBasicAuth(s.appConfig.AppKey, s.appConfig.AppSecret)
	logrus.Debugf("Refreshing token. URL: %s", s.appConfig.TokenURL)

	resp, err := s.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("refresh token request failed: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read refresh token response body: %w", err)
	}
	logrus.Debugf("Refresh token response status: %s, body: %s", resp.Status, string(body))

	if resp.StatusCode != http.StatusOK {
		var saxoErr trade_exceptions.SaxoApiError
		if json.Unmarshal(body, &saxoErr) == nil && len(saxoErr.SaxoErrorDetails) > 0 {
			saxoErr.StatusCode = resp.StatusCode
			return nil, &saxoErr
		}
		return nil, fmt.Errorf("refresh token failed: status %s, body: %s", resp.Status, string(body))
	}
	var tokenData map[string]interface{}
	if err := json.Unmarshal(body, &tokenData); err != nil {
		return nil, fmt.Errorf("failed to unmarshal refresh token response: %w. Body: %s", err, string(body))
	}
	return tokenData, nil
}

func (s *SaxoAuth) askNewToken() (map[string]interface{}, error) {
	logrus.Info("Attempting to get a new authorization code.")
	authCode, err := s.GetAuthorizationCode()
	if err != nil {
		return nil, fmt.Errorf("failed to get authorization code: %w", err)
	}
	if authCode == "" {
		return nil, errors.New("authorization code is empty though no error reported")
	}
	logrus.Info("Exchanging authorization code for token.")
	tokenData, err := s.exchangeCodeForToken(authCode)
	if err != nil {
		return nil, fmt.Errorf("failed to exchange authorization code for token: %w", err)
	}
	return tokenData, nil
}

func (s *SaxoAuth) saveTokenData(tokenData map[string]interface{}) error {
	if s.tokenDB == nil {
		return errors.New("TokenDB not initialized, cannot save token to database")
	}
	tokenData["date_saved"] = time.Now().UTC().Format(time.RFC3339Nano)
	jsonData, err := json.Marshal(tokenData)
	if err != nil {
		return fmt.Errorf("failed to marshal token data to JSON: %w", err)
	}
	encryptedData, err := Encrypt(s.aead, jsonData)
	if err != nil {
		return fmt.Errorf("failed to encrypt token data: %w", err)
	}

	accessToken, okAt := tokenData["access_token"].(string)
	if !okAt || accessToken == "" {
		return errors.New("access_token not found or invalid in token data")
	}
	var expiresAtTime time.Time
	if expiresInVal, ok := tokenData["expires_in"].(float64); ok {
		expiresAtTime = time.Now().UTC().Add(time.Duration(expiresInVal) * time.Second)
	} else {
		logrus.Warn("expires_in not found or not a number in token data for DB expiry, token might not auto-expire correctly in DB record.")
	}

	appSpecificTokenKey := s.appConfig.AppName
	metadataForDB := map[string]interface{}{
		"type":        "saxo_oauth_token",
		"appName":     s.appConfig.AppName,
		"accessToken": accessToken[:min(16, len(accessToken))] + "...",
	}

	err = s.tokenDB.StoreToken(appSpecificTokenKey, s.appConfig.AppName, encryptedData, expiresAtTime, "", "", metadataForDB)
	if err != nil {
		return fmt.Errorf("failed to store token in database (key: %s): %w", appSpecificTokenKey, err)
	}
	logrus.Infof("Token data saved to database for app: %s (key: %s)", s.appConfig.AppName, appSpecificTokenKey)

	if s.tokenFilePath != "" {
		if err := ensureDirExists(s.tokenFilePath); err != nil {
			logrus.Errorf("Failed to ensure directory for legacy token file %s: %v", s.tokenFilePath, err)
		} else {
			if err := os.WriteFile(s.tokenFilePath, encryptedData, 0600); err != nil {
				logrus.Errorf("Failed to save token to legacy file %s: %v", s.tokenFilePath, err)
			} else {
				logrus.Infof("Token data also saved to legacy file: %s", s.tokenFilePath)
			}
		}
	}
	return nil
}

// func parseTimeFlexible(t interface{}) (time.Time, bool) { ... } // Currently unused

func (s *SaxoAuth) isTokenExpired(tokenData map[string]interface{}) bool {
	expiresIn, okEI := tokenData["expires_in"].(float64)
	dateSavedStr, okDS := tokenData["date_saved"].(string)
	if !okEI || !okDS {
		logrus.Warn("Token data missing 'expires_in' or 'date_saved', assuming expired.")
		return true
	}
	dateSaved, err := time.Parse(time.RFC3339Nano, dateSavedStr)
	if err != nil {
		logrus.Warnf("Failed to parse 'date_saved' timestamp '%s', assuming expired: %v", dateSavedStr, err)
		return true
	}
	expiryTime := dateSaved.Add(time.Duration(expiresIn) * time.Second)
	return expiryTime.Before(time.Now().UTC().Add(1 * time.Minute))
}

func (s *SaxoAuth) isRefreshTokenExpired(tokenData map[string]interface{}) bool {
	rtExpiresIn, okEI := tokenData["refresh_token_expires_in"].(float64)
	dateSavedStr, okDS := tokenData["date_saved"].(string)
	if !okEI || !okDS {
		logrus.Warn("Token data missing 'refresh_token_expires_in' or 'date_saved' for refresh token, assuming expired.")
		return true
	}
	dateSaved, err := time.Parse(time.RFC3339Nano, dateSavedStr)
	if err != nil {
		logrus.Warnf("Failed to parse 'date_saved' timestamp '%s' for refresh token, assuming expired: %v", dateSavedStr, err)
		return true
	}
	expiryTime := dateSaved.Add(time.Duration(rtExpiresIn) * time.Second)
	return expiryTime.Before(time.Now().UTC().Add(1 * time.Hour))
}

// GetToken is the main public method. If GetTokenOverride is set, it calls that.
// Otherwise, it calls the default token retrieval logic.
func (s *SaxoAuth) GetToken() (string, error) {
	if s.GetTokenOverride != nil {
		return s.GetTokenOverride()
	}
	return s.getTokenInternal()
}

// getTokenInternal contains the actual logic for GetToken.
func (s *SaxoAuth) getTokenInternal() (string, error) {
	if s.tokenDB == nil {
		return "", errors.New("TokenDB not initialized in SaxoAuth. Call SetTokenDB or ensure it's passed during construction.")
	}

	tokenData, err := s.loadTokenDataFromDB()
	if err != nil {
		logrus.Warnf("Failed to load token data from DB: %v. Will attempt to get new token.", err)
		tokenData = nil
	}

	if tokenData != nil {
		logrus.Info("Token data loaded from DB. Checking validity...")
		if !s.isTokenExpired(tokenData) {
			accessToken, ok := tokenData["access_token"].(string)
			if ok && accessToken != "" {
				logrus.Info("Access token is valid.")
				return accessToken, nil
			}
			logrus.Warn("Access token in DB data is missing or invalid, proceeding to refresh/new.")
		} else {
			logrus.Info("Access token expired. Attempting to refresh.")
			refreshToken, okRf := tokenData["refresh_token"].(string)
			if !okRf || refreshToken == "" {
				logrus.Warn("No refresh token available in DB data. Cannot refresh.")
			} else if s.isRefreshTokenExpired(tokenData) {
				logrus.Warn("Refresh token also expired.")
			} else {
				refreshedTokenData, refreshErr := s.refreshToken(refreshToken)
				if refreshErr != nil {
					logrus.Warnf("Failed to refresh token: %v. Will attempt to get a new token.", refreshErr)
				} else {
					logrus.Info("Token refreshed successfully.")
					if errSave := s.saveTokenData(refreshedTokenData); errSave != nil {
						logrus.Errorf("Failed to save refreshed token data: %v", errSave)
					}
					newAccessToken, ok := refreshedTokenData["access_token"].(string)
					if ok && newAccessToken != "" {
						return newAccessToken, nil
					}
					logrus.Error("Refreshed token data does not contain a valid access_token.")
				}
			}
		}
	} else {
		logrus.Info("No existing token data found in DB.")
	}

	logrus.Info("Requesting a new token.")
	newTokenData, newErr := s.askNewToken()
	if newErr != nil {
		return "", fmt.Errorf("failed to obtain a new token: %w", newErr)
	}

	logrus.Info("New token obtained successfully.")
	if errSave := s.saveTokenData(newTokenData); errSave != nil {
		logrus.Errorf("Failed to save new token data: %v", errSave)
	}

	finalAccessToken, ok := newTokenData["access_token"].(string)
	if !ok || finalAccessToken == "" {
		return "", errors.New("newly obtained token data does not contain a valid access_token")
	}
	return finalAccessToken, nil
}

// Helper for snippet
func min(a, b int) int {
    if a < b {
        return a
    }
    return b
}
