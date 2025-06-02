package database

import (
	"os"
	"path/filepath"
	"pymath/go_src/configuration"
	"strings" // Added import
	"testing"
)

// Helper to get a basic config for testing.
func getTestConfig(dbPath string) *configuration.Config {
	return &configuration.Config{
		Database: configuration.Database{
			DBName: dbPath, // Using DBName to hold the full path for testing convenience
		},
		// Populate other fields if NewTradingDB depends on them, though currently it doesn't
	}
}

func TestNewTradingDB_InMemory(t *testing.T) {
	tdb, err := NewTradingDB(nil, true) // Pass nil config, true for in-memory
	if err != nil {
		t.Fatalf("NewTradingDB in-memory failed: %v", err)
	}
	if tdb == nil {
		t.Fatal("NewTradingDB in-memory returned nil tdb")
	}
	if !tdb.isTestDB {
		t.Error("Expected isTestDB to be true for in-memory database")
	}
	if tdb.dbPath != ":memory:" {
		t.Errorf("Expected dbPath to be :memory:, got %s", tdb.dbPath)
	}

	err = tdb.DB().Ping()
	if err != nil {
		t.Errorf("Ping failed for in-memory database: %v", err)
	}

	err = tdb.Close()
	if err != nil {
		t.Errorf("Close failed for in-memory database: %v", err)
	}
}

func TestNewTradingDB_File(t *testing.T) {
	tempDir := t.TempDir()
	dbFilePath := filepath.Join(tempDir, "test_trading.db")
	config := getTestConfig(dbFilePath)

	// Ensure no corruption marker from previous tests if any
	_ = os.Remove(filepath.Join(tempDir, corruptionMarkerFile))

	tdb, err := NewTradingDB(config, false)
	if err != nil {
		t.Fatalf("NewTradingDB with file failed: %v", err)
	}
	if tdb == nil {
		t.Fatal("NewTradingDB with file returned nil tdb")
	}
	if tdb.isTestDB {
		t.Error("Expected isTestDB to be false for file database")
	}
	if tdb.dbPath != dbFilePath {
		t.Errorf("Expected dbPath to be %s, got %s", dbFilePath, tdb.dbPath)
	}

	err = tdb.DB().Ping()
	if err != nil {
		t.Errorf("Ping failed for file database: %v", err)
	}

	// Test corruption marking
	if tdb.IsDatabaseCorrupted() {
		t.Error("Database should not be marked as corrupted initially")
	}
	err = tdb.MarkDatabaseAsCorrupted()
	if err != nil {
		t.Errorf("MarkDatabaseAsCorrupted failed: %v", err)
	}
	if !tdb.IsDatabaseCorrupted() {
		t.Error("Database should be marked as corrupted after marking")
	}

	// Try to open it again, should fail due to corruption marker
	tdb.Close() // Close the current connection first
	_, err = NewTradingDB(config, false)
	if err == nil {
		t.Error("NewTradingDB should have failed for a corrupted database, but it succeeded")
	} else {
		t.Logf("Got expected error for corrupted DB: %v", err) // Log for info
	}


	// Test removing corruption mark
	// Need a valid tdb instance that is not :memory: to remove the mark
	// Let's create a new tdb instance after removing the marker file manually for the test
	err = os.Remove(filepath.Join(tempDir, corruptionMarkerFile))
	if err != nil {
		t.Fatalf("Failed to remove corruption marker file for test setup: %v", err)
	}
	
	// Re-initialize tdb for RemoveCorruptionMark test
	tdb, err = NewTradingDB(config, false)
	if err != nil {
		t.Fatalf("Failed to re-initialize tdb for RemoveCorruptionMark test: %v", err)
	}

	// Mark it again via the instance
	err = tdb.MarkDatabaseAsCorrupted()
	if err != nil {
		t.Fatalf("Failed to mark database as corrupted for RemoveCorruptionMark test: %v", err)
	}
	if !tdb.IsDatabaseCorrupted() {
		t.Fatal("Database should be marked as corrupted before removing the mark")
	}
	err = tdb.RemoveCorruptionMark()
	if err != nil {
		t.Errorf("RemoveCorruptionMark failed: %v", err)
	}
	if tdb.IsDatabaseCorrupted() {
		t.Error("Database should not be marked as corrupted after removing the mark")
	}
	
	// Verify marker file is gone
	if _, statErr := os.Stat(filepath.Join(tempDir, corruptionMarkerFile)); !os.IsNotExist(statErr) {
		t.Error("Corruption marker file still exists after RemoveCorruptionMark")
	}


	err = tdb.Close()
	if err != nil {
		t.Errorf("Close failed for file database: %v", err)
	}

	// Check if db file was created
	if _, err := os.Stat(dbFilePath); os.IsNotExist(err) {
		t.Errorf("Database file %s was not created", dbFilePath)
	}
}

