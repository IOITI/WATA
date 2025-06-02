package database

import (
	"database/sql"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	// "github.com/google/uuid" // Not strictly needed for tokens if hash is provided
)

// Helper function to get a TokenManager with an in-memory DB for testing
func setupTokenManagerTest(t *testing.T) (*TradingDB, *TokenManager, func()) {
	tdb, cleanupMain := setupTestDB(t)
	tm := NewTokenManager(tdb)

	err := tm.CreateSchemaAuthTokens()
	if err != nil {
		cleanupMain()
		t.Fatalf("Failed to create auth_tokens schema: %v", err)
	}
	
	cleanup := func() {
		// tdb.DB().Exec("DROP TABLE IF EXISTS auth_tokens;")
		cleanupMain()
	}
	return tdb, tm, cleanup
}

func TestTokenManager_CreateSchema(t *testing.T) {
	tdb, tm, cleanup := setupTokenManagerTest(t)
	defer cleanup()

	var tableName string
	err := tdb.DB().QueryRow("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_name = 'auth_tokens';").Scan(&tableName)
	if err != nil {
		if err == sql.ErrNoRows {
			t.Fatal("Table 'auth_tokens' was not created.")
		}
		t.Fatalf("Failed to query for table 'auth_tokens': %v", err)
	}
	if tableName != "auth_tokens" {
		t.Fatalf("Expected 'auth_tokens', got '%s'", tableName)
	}
	err = tm.CreateSchemaAuthTokens() // Idempotency
	if err != nil {
		t.Fatalf("Calling CreateSchemaAuthTokens again failed: %v", err)
	}
}

func sampleAuthTokenData(tokenHash, userID string, expiresAt time.Time, encryptedData []byte) (string, string, []byte, time.Time, string, string, map[string]interface{}) {
	ipAddress := "192.168.1.1"
	userAgent := "TestAgent/1.0"
	metadata := map[string]interface{}{"info": "test_token", "priority": float64(1)} // JSON numbers are float64
	if encryptedData == nil {
		encryptedData = []byte("sample_encrypted_payload")
	}
	return tokenHash, userID, encryptedData, expiresAt, ipAddress, userAgent, metadata
}


func TestTokenManager_StoreAndGetToken(t *testing.T) {
	_, tm, cleanup := setupTokenManagerTest(t)
	defer cleanup()

	tokenHash := "testhash123"
	userID := "userTokenTest1"
	expiresAt := time.Now().UTC().Add(1 * time.Hour).Truncate(time.Millisecond)
	hash, uid, payload, expAt, ip, ua, meta := sampleAuthTokenData(tokenHash, userID, expiresAt, nil)

	err := tm.StoreToken(hash, uid, payload, expAt, ip, ua, meta)
	if err != nil {
		t.Fatalf("StoreToken failed: %v", err)
	}

	retrievedToken, err := tm.GetToken(tokenHash)
	if err != nil {
		t.Fatalf("GetToken failed: %v", err)
	}

	if retrievedToken.TokenHash != tokenHash {
		t.Errorf("TokenHash mismatch: expected %s, got %s", tokenHash, retrievedToken.TokenHash)
	}
	if retrievedToken.UserID != userID {
		t.Errorf("UserID mismatch: expected %s, got %s", userID, retrievedToken.UserID)
	}
	if !reflect.DeepEqual(retrievedToken.EncryptedData, payload) {
		t.Errorf("EncryptedData mismatch: expected %v, got %v", payload, retrievedToken.EncryptedData)
	}
	if !retrievedToken.ExpiresAt.Equal(expiresAt) {
		t.Errorf("ExpiresAt mismatch: expected %v, got %v", expiresAt, retrievedToken.ExpiresAt)
	}
	if retrievedToken.IPAddress.String != ip {
		t.Errorf("IPAddress mismatch: expected %s, got %s", ip, retrievedToken.IPAddress.String)
	}
	if retrievedToken.UserAgent.String != ua {
		t.Errorf("UserAgent mismatch: expected %s, got %s", ua, retrievedToken.UserAgent.String)
	}
	if !reflect.DeepEqual(retrievedToken.Metadata, meta) {
		t.Errorf("Metadata mismatch: expected %v, got %v", meta, retrievedToken.Metadata)
	}
	if !retrievedToken.IsActive {
		t.Error("Token should be active upon creation")
	}
	if retrievedToken.LastUsedAt.Valid { // Should be NULL initially
		t.Errorf("Expected LastUsedAt to be NULL, got %v", retrievedToken.LastUsedAt)
	}

	// Test GetToken for non-existent hash
	_, err = tm.GetToken("nonexistenthash")
	if err == nil {
		t.Error("GetToken should fail for non-existent hash")
	} else if !strings.Contains(err.Error(), "not found") {
		t.Errorf("Unexpected error for non-existent hash: %v", err)
	}
}

