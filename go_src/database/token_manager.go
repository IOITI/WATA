package database

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"time"
)

// TokenManager handles operations for the auth_tokens table.
type TokenManager struct {
	tdb *TradingDB
}

// NewTokenManager creates a new TokenManager.
func NewTokenManager(tdb *TradingDB) *TokenManager {
	return &TokenManager{tdb: tdb}
}

// CreateSchemaAuthTokens creates the auth_tokens table.
func (tm *TokenManager) CreateSchemaAuthTokens() error {
	schema := `
	CREATE TABLE IF NOT EXISTS auth_tokens (
		token_hash VARCHAR PRIMARY KEY, -- Hash of the token for quick lookup
		user_id VARCHAR NOT NULL,
		encrypted_data BLOB NOT NULL,    -- Encrypted token payload
		created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
		expires_at TIMESTAMP NOT NULL,
		last_used_at TIMESTAMP,
		ip_address VARCHAR,
		user_agent VARCHAR,
		metadata JSON, -- Store additional non-sensitive info as JSON
		is_active BOOLEAN DEFAULT TRUE,
		updated_at TIMESTAMP
	);`
	// DuckDB supports JSON type.
	// BLOB type is for binary data.
	_, err := tm.tdb.DB().Exec(schema)
	if err != nil {
		return fmt.Errorf("failed to create auth_tokens schema: %w", err)
	}
	return nil
}

// AuthTokenData represents the data stored for an authentication token.
type AuthTokenData struct {
	TokenHash     string                 `json:"token_hash"` // Not stored in encrypted_data, but used as key
	UserID        string                 `json:"user_id"`
	EncryptedData []byte                 `json:"encrypted_data"` // Actual encrypted payload
	CreatedAt     time.Time              `json:"created_at"`
	ExpiresAt     time.Time              `json:"expires_at"`
	LastUsedAt    sql.NullTime           `json:"last_used_at"`
	IPAddress     sql.NullString         `json:"ip_address"`
	UserAgent     sql.NullString         `json:"user_agent"`
	Metadata      map[string]interface{} `json:"metadata"` // For flexible additional data
	IsActive      bool                   `json:"is_active"`
}

// StoreToken stores a new token in the database.
// tokenHash is the hashed version of the raw token.
// encryptedPayload is the actual token data, encrypted.
func (tm *TokenManager) StoreToken(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error {
	if tokenHash == "" {
		return fmt.Errorf("token hash cannot be empty")
	}
	if userID == "" {
		return fmt.Errorf("user ID cannot be empty")
	}
	if len(encryptedPayload) == 0 {
		return fmt.Errorf("encrypted payload cannot be empty")
	}
	if expiresAt.IsZero() || expiresAt.Before(time.Now()) {
		return fmt.Errorf("expires_at must be a future time")
	}

	metadataJSON, err := json.Marshal(metadata)
	if err != nil {
		return fmt.Errorf("failed to marshal metadata to JSON: %w", err)
	}
	// Use NULL for metadata if it's empty, instead of "null" string or empty object string
	var metadataArg interface{}
	if len(metadata) > 0 {
		metadataArg = metadataJSON
	} else {
		metadataArg = nil // Store database NULL
	}


	query := `
	INSERT INTO auth_tokens (
		token_hash, user_id, encrypted_data, expires_at, ip_address, user_agent, metadata, created_at, last_used_at, is_active
	) VALUES ($1, $2, $3, $4, $5, $6, $7, CURRENT_TIMESTAMP, NULL, TRUE);`

	_, err = tm.tdb.DB().Exec(query,
		tokenHash,
		userID,
		encryptedPayload,
		expiresAt,
		sql.NullString{String: ipAddress, Valid: ipAddress != ""},
		sql.NullString{String: userAgent, Valid: userAgent != ""},
		metadataArg,
	)
	if err != nil {
		return fmt.Errorf("failed to store token with hash %s: %w", tokenHash, err)
	}
	return nil
}

// GetToken retrieves token data (excluding the encrypted part for this example, but could include it)
// and updates last_used_at.
// A real GetToken would likely validate expiry and active status more strictly here or in the caller.
func (tm *TokenManager) GetToken(tokenHash string) (*AuthTokenData, error) {
	query := `
	SELECT user_id, encrypted_data, created_at, expires_at, last_used_at, ip_address, user_agent, metadata, is_active
	FROM auth_tokens
	WHERE token_hash = $1;`

	var data AuthTokenData
	data.TokenHash = tokenHash
	var metadataJSON sql.NullString // Fetch JSON as string then unmarshal

	err := tm.tdb.DB().QueryRow(query, tokenHash).Scan(
		&data.UserID,
		&data.EncryptedData,
		&data.CreatedAt,
		&data.ExpiresAt,
		&data.LastUsedAt,
		&data.IPAddress,
		&data.UserAgent,
		&metadataJSON,
		&data.IsActive,
	)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, fmt.Errorf("token with hash %s not found", tokenHash)
		}
		return nil, fmt.Errorf("failed to get token with hash %s: %w", tokenHash, err)
	}

	if metadataJSON.Valid && metadataJSON.String != "" {
		err = json.Unmarshal([]byte(metadataJSON.String), &data.Metadata)
		if err != nil {
			return nil, fmt.Errorf("failed to unmarshal metadata for token %s: %w", tokenHash, err)
		}
	} else {
		data.Metadata = make(map[string]interface{}) // Ensure it's not nil
	}
	
	// Optionally, update last_used_at, but this means GetToken modifies state.
	// Consider a separate UpdateLastUsed method if preferred.
	// For now, let's not update it here to keep GetToken read-only for the main data.

	return &data, nil
}

