package main

import (
	// "context" // Removed unused import
	"fmt"
	stdlog "log" // Standard log for initial bootstrap
	"os"
	"os/signal"
	"syscall"
	"time"

	"pymath/go_src/configuration"
	"pymath/go_src/logging_helper"
	"pymath/go_src/scheduler" // Our jobs package

	"github.com/go-co-op/gocron/v2"
	"github.com/sirupsen/logrus"
)

const (
	appName           = "wata-scheduler"
	configPathEnvVar  = "WATA_CONFIG_PATH"
	defaultConfigPath = "./config/config.json"
)

// Helper to extract close_position_time from config using the new typed structure.
func getClosePositionTime(cfg *configuration.Config) (string, error) {
	if cfg == nil {
		return "", fmt.Errorf("configuration is nil")
	}
	// cfg.Trade is now a TradeConfig struct
	// cfg.Trade.Rules is []TradeRule
	// Each TradeRule has RuleConfig which is TradeRuleConfig struct

	for _, rule := range cfg.Trade.Rules {
		if rule.RuleType == "day_trading" {
			if rule.RuleConfig.ClosePositionTime == "" {
				return "", fmt.Errorf("'close_position_time' is empty in day_trading rule_config")
			}
			return rule.RuleConfig.ClosePositionTime, nil // Expects HH:MM format
		}
	}
	return "", fmt.Errorf("'day_trading' rule with 'close_position_time' not found in config")
}


func main() {
	stdlog.Printf("Starting %s application...", appName)

	configPath := os.Getenv(configPathEnvVar)
	if configPath == "" {
		stdlog.Printf("Environment variable %s not set, using default config path: %s", configPathEnvVar, defaultConfigPath)
		configPath = defaultConfigPath
	}
	cfg, err := configuration.LoadConfig(configPath)
	if err != nil {
		stdlog.Fatalf("Failed to load configuration from %s: %v", configPath, err)
	}
	stdlog.Println("Configuration loaded successfully.")

	if err := logging_helper.SetupLogging(cfg, appName); err != nil {
		stdlog.Fatalf("Failed to setup logging: %v", err)
	}
	logrus.Info("Logging has been initialized.")

	// --- Scheduler Setup ---
	// tradingTimezoneStr, ok := cfg.GlobalSettings.Version.(string) // DELETE THIS PLACEHOLDER LINE
	// This needs to be a real config value, e.g. cfg.GlobalSettings.TradingTimezone
	// Python: self.config_manager.get_config_value("trading.timezone")
	// Now using the typed TradeConfig struct
	var tradingTimezoneStr string
	if cfg.Trade.Timezone == "" {
		logrus.Warnf("'trading.timezone' not found or empty in config, using UTC as default.")
		tradingTimezoneStr = "UTC"
	} else {
		tradingTimezoneStr = cfg.Trade.Timezone
	}

	location, err := time.LoadLocation(tradingTimezoneStr)
	if err != nil {
		logrus.Fatalf("Failed to load trading timezone '%s': %v. Ensure it's a valid IANA Time Zone.", tradingTimezoneStr, err)
	}
	logrus.Infof("Using timezone for scheduler: %s", location.String())

	s, err := gocron.NewScheduler(gocron.WithLocation(location))
	if err != nil {
		logrus.Fatalf("Failed to create gocron scheduler: %v", err)
	}
	logrus.Info("Gocron scheduler created.")

	// --- Schedule Jobs ---
	// Job 1: Check Positions (every 15 seconds)
	_, err = s.NewJob(
		gocron.DurationJob(15*time.Second), // gocron.Every(15).Seconds() is also an option
		gocron.NewTask(scheduler.JobCheckPositions, cfg),
		gocron.WithName("JobCheckPositions"),
	)
	if err != nil {
		logrus.Fatalf("Failed to schedule JobCheckPositions: %v", err)
	}
	logrus.Info("JobCheckPositions scheduled every 15 seconds.")

	// Job 2: Daily Stats (daily at 22:00 in trading timezone)
	_, err = s.NewJob(
		gocron.DailyJob(1, gocron.NewAtTimes( // gocron.Every(1).Day().At("22:00")
			gocron.NewAtTime(22,0,0),
		)),
		gocron.NewTask(scheduler.JobDailyStats, cfg),
		gocron.WithName("JobDailyStats"),
	)
	if err != nil {
		logrus.Fatalf("Failed to schedule JobDailyStats: %v", err)
	}
	logrus.Info("JobDailyStats scheduled daily at 22:00 (in scheduler's timezone).")


	// Job 3: Close Positions
	closeTimeStr, err := getClosePositionTime(cfg)
	if err != nil {
		logrus.Fatalf("Failed to get close_position_time for JobClosePositions: %v", err)
	}
	// gocron At expects "HH:MM" or "HH:MM:SS"
	// closeTimeStr is expected to be "HH:MM"
	// We need to parse HH and MM from closeTimeStr
	var closeHour, closeMinute int
	if _, errScan := fmt.Sscanf(closeTimeStr, "%d:%d", &closeHour, &closeMinute); errScan != nil {
		logrus.Fatalf("Failed to parse close_position_time '%s' (expected HH:MM format): %v", closeTimeStr, errScan)
	}

	_, err = s.NewJob(
		gocron.DailyJob(1, gocron.NewAtTimes(
			gocron.NewAtTime(uint(closeHour), uint(closeMinute), 0),
		)),
		gocron.NewTask(scheduler.JobClosePositions, cfg, closeTimeStr), // Pass original string for logging if needed
		gocron.WithName("JobClosePositions"),
	)
	if err != nil {
		logrus.Fatalf("Failed to schedule JobClosePositions at %s: %v", closeTimeStr, err)
	}
	logrus.Infof("JobClosePositions scheduled daily at %s (in scheduler's timezone).", closeTimeStr)

	// --- Start Scheduler and Handle Shutdown ---
	s.Start() // Start scheduler asynchronously
	logrus.Info("Scheduler started. Waiting for jobs...")

	// Wait for interrupt signal
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, os.Interrupt, syscall.SIGTERM)

	// Block until a signal is received or context is done (if using context with Start)
	<-quit
	logrus.Info("Shutdown signal received...")

	// Graceful shutdown
	err = s.Shutdown()
	if err != nil {
		logrus.Errorf("Scheduler shutdown error: %v", err)
	}
	logrus.Info("Scheduler shut down gracefully.")
}