func TestTokenManager_StoreToken_Validations(t *testing.T) {
	_, tm, cleanup := setupTokenManagerTest(t)
	defer cleanup()
	
	validHash := "validHash"
	validUser := "validUser"
	validPayload := []byte("payload")
	validExpiry := time.Now().Add(time.Hour)

	testCases := []struct{
		name string
		hash string
		user string
		payload []byte
		expiry time.Time
		expectedErrPart string
	} {
		{"empty hash", "", validUser, validPayload, validExpiry, "token hash cannot be empty"},
		{"empty user", validHash, "", validPayload, validExpiry, "user ID cannot be empty"},
		{"empty payload", validHash, validUser, []byte{}, validExpiry, "encrypted payload cannot be empty"},
		{"nil payload", validHash, validUser, nil, validExpiry, "encrypted payload cannot be empty"},
		{"zero expiry", validHash, validUser, validPayload, time.Time{}, "expires_at must be a future time"},
		{"past expiry", validHash, validUser, validPayload, time.Now().Add(-time.Hour), "expires_at must be a future time"},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T){
			err := tm.StoreToken(tc.hash, tc.user, tc.payload, tc.expiry, "ip", "ua", nil)
			if err == nil {
				t.Fatalf("Expected error for %s, but got nil", tc.name)
			}
			if !strings.Contains(err.Error(), tc.expectedErrPart) {
				t.Errorf("For %s, expected error containing '%s', got '%s'", tc.name, tc.expectedErrPart, err.Error())
			}
		})
	}

	// Test duplicate token hash
	err := tm.StoreToken(validHash, validUser, validPayload, validExpiry, "", "", nil)
	if err != nil {
		t.Fatalf("Initial valid StoreToken failed: %v", err)
	}
	err = tm.StoreToken(validHash, "anotherUser", validPayload, validExpiry, "", "", nil) // Same hash
	if err == nil {
		t.Fatal("StoreToken should fail for duplicate token_hash (PRIMARY KEY constraint)")
	}
	// Error message check for duplicate can be tricky, depends on DB.
	// "UNIQUE constraint failed" or similar. For DuckDB, it might be "Constraint Error"
	t.Logf("Got expected error for duplicate token hash: %v", err)
	if !strings.Contains(strings.ToLower(err.Error()), "unique constraint failed") && !strings.Contains(strings.ToLower(err.Error()), "primary key constraint failed") && !strings.Contains(strings.ToLower(err.Error()), "constraint error"){
         // The wrapper `failed to store token with hash` is also a good check
        if !strings.Contains(err.Error(), fmt.Sprintf("failed to store token with hash %s", validHash)) {
		    t.Errorf("Error message for duplicate token_hash did not contain expected parts. Got: %v", err)
        }
	}
}