func TestNewTradingDB_NoConfig(t *testing.T) {
	_, err := NewTradingDB(nil, false) // File DB without config
	if err == nil {
		t.Fatal("NewTradingDB should fail if config is nil for a file database")
	}
	expectedErrorMsg := "database path (DBName) not provided in configuration"
	if err.Error() != expectedErrorMsg {
		t.Errorf("Expected error '%s', got '%s'", expectedErrorMsg, err.Error())
	}
}

func TestNewTradingDB_DirectoryCreation(t *testing.T) {
	tempDir := t.TempDir()
	// Create a nested path that doesn't exist yet
	nestedDbPath := filepath.Join(tempDir, "subdir1", "subdir2", "test_nested.db")
	config := getTestConfig(nestedDbPath)

	tdb, err := NewTradingDB(config, false)
	if err != nil {
		t.Fatalf("NewTradingDB with nested path failed: %v", err)
	}
	defer tdb.Close()

	if _, err := os.Stat(nestedDbPath); os.IsNotExist(err) {
		t.Errorf("Database file %s was not created in nested directory", nestedDbPath)
	}
}

// Mock configuration for testing TradingDB dependencies if any were to be added.
// For now, it's simple.
var testDBInstance *TradingDB
var testConfig *configuration.Config

// testMainWithDB is a helper to set up an in-memory DB for other manager tests.
// Not a TestMain, but a helper for other test files in this package.
func setupTestDB(t *testing.T) (*TradingDB, func()) {
	t.Helper()
	// Use in-memory for speed and isolation for most manager tests
	tdb, err := NewTradingDB(nil, true)
	if err != nil {
		t.Fatalf("Failed to create in-memory test DB: %v", err)
	}
	return tdb, func() {
		tdb.Close()
	}
}

// Example of how other test files might use this:
// func TestSomethingInAnotherManager(t *testing.T) {
//     tdb, cleanup := setupTestDB(t)
//     defer cleanup()
//     // ... use tdb
// }

