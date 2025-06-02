package logging_helper

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"pymath/go_src/configuration" // Adjusted import path
	"strings"
	"testing"

	"github.com/sirupsen/logrus"
	// "github.com/stretchr/testify/assert" // Could use for more fluent assertions
)

func getDefaultTestLogConfig(logPath string) configuration.Logging {
	return configuration.Logging{
		Level:         "debug",
		FilePath:      logPath,
		RotationSize:  1, // 1 MB for faster rotation if we were to test it fully
		MaxBackups:    2,
		ConsoleOutput: false, // Disable console output for cleaner test logs by default
	}
}

// Helper to create a temporary configuration for tests
func createTestConfig(logConfig configuration.Logging) *configuration.Config {
	return &configuration.Config{
		Logging: logConfig,
		// Populate other parts of Config if SetupLogging starts depending on them
	}
}

func TestSetupLogging_Success(t *testing.T) {
	tempDir := t.TempDir() // Creates a temporary directory, cleaned up automatically
	appName := "TestApp"

	logConfig := getDefaultTestLogConfig(tempDir) // Log path is the tempDir itself
	config := createTestConfig(logConfig)

	err := SetupLogging(config, appName)
	if err != nil {
		t.Fatalf("SetupLogging failed: %v", err)
	}

	// 1. Verify log file creation
	expectedLogDir := filepath.Join(tempDir, appName)
	expectedLogFile := filepath.Join(expectedLogDir, appName+".log")

	if _, errStat := os.Stat(expectedLogFile); os.IsNotExist(errStat) {
		t.Errorf("Log file was not created at %s", expectedLogFile)
	}

	// 2. Verify log level is set
	if logrus.GetLevel() != logrus.DebugLevel {
		t.Errorf("Expected log level Debug, got %s", logrus.GetLevel().String())
	}

	// 3. Log a message and check if it appears in the file
	testMessage := "This is a test log message for Success case."
	logrus.Info(testMessage) // Use Info as Debug might be too verbose for simple check

	// Give a moment for log to be written (especially if async, though Logrus default is not)
	// time.Sleep(100 * time.Millisecond) // Usually not needed for sync logging

	file, errFile := os.Open(expectedLogFile)
	if errFile != nil {
		t.Fatalf("Could not open log file %s for verification: %v", expectedLogFile, errFile)
	}
	// No defer file.Close() yet, need to read it first.

	// Add a small delay to help ensure logs are flushed, especially these initial warnings.
	// time.Sleep(50 * time.Millisecond) // Keep this commented unless absolutely necessary as it slows tests.

	scanner := bufio.NewScanner(file)
	found := false
	for scanner.Scan() {
		if strings.Contains(scanner.Text(), testMessage) {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("Test message '%s' not found in log file %s", testMessage, expectedLogFile)
	}

	// 4. Check initial "Started" message
	// Reset scanner to read from beginning
	_, _ = file.Seek(0, 0)
	scanner = bufio.NewScanner(file)
	foundStartedMsg := false
	expectedStartedMsg := fmt.Sprintf("Started %s application", appName)
	for scanner.Scan() {
		if strings.Contains(scanner.Text(), expectedStartedMsg) {
			foundStartedMsg = true
			break
		}
	}
	if !foundStartedMsg {
		t.Errorf("Initial 'Started application' message not found in log file.")
	}
}

func TestSetupLogging_LumberjackConfig(t *testing.T) {
	tempDir := t.TempDir()
	appName := "LumberjackTestApp"

	logConfig := getDefaultTestLogConfig(tempDir)
	logConfig.RotationSize = 5 // MB
	logConfig.MaxBackups = 10
	config := createTestConfig(logConfig)

	// We can't easily get the lumberjack.Logger instance from SetupLogging as it's not returned.
	// So, this test mainly verifies that SetupLogging runs without error when these are set.
	// A more direct test would require refactoring SetupLogging to expose the logger or its config.
	// For now, we trust that if SetupLogging completes, the parameters were passed.
	err := SetupLogging(config, appName)
	if err != nil {
		t.Fatalf("SetupLogging failed with custom lumberjack config: %v", err)
	}

	// Verify file creation as a basic check
	expectedLogFile := filepath.Join(tempDir, appName, appName+".log")
	if _, errStat := os.Stat(expectedLogFile); os.IsNotExist(errStat) {
		t.Errorf("Log file was not created at %s for lumberjack config test", expectedLogFile)
	}
	// Further checks on lumberjack parameters would require accessing the global logrus.Out or refactoring.
}

func TestSetupLogging_InvalidLogLevel(t *testing.T) {
	tempDir := t.TempDir()
	appName := "InvalidLevelApp"

	logConfig := getDefaultTestLogConfig(tempDir)
	logConfig.Level = "NOT_A_LEVEL"
	config := createTestConfig(logConfig)

	// SetupLogging should default to "info" and log a warning, not return an error for this.
	err := SetupLogging(config, appName)
	if err != nil {
		t.Fatalf("SetupLogging failed for invalid log level: %v. Expected it to default and continue.", err)
	}

	if logrus.GetLevel() != logrus.InfoLevel {
		t.Errorf("Expected log level to default to Info for invalid config, got %s", logrus.GetLevel().String())
	}

	// Check for the warning message about invalid level
	expectedLogFile := filepath.Join(tempDir, appName, appName+".log")
	file, errFile := os.Open(expectedLogFile)
	if errFile != nil {
		t.Fatalf("Could not open log file %s for verification: %v", expectedLogFile, errFile)
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	foundWarning := false
	// This is the message that is logged AFTER SetOutput has been called.
	expectedWarningMsg := "Invalid log level 'NOT_A_LEVEL' (from config) was overridden to 'info'." 
	// The full message includes an "Error: <details>" part, so Contains should work.
	// Example: "level=warning msg="Invalid log level 'NOT_A_LEVEL' (from config) was overridden to 'info'. Error: logrus: level not_a_level not found"
	// We are checking for the core part of the message.
	
	for scanner.Scan() {
		line := scanner.Text()
		// t.Logf("Log line: %s", line) // For debugging test
		if strings.Contains(line, expectedWarningMsg) { // Use the variable
			foundWarning = true
			break
		}
	}
	if !foundWarning {
		t.Errorf("Warning message for invalid log level not found in log file.")
	}
}

func TestSetupLogging_MissingLogPath(t *testing.T) {
	appName := "NoLogPathApp"
	logConfig := getDefaultTestLogConfig("") // Empty log path
	config := createTestConfig(logConfig)

	err := SetupLogging(config, appName)
	if err == nil {
		t.Fatal("SetupLogging should have failed for missing log path, but it succeeded.")
	}
	expectedErrorMsg := "log_path (config.Logging.FilePath) is not configured"
	if !strings.Contains(err.Error(), expectedErrorMsg) {
		t.Errorf("Expected error message to contain '%s', got '%v'", expectedErrorMsg, err)
	}
}

func TestSetupLogging_CannotCreateLogDir(t *testing.T) {
	// Using a path that's likely not writable without sudo, e.g., direct root.
	// This test might be flaky depending on test runner permissions.
	// A safer way is to make a file with the same name as intended dir.
	tempDir := t.TempDir()
	appName := "DirCreateFailApp"
	
	// Create a file where a directory is supposed to be created
	conflictingFilePath := filepath.Join(tempDir, appName)
	file, err := os.Create(conflictingFilePath)
	if err != nil {
		t.Fatalf("Failed to create conflicting file for test: %v", err)
	}
	file.Close()

	logConfig := getDefaultTestLogConfig(tempDir) // Log path is tempDir, appName will be subdir
	config := createTestConfig(logConfig)

	setupErr := SetupLogging(config, appName)
	if setupErr == nil {
		t.Fatalf("SetupLogging should have failed trying to create directory over a file, but it succeeded.")
	}
	// Error message might vary by OS ("is not a directory", "mkdir ...: file exists")
	// We check for the "failed to create log directory" part from our function.
	if !strings.Contains(setupErr.Error(), "failed to create log directory") {
		t.Errorf("Expected error related to directory creation, got: %v", setupErr)
	}
}

func TestSetupLogging_NoAppName(t *testing.T) {
	tempDir := t.TempDir()
	logConfig := getDefaultTestLogConfig(tempDir)
	config := createTestConfig(logConfig)
	err := SetupLogging(config, "") // Empty appName
	if err == nil {
		t.Fatal("SetupLogging should have failed for empty appName, but it succeeded.")
	}
	if !strings.Contains(err.Error(), "appName cannot be empty") {
		t.Errorf("Expected error 'appName cannot be empty', got '%v'", err)
	}
}

func TestSetupLogging_NoConfig(t *testing.T) {
	err := SetupLogging(nil, "SomeApp") // Nil config
	if err == nil {
		t.Fatal("SetupLogging should have failed for nil config, but it succeeded.")
	}
	if !strings.Contains(err.Error(), "configuration cannot be nil") {
		t.Errorf("Expected error 'configuration cannot be nil', got '%v'", err)
	}
}

func TestSetupLogging_InvalidRotationValues(t *testing.T) {
	tempDir := t.TempDir()
	appName := "InvalidRotationApp"

	logConfig := getDefaultTestLogConfig(tempDir)
	logConfig.RotationSize = 0 // Invalid
	logConfig.MaxBackups = -1  // Invalid
	config := createTestConfig(logConfig)

	err := SetupLogging(config, appName)
	if err != nil {
		t.Fatalf("SetupLogging failed: %v", err)
	}

	// Check for warning messages about defaults being used
	expectedLogFile := filepath.Join(tempDir, appName, appName+".log")
	file, errFile := os.Open(expectedLogFile)
	if errFile != nil {
		t.Fatalf("Could not open log file %s for verification: %v", expectedLogFile, errFile)
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	foundRotationWarning := false
	foundBackupWarning := false
	expectedRotationMsg := "logConfig.RotationSize is invalid (0), defaulting to 2MB"
	expectedBackupMsg := "logConfig.MaxBackups is invalid (-1), defaulting to 30"

	for scanner.Scan() {
		line := scanner.Text()
		if strings.Contains(line, expectedRotationMsg) {
			foundRotationWarning = true
		}
		if strings.Contains(line, expectedBackupMsg) {
			foundBackupWarning = true
		}
	}
	fileContent, readErr := os.ReadFile(expectedLogFile)
	if readErr != nil {
		t.Logf("Could not read log file %s to debug: %v", expectedLogFile, readErr)
	}

	if !foundRotationWarning {
		t.Errorf("Warning for invalid RotationSize not found in log.\nLog content:\n%s", string(fileContent))
	}
	if !foundBackupWarning {
		t.Errorf("Warning for invalid MaxBackups not found in log.\nLog content:\n%s", string(fileContent))
	}
	file.Close() // Close the file after reading
}