func TestTokenManager_TokenExists(t *testing.T) {
	_, tm, cleanup := setupTokenManagerTest(t)
	defer cleanup()

	tokenHash := "testhash_exists"
	userID := "userExists"
	expiresInFuture := time.Now().UTC().Add(1 * time.Hour)
	expiresInPast := time.Now().UTC().Add(-1 * time.Hour)

	// Store an active, non-expired token
	tm.StoreToken(tokenHash, userID, []byte("data"), expiresInFuture, "", "", nil)

	exists, err := tm.TokenExists(tokenHash)
	if err != nil {
		t.Fatalf("TokenExists failed for active token: %v", err)
	}
	if !exists {
		t.Error("Expected active token to exist, but it doesn't")
	}

	// Test non-existent token
	exists, err = tm.TokenExists("nonexistent_hash")
	if err != nil {
		t.Fatalf("TokenExists failed for non-existent token: %v", err)
	}
	if exists {
		t.Error("Expected non-existent token not to exist, but it does")
	}

	// Store an expired token
	expiredTokenHash := "testhash_expired"
	tm.StoreToken(expiredTokenHash, userID, []byte("data"), expiresInPast, "", "", nil)
	exists, err = tm.TokenExists(expiredTokenHash)
	if err != nil {
		t.Fatalf("TokenExists failed for expired token: %v", err)
	}
	if exists {
		t.Error("Expected expired token not to exist (as per TokenExists logic), but it does")
	}

	// Store an inactive token
	inactiveTokenHash := "testhash_inactive"
	tm.StoreToken(inactiveTokenHash, userID, []byte("data"), expiresInFuture, "", "", nil)
	// Manually set inactive for test (DeleteToken also does this)
	_, err = tm.tdb.DB().Exec("UPDATE auth_tokens SET is_active = FALSE WHERE token_hash = ?", inactiveTokenHash)
	if err != nil {
		t.Fatalf("Failed to manually set token inactive: %v", err)
	}
	exists, err = tm.TokenExists(inactiveTokenHash)
	if err != nil {
		t.Fatalf("TokenExists failed for inactive token: %v", err)
	}
	if exists {
		t.Error("Expected inactive token not to exist (as per TokenExists logic), but it does")
	}
}

func TestTokenManager_DeleteToken(t *testing.T) {
	_, tm, cleanup := setupTokenManagerTest(t)
	defer cleanup()

	tokenHash := "testhash_delete"
	tm.StoreToken(tokenHash, "userDel", []byte("data"), time.Now().Add(time.Hour), "", "", nil)

	err := tm.DeleteToken(tokenHash)
	if err != nil {
		t.Fatalf("DeleteToken failed: %v", err)
	}

	// Verify it's marked as inactive
	var isActive bool
	err = tm.tdb.DB().QueryRow("SELECT is_active FROM auth_tokens WHERE token_hash = ?", tokenHash).Scan(&isActive)
	if err != nil {
		t.Fatalf("Failed to query is_active after DeleteToken: %v", err)
	}
	if isActive {
		t.Error("Token should be inactive after DeleteToken, but it's active")
	}

	// Try to delete non-existent token
	err = tm.DeleteToken("nonexistent_hash_del")
	if err == nil {
		t.Error("DeleteToken should fail for non-existent token")
	} else if !strings.Contains(err.Error(), "no token found") {
		t.Errorf("Unexpected error for deleting non-existent token: %v", err)
	}
}

