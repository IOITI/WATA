package logging_helper

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"pymath/go_src/configuration"

	"github.com/sirupsen/logrus"
	"gopkg.in/natefinch/lumberjack.v2"
)

// SetupLogging configures application logging using Logrus and Lumberjack.
func SetupLogging(config *configuration.Config, appName string) error {
	if config == nil {
		return fmt.Errorf("configuration cannot be nil")
	}
	if appName == "" {
		return fmt.Errorf("appName cannot be empty")
	}

	logConfig := config.Logging

	// 1. Configure Logrus Formatter
	formatter := &logrus.TextFormatter{
		FullTimestamp:   true,
		TimestampFormat: "2006-01-02 15:04:05.000",
	}
	logrus.SetFormatter(formatter)

	// 2. Parse and Set Logrus Level (store error from parsing)
	levelStr := strings.ToLower(logConfig.Level)
	level, errLogrus := logrus.ParseLevel(levelStr)
	if errLogrus != nil {
		// Log this initial parsing error using the default Logrus logger (likely stderr at this point)
		// This specific message might not go to file if SetOutput hasn't happened with lumberjack.
		logrus.Warnf("Invalid log level '%s' in config, defaulting to 'info' for initial setup. Error: %v", logConfig.Level, errLogrus)
		logrus.SetLevel(logrus.InfoLevel) // Default to InfoLevel
	} else {
		logrus.SetLevel(level)
	}

	// 3. Configure Lumberjack (use initial values from config)
	if logConfig.FilePath == "" {
		// If FilePath is essential and missing, subsequent lumberjack setup will fail.
		// Log this error before trying to use logConfig.FilePath
		err := fmt.Errorf("log_path (config.Logging.FilePath) is not configured")
		logrus.Error(err.Error()) // Use existing logger config (might be stderr)
		return err
	}
	logDir := filepath.Join(logConfig.FilePath, appName)
	errMkdir := os.MkdirAll(logDir, 0755)
	if errMkdir != nil {
		// Also log this error
		err := fmt.Errorf("failed to create log directory '%s': %w", logDir, errMkdir)
		logrus.Error(err.Error())
		return err
	}
	logFile := filepath.Join(logDir, appName+".log")

	lumberjackLogger := &lumberjack.Logger{
		Filename:   logFile,
		MaxSize:    logConfig.RotationSize, // Use direct config values initially
		MaxBackups: logConfig.MaxBackups,   // Use direct config values initially
		Compress:   true,
	}

	// 4. Set Logrus Output
	var writers []io.Writer
	if logConfig.ConsoleOutput {
		writers = append(writers, os.Stdout)
	}
	writers = append(writers, lumberjackLogger)
	multiWriter := io.MultiWriter(writers...)
	logrus.SetOutput(multiWriter)

	// 5. Log a priming message (optional, but used for debugging)
	// This message confirms the output pipe is working with the (potentially uncorrected) lumberjack values.
	logrus.Debugln("Logger output initialized. Initial lumberjack values used.")

	// 6. Correct lumberjackLogger fields if they were invalid and log warnings.
	// These warnings should now go to the configured logger (file/console).
	if logConfig.RotationSize <= 0 {
		actualDefaultSize := 2 // The default we apply
		lumberjackLogger.MaxSize = actualDefaultSize
		logrus.Warnf("logConfig.RotationSize is invalid (%d), defaulting to %dMB", logConfig.RotationSize, actualDefaultSize)
	}
	if logConfig.MaxBackups <= 0 {
		actualDefaultBackups := 30 // The default we apply
		lumberjackLogger.MaxBackups = actualDefaultBackups
		logrus.Warnf("logConfig.MaxBackups is invalid (%d), defaulting to %d", logConfig.MaxBackups, actualDefaultBackups)
	}

	// 7. Re-log the level parsing error if it happened, to ensure it's in the configured file log.
	if errLogrus != nil {
		logrus.Warnf("Invalid log level '%s' (from config) was overridden to 'info'. Error: %v", logConfig.Level, errLogrus)
	}

	// 8. Initial application Log Messages
	logrus.Infof("-------------------------------- Started %s application --------------------------------", appName)
	logrus.Infof("Logging configured: Level=%s, File=%s, ConsoleOutput=%t", logrus.GetLevel().String(), logFile, logConfig.ConsoleOutput)

	return nil
}
