package database

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"pymath/go_src/configuration" // Assuming configuration package is in this path

	_ "github.com/marcboeker/go-duckdb" // DuckDB driver
)

const (
	duckDBMemoryLimit    = "1GB"
	duckDBThreads        = "2"
	corruptionMarkerFile = ".db_corrupted"
)

// TradingDB manages the DuckDB connection and related settings.
type TradingDB struct {
	db       *sql.DB
	dbPath   string
	isTestDB bool // Flag to indicate if this is a test database (e.g., in-memory)
}

// NewTradingDB creates a new TradingDB instance.
// It takes the database file path from the configuration.
// For testing, if dbPath is ":memory:", it will use an in-memory database.
func NewTradingDB(config *configuration.Config, useInMemory bool) (*TradingDB, error) {
	var dbPath string
	if useInMemory {
		dbPath = ":memory:"
	} else {
		if config == nil || config.Database.DBName == "" {
			return nil, fmt.Errorf("database path (DBName) not provided in configuration")
		}
		// Assuming DBName in config is the actual file path or a base name for it.
		// For simplicity, let's assume DBName is the full path.
		// In a real application, you might construct this path more carefully.
		dbPath = config.Database.DBName
	}

	// Ensure the directory for the database file exists if not in-memory
	if !useInMemory {
		dbDir := filepath.Dir(dbPath)
		if _, err := os.Stat(dbDir); os.IsNotExist(err) {
			if mkDirErr := os.MkdirAll(dbDir, 0755); mkDirErr != nil {
				return nil, fmt.Errorf("failed to create database directory '%s': %w", dbDir, mkDirErr)
			}
		}
	}

	// Connect to DuckDB
	// The connection string can include parameters like ?access_mode=READ_WRITE
	connStr := dbPath
	if !useInMemory {
		// Check for corruption marker only for file-based databases
		if _, err := os.Stat(filepath.Join(filepath.Dir(dbPath), corruptionMarkerFile)); err == nil {
			return nil, fmt.Errorf("database at %s is marked as corrupted. Please check.", dbPath)
		}
		connStr = fmt.Sprintf("%s?access_mode=READ_WRITE", dbPath) // Example, adjust as needed
	}


	db, err := sql.Open("duckdb", connStr)
	if err != nil {
		return nil, fmt.Errorf("failed to open DuckDB database at %s: %w", dbPath, err)
	}

	// Ping the database to ensure the connection is live
	if err = db.Ping(); err != nil {
		db.Close() // Close the connection if ping fails
		return nil, fmt.Errorf("failed to ping DuckDB database at %s: %w", dbPath, err)
	}

	// Apply initial configurations
	// Note: Some configurations might be better set via DSN or connection string parameters if supported.
	// For DuckDB, SET statements are used for these.
	initialConfigs := []string{
		fmt.Sprintf("SET memory_limit='%s';", duckDBMemoryLimit),
		fmt.Sprintf("SET threads=%s;", duckDBThreads),
		// Add other SET statements or PRAGMAs as needed
		// e.g., "PRAGMA default_null_order='NULLS LAST';" // This is a valid PRAGMA
		// e.g., "PRAGMA enable_external_access=false;" // If not needing to access external files/S3
	}

	for _, confSQL := range initialConfigs {
		_, err := db.Exec(confSQL)
		if err != nil {
			db.Close()
			// It might be too strict to fail entirely for a PRAGMA not working,
			// depending on its importance. Log it at least.
			return nil, fmt.Errorf("failed to apply initial config '%s': %w", confSQL, err)
		}
	}

	return &TradingDB{db: db, dbPath: dbPath, isTestDB: useInMemory}, nil
}

// Close closes the database connection.
func (tdb *TradingDB) Close() error {
	if tdb.db != nil {
		return tdb.db.Close()
	}
	return nil
}

// DB returns the underlying sql.DB object for direct use if needed.
func (tdb *TradingDB) DB() *sql.DB {
	return tdb.db
}

// IsDatabaseCorrupted checks if the database is marked as corrupted.
// This is a simple file-based marker. A more robust system might involve DB checks.
func (tdb *TradingDB) IsDatabaseCorrupted() bool {
	if tdb.isTestDB || tdb.dbPath == ":memory:" || tdb.dbPath == "" { // Don't check for in-memory or uninitialized DB
		return false
	}
	markerPath := filepath.Join(filepath.Dir(tdb.dbPath), corruptionMarkerFile)
	_, err := os.Stat(markerPath)
	return err == nil // File exists means it's marked as corrupted
}

// MarkDatabaseAsCorrupted marks the database as corrupted by creating a marker file.
func (tdb *TradingDB) MarkDatabaseAsCorrupted() error {
	if tdb.isTestDB || tdb.dbPath == ":memory:" || tdb.dbPath == "" {
		return fmt.Errorf("cannot mark in-memory or uninitialized database as corrupted")
	}
	markerPath := filepath.Join(filepath.Dir(tdb.dbPath), corruptionMarkerFile)
	file, err := os.Create(markerPath)
	if err != nil {
		return fmt.Errorf("failed to create corruption marker file at %s: %w", markerPath, err)
	}
	return file.Close()
}

// RemoveCorruptionMark removes the corruption marker file.
func (tdb *TradingDB) RemoveCorruptionMark() error {
	if tdb.isTestDB || tdb.dbPath == ":memory:" || tdb.dbPath == "" {
		return nil // No marker to remove for in-memory DBs
	}
	markerPath := filepath.Join(filepath.Dir(tdb.dbPath), corruptionMarkerFile)
	err := os.Remove(markerPath)
	if err != nil && !os.IsNotExist(err) { // Don't error if marker doesn't exist
		return fmt.Errorf("failed to remove corruption marker file at %s: %w", markerPath, err)
	}
	return nil
}