func TestTokenManager_UpdateMetadata(t *testing.T) {
	_, tm, cleanup := setupTokenManagerTest(t)
	defer cleanup()

	tokenHash := "testhash_metadata"
	initialMeta := map[string]interface{}{"key1": "value1", "num": float64(10)}
	tm.StoreToken(tokenHash, "userMeta", []byte("data"), time.Now().Add(time.Hour), "", "", initialMeta)

	newMeta := map[string]interface{}{"key2": "value2", "updated": true, "num": float64(20)}
	err := tm.UpdateMetadata(tokenHash, newMeta)
	if err != nil {
		t.Fatalf("UpdateMetadata failed: %v", err)
	}

	retrievedToken, err := tm.GetToken(tokenHash)
	if err != nil {
		t.Fatalf("GetToken failed after UpdateMetadata: %v", err)
	}
	if !reflect.DeepEqual(retrievedToken.Metadata, newMeta) {
		t.Errorf("Metadata mismatch: expected %v, got %v", newMeta, retrievedToken.Metadata)
	}

	// Test update with empty metadata (should store NULL or empty JSON)
	emptyMeta := make(map[string]interface{})
	err = tm.UpdateMetadata(tokenHash, emptyMeta)
	if err != nil {
		t.Fatalf("UpdateMetadata with empty map failed: %v", err)
	}
	retrievedTokenEmpty, err := tm.GetToken(tokenHash)
	if err != nil {
		t.Fatalf("GetToken failed after UpdateMetadata with empty map: %v", err)
	}
	if len(retrievedTokenEmpty.Metadata) != 0 { // Check if it's an empty map
		t.Errorf("Expected empty metadata, got %v", retrievedTokenEmpty.Metadata)
	}
	// Verify actual DB value (should be NULL or '{}')
	var metadataJSON sql.NullString
	err = tm.tdb.DB().QueryRow("SELECT metadata FROM auth_tokens WHERE token_hash = ?", tokenHash).Scan(&metadataJSON)
	if err != nil { t.Fatalf("DB query for metadata failed: %v", err) }
	if metadataJSON.Valid && metadataJSON.String != "{}" && metadataJSON.String != "" { // DuckDB might store "{}" for empty map if not NULL
		// If metadataArg was nil, it should be NULL in DB
		// If metadataArg was marshalled empty map, it might be "{}"
		// The StoreToken/UpdateMetadata passes nil if map is empty. So DB should be NULL.
		t.Errorf("Expected DB metadata to be NULL or empty JSON, got '%s'", metadataJSON.String)
	}


	// Test update non-existent token
	err = tm.UpdateMetadata("nonexistent_hash_meta", newMeta)
	if err == nil {
		t.Error("UpdateMetadata should fail for non-existent token")
	} else if !strings.Contains(err.Error(), "no token found") {
		t.Errorf("Unexpected error for updating metadata of non-existent token: %v", err)
	}
}