// TokenExists checks if a token with the given hash exists and is active.
func (tm *TokenManager) TokenExists(tokenHash string) (bool, error) {
	query := "SELECT EXISTS (SELECT 1 FROM auth_tokens WHERE token_hash = $1 AND is_active = TRUE AND expires_at > CURRENT_TIMESTAMP);"
	var exists bool
	err := tm.tdb.DB().QueryRow(query, tokenHash).Scan(&exists)
	if err != nil {
		return false, fmt.Errorf("failed to check token existence for hash %s: %w", tokenHash, err)
	}
	return exists, nil
}

// DeleteToken effectively deactivates a token or removes it.
// For auditing, deactivating (is_active = FALSE) is often better than hard delete.
func (tm *TokenManager) DeleteToken(tokenHash string) error {
	// Option 1: Hard delete
	// query := "DELETE FROM auth_tokens WHERE token_hash = $1;"
	// result, err := tm.tdb.DB().Exec(query, tokenHash)

	// Option 2: Soft delete (mark as inactive)
	query := "UPDATE auth_tokens SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP WHERE token_hash = $1;"
	result, err := tm.tdb.DB().Exec(query, tokenHash)

	if err != nil {
		return fmt.Errorf("failed to delete/deactivate token with hash %s: %w", tokenHash, err)
	}
	rowsAffected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get rows affected for token deletion/deactivation %s: %w", tokenHash, err)
	}
	if rowsAffected == 0 {
		return fmt.Errorf("no token found with hash %s to delete/deactivate", tokenHash)
	}
	return nil
}

// UpdateMetadata updates the metadata for a given token hash.
func (tm *TokenManager) UpdateMetadata(tokenHash string, newMetadata map[string]interface{}) error {
	if tokenHash == "" {
		return fmt.Errorf("token hash cannot be empty for metadata update")
	}

	metadataJSON, err := json.Marshal(newMetadata)
	if err != nil {
		return fmt.Errorf("failed to marshal new metadata to JSON: %w", err)
	}
	
	var metadataArg interface{}
	if len(newMetadata) > 0 {
		metadataArg = metadataJSON
	} else {
		metadataArg = nil
	}


	query := "UPDATE auth_tokens SET metadata = $1, updated_at = CURRENT_TIMESTAMP WHERE token_hash = $2;"
	result, err := tm.tdb.DB().Exec(query, metadataArg, tokenHash)
	if err != nil {
		return fmt.Errorf("failed to update metadata for token %s: %w", tokenHash, err)
	}
	rowsAffected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get rows affected for metadata update on token %s: %w", tokenHash, err)
	}
	if rowsAffected == 0 {
		return fmt.Errorf("no token found with hash %s to update metadata", tokenHash)
	}
	return nil
}

// UpdateLastUsed sets the last_used_at timestamp for a token.
func (tm *TokenManager) UpdateLastUsed(tokenHash string) error {
	query := "UPDATE auth_tokens SET last_used_at = CURRENT_TIMESTAMP WHERE token_hash = $1 AND is_active = TRUE;"
	result, err := tm.tdb.DB().Exec(query, tokenHash)
	if err != nil {
		return fmt.Errorf("failed to update last_used_at for token %s: %w", tokenHash, err)
	}
	rowsAffected, err := result.RowsAffected()
	if err != nil {
		// Log this error, but it might not be critical enough to fail the operation that used the token
		return fmt.Errorf("failed to get rows affected for last_used_at update on token %s: %w", tokenHash, err)
	}
	if rowsAffected == 0 {
		// Token might have been deactivated or expired just before this update.
		// This might not be an error worth propagating, or it could be logged.
		// For now, we'll consider it a non-critical issue if no rows were updated.
		// fmt.Printf("Warning: No active token found with hash %s to update last_used_at, or already updated.\n", tokenHash)
	}
	return nil
}