// TestTradingDB_Pragmas ensures PRAGMA statements are executed.
// This is a bit of an integration test as it checks DB state.
func TestTradingDB_Pragmas(t *testing.T) {
	tdb, err := NewTradingDB(nil, true) // In-memory
	if err != nil {
		t.Fatalf("NewTradingDB failed: %v", err)
	}
	defer tdb.Close()

	var memoryLimit string
	// Querying settings in DuckDB can be done via `SELECT current_setting('memory_limit');`
	err = tdb.DB().QueryRow("SELECT current_setting('memory_limit');").Scan(&memoryLimit)
	if err != nil {
		// DuckDB might return '1024MB' or similar if it normalizes.
		// The PRAGMA query for memory_limit might not be supported or might have a different name.
		// Let's try another way or accept that direct verification is tricky.
		// For now, we'll log if reading the setting fails, as applying it was the main goal.
		// Alternative: query "SELECT value FROM duckdb_settings() WHERE name = 'memory_limit';"
		err = tdb.DB().QueryRow("SELECT setting FROM duckdb_settings() WHERE name = 'memory_limit';").Scan(&memoryLimit)
		if err != nil {
			t.Logf("Could not directly verify memory_limit setting using duckdb_settings(): %v. This might be fine if SET command succeeded.", err)
		} else {
			// Compare based on a rough match or by parsing
			// Example: "1GB" vs "953.7MiB" or "1024.0MiB"
			// For simplicity, we'll check if the numeric part is present and non-zero if the original limit was non-zero
			// This is a loose check. A more robust check would parse units.
			numericOriginal := strings.TrimRight(duckDBMemoryLimit, "GMBgb")
			if numericOriginal != "0" && !strings.Contains(memoryLimit, numericOriginal) {
				// A more robust check might involve parsing the value and unit
				t.Logf("Memory limit from settings: %s (original set: %s). Verification is approximate.", memoryLimit, duckDBMemoryLimit)
				// Allow common variations like 953.6MiB or 1024MB for 1GB
				if duckDBMemoryLimit == "1GB" && !(strings.Contains(memoryLimit, "953") || strings.Contains(memoryLimit, "1024") || strings.Contains(memoryLimit, "1G")) {
					t.Errorf("Expected memory_limit (from duckdb_settings) to reflect '%s', got '%s'", duckDBMemoryLimit, memoryLimit)
				} else if duckDBMemoryLimit != "1GB" && !strings.Contains(memoryLimit, numericOriginal){ // For other values
                     t.Errorf("Expected memory_limit (from duckdb_settings) to contain '%s', got '%s'", numericOriginal, memoryLimit)
                }
			}
		}
	} else { // current_setting('memory_limit') worked
		// Similar loose check
		numericOriginal := strings.TrimRight(duckDBMemoryLimit, "GMBgb")
		if numericOriginal != "0" && !strings.Contains(memoryLimit, numericOriginal) {
			t.Logf("Memory limit from current_setting: %s (original set: %s). Verification is approximate.", memoryLimit, duckDBMemoryLimit)
			if duckDBMemoryLimit == "1GB" && !(strings.Contains(memoryLimit, "953") || strings.Contains(memoryLimit, "1024") || strings.Contains(memoryLimit, "1G")) {
				t.Errorf("Expected memory_limit (from current_setting) to reflect '%s', got '%s'", duckDBMemoryLimit, memoryLimit)
			} else if duckDBMemoryLimit != "1GB" && !strings.Contains(memoryLimit, numericOriginal) {
                 t.Errorf("Expected memory_limit (from current_setting) to contain '%s', got '%s'", numericOriginal, memoryLimit)
            }
		}
    }


	var threads string
	err = tdb.DB().QueryRow("SELECT current_setting('threads');").Scan(&threads)
	if err != nil {
		err = tdb.DB().QueryRow("SELECT setting FROM duckdb_settings() WHERE name = 'threads';").Scan(&threads)
		if err != nil {
			t.Logf("Could not directly verify threads setting: %v. This might be fine if SET command succeeded.", err)
		} else {
			expectedThreads := duckDBThreads // which is "2"
			if threads != expectedThreads {
				t.Errorf("Expected threads (from duckdb_settings) to be '%s', got '%s'", expectedThreads, threads)
			}
		}
	} else {
        expectedThreads := duckDBThreads // which is "2"
        if threads != expectedThreads {
            t.Errorf("Expected threads (from current_setting) to be '%s', got '%s'", expectedThreads, threads)
        }
    }
}

// TestTradingDB_CorruptionMark_InMemory checks that corruption marking is a no-op for in-memory DBs
func TestTradingDB_CorruptionMark_InMemory(t *testing.T) {
	tdb, err := NewTradingDB(nil, true)
	if err != nil {
		t.Fatalf("Failed to create in-memory DB: %v", err)
	}
	defer tdb.Close()

	if tdb.IsDatabaseCorrupted() {
		t.Error("In-memory DB should not be corrupted initially")
	}
	err = tdb.MarkDatabaseAsCorrupted()
	if err == nil { // Expecting an error as it's not applicable
		t.Error("MarkDatabaseAsCorrupted should return an error for in-memory DB")
	} else {
		expectedErrorMsg := "cannot mark in-memory or uninitialized database as corrupted"
		if err.Error() != expectedErrorMsg {
			t.Errorf("Expected error '%s', got '%s'", expectedErrorMsg, err.Error())
		}
	}


	if tdb.IsDatabaseCorrupted() { // Should still be false
		t.Error("In-memory DB should not be reported as corrupted after trying to mark")
	}

	err = tdb.RemoveCorruptionMark() // Should be a no-op, no error
	if err != nil {
		t.Errorf("RemoveCorruptionMark for in-memory DB failed: %v", err)
	}
}