func TestTokenManager_UpdateLastUsed(t *testing.T) {
	_, tm, cleanup := setupTokenManagerTest(t)
	defer cleanup()

	tokenHash := "testhash_lastused"
	tm.StoreToken(tokenHash, "userLastUsed", []byte("data"), time.Now().Add(time.Hour), "", "", nil)

	// Get initial state
	tokenBefore, err := tm.GetToken(tokenHash)
	if err != nil { t.Fatalf("GetToken failed: %v", err) }
	if tokenBefore.LastUsedAt.Valid {
		t.Fatalf("LastUsedAt should be NULL initially, got %v", tokenBefore.LastUsedAt.Time)
	}

	// Small delay to ensure timestamp changes
	time.Sleep(10 * time.Millisecond) 
	
	err = tm.UpdateLastUsed(tokenHash)
	if err != nil {
		t.Fatalf("UpdateLastUsed failed: %v", err)
	}

	tokenAfter, err := tm.GetToken(tokenHash)
	if err != nil { t.Fatalf("GetToken after UpdateLastUsed failed: %v", err) }
	
	if !tokenAfter.LastUsedAt.Valid {
		t.Fatal("LastUsedAt should be valid after update")
	}
	if tokenAfter.LastUsedAt.Time.Before(tokenBefore.CreatedAt) || tokenAfter.LastUsedAt.Time.Equal(tokenBefore.CreatedAt) {
		t.Errorf("Expected LastUsedAt (%v) to be after CreatedAt (%v)", tokenAfter.LastUsedAt.Time, tokenBefore.CreatedAt)
	}

	// Test on inactive token: UpdateLastUsed should not modify LastUsedAt if token is inactive.
	inactiveHash := "testhash_lastused_inactive"
	tm.StoreToken(inactiveHash, "userInactiveLU", []byte("data"), time.Now().Add(time.Hour), "", "", nil)

	// Use it once while active
	err = tm.UpdateLastUsed(inactiveHash)
	if err != nil {
		t.Fatalf("First UpdateLastUsed for inactiveHash failed: %v", err)
	}
	tokenBeforeInactive, err := tm.GetToken(inactiveHash)
	if err != nil {
		t.Fatalf("GetToken for inactiveHash before deactivation failed: %v", err)
	}
	if !tokenBeforeInactive.LastUsedAt.Valid {
		t.Fatal("LastUsedAt should be valid after first use.")
	}
	lastUsedTimestampBeforeInactive := tokenBeforeInactive.LastUsedAt.Time

	// Deactivate the token
	tm.DeleteToken(inactiveHash) 
	
	// Try to update last_used again (should not happen)
	// Wait a bit to ensure timestamp would be different if updated
	time.Sleep(10 * time.Millisecond)
	err = tm.UpdateLastUsed(inactiveHash) 
	if err != nil {
		t.Fatalf("Second UpdateLastUsed (on inactive token) failed unexpectedly: %v", err)
	}
	
	tokenAfterInactiveUpdateAttempt, err := tm.GetToken(inactiveHash)
	if err != nil { 
		t.Fatalf("GetToken for inactiveHash after deactivation and update attempt failed: %v", err) 
	}

	if !tokenAfterInactiveUpdateAttempt.LastUsedAt.Valid {
		t.Errorf("LastUsedAt should still be valid for inactive token if it was used before, got NULL")
	} else if !tokenAfterInactiveUpdateAttempt.LastUsedAt.Time.Equal(lastUsedTimestampBeforeInactive) {
		t.Errorf("LastUsedAt for inactive token was modified after deactivation. Expected %v, got %v",
			lastUsedTimestampBeforeInactive, tokenAfterInactiveUpdateAttempt.LastUsedAt.Time)
	}
}

// Helper to ensure JSON numbers are compared correctly (Go unmarshals all numbers to float64)
func TestTokenManager_MetadataJsonNumbers(t *testing.T) {
	_, tm, cleanup := setupTokenManagerTest(t)
	defer cleanup()

	tokenHash := "jsonNumTest"
	userID := "userJsonNum"
	expiresAt := time.Now().Add(time.Hour)
	payload := []byte("data")
	
	// Metadata with integer and float
	meta := map[string]interface{}{
		"integer_val": 100,          // This will become float64(100) when unmarshalled
		"float_val":   100.5,
		"string_val":  "hello",
	}
	
	err := tm.StoreToken(tokenHash, userID, payload, expiresAt, "", "", meta)
	if err != nil {
		t.Fatalf("StoreToken failed: %v", err)
	}

	retrievedToken, err := tm.GetToken(tokenHash)
	if err != nil {
		t.Fatalf("GetToken failed: %v", err)
	}

	expectedMeta := map[string]interface{}{
		"integer_val": float64(100), // JSON unmarshalling turns numbers into float64
		"float_val":   float64(100.5),
		"string_val":  "hello",
	}

	if !reflect.DeepEqual(retrievedToken.Metadata, expectedMeta) {
		t.Errorf("Metadata (numbers) mismatch:\nExpected: %+v (types: %T, %T)\nGot:      %+v (types: %T, %T)", 
			expectedMeta, expectedMeta["integer_val"], expectedMeta["float_val"],
			retrievedToken.Metadata, retrievedToken.Metadata["integer_val"], retrievedToken.Metadata["float_val"])

		// More detailed check
		for k, expectedV := range expectedMeta {
			actualV, ok := retrievedToken.Metadata[k]
			if !ok {
				t.Errorf("Key %s missing in retrieved metadata", k)
				continue
			}
			if !reflect.DeepEqual(actualV, expectedV) {
				t.Errorf("Mismatch for key %s: expected %v (type %T), got %v (type %T)", k, expectedV, expectedV, actualV, actualV)
			}
		}
	}
}
