package configuration

import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"strings"
	"time"
)

// Config struct to hold the configuration data
type Config struct {
	GlobalSettings    GlobalSettings    `json:"global_settings"`
	FeatureToggles    FeatureToggles    `json:"feature_toggles"`
	APIServices       APIServices       `json:"api_services"`
	Database          Database          `json:"database"`
	Logging           Logging           `json:"logging"`
	RabbitMQ          RabbitMQ          `json:"rabbitmq"`
	SchedulerSettings SchedulerSettings `json:"scheduler_settings"`
	Trade             TradeConfig       `json:"trade,omitempty"` // Added Trade config
}

// --- Trade Configuration Structs ---

// TradeRuleConfig holds specific configuration for a trading rule.
type TradeRuleConfig struct {
	// For day_trading
	ClosePositionTime                   string   `json:"close_position_time,omitempty"` // HH:MM
	DontEnterTradeIfDayProfitIsMoreThan *float64 `json:"dont_enter_trade_if_day_profit_is_more_than,omitempty"`
	MaxDayLossPercent                   *float64 `json:"max_day_loss_percent,omitempty"`
	TradingStartHour                    *int     `json:"trading_start_hour,omitempty"`    // Using pointers for optionality
	TradingEndHour                      *int     `json:"trading_end_hour,omitempty"`
	RiskyTradingStartHour               *int     `json:"risky_trading_start_hour,omitempty"`
	RiskyTradingStartMinute             *int     `json:"risky_trading_start_minute,omitempty"`

	// For allowed_indices
	IndiceIDs map[string]interface{} `json:"indice_ids,omitempty"` // e.g. {"CAC40": "FRA40", "DAX40": "GER40"}

	// For market_closed_dates
	DatesList []string `json:"dates_list,omitempty"` // e.g. ["2023-12-25", "2024-01-01"]

	// For signal_validation
	MaxAgeMinutes *float64 `json:"max_age_minutes,omitempty"` // Using float64 to match JSON number type
}

// TradeRule defines a single trading rule.
type TradeRule struct {
	RuleType   string          `json:"rule_type"`
	RuleConfig TradeRuleConfig `json:"rule_config"`
}

// TradeConfig holds all trading related configurations.
type TradeConfig struct {
	Timezone string      `json:"timezone,omitempty"` // e.g. "Europe/Paris"
	Rules    []TradeRule `json:"rules,omitempty"`
	// Add other general trade settings here
}

// GlobalSettings struct
type GlobalSettings struct {
	AppName        string `json:"app_name"`
	Version        string `json:"version"`
	MaintenanceMode bool   `json:"maintenance_mode"`
}

// FeatureToggles struct
type FeatureToggles struct {
	EnableNewDashboard   bool `json:"enable_new_dashboard"`
	EnableEmailAlerts  bool `json:"enable_email_alerts"`
	EnableAnalyticsTracking bool `json:"enable_analytics_tracking"`
}

// APIServices struct
type APIServices struct {
	UserService     APIServiceConfig `json:"user_service"`
	ProductService  APIServiceConfig `json:"product_service"`
	PaymentGateway  APIServiceConfig `json:"payment_gateway"`
}

// APIServiceConfig struct
type APIServiceConfig struct {
	Endpoint string `json:"endpoint"`
	Timeout  int    `json:"timeout"` // in seconds
	Retries  int    `json:"retries"`
}

// Database struct
type Database struct {
	Type         string `json:"type"`
	Host         string `json:"host"`
	Port         int    `json:"port"`
	Username     string `json:"username"`
	Password     string `json:"password"`
	DBName       string `json:"db_name"`
	SSLMode      string `json:"ssl_mode"`
	MaxOpenConns int    `json:"max_open_conns"`
	MaxIdleConns int    `json:"max_idle_conns"`
	ConnMaxLifetime int `json:"conn_max_lifetime"` // in minutes
}

// Logging struct
type Logging struct {
	Level        string `json:"level"` // e.g., "debug", "info", "warn", "error"
	FilePath     string `json:"file_path"`
	RotationSize int    `json:"rotation_size"` // in MB
	MaxBackups   int    `json:"max_backups"`
	ConsoleOutput bool  `json:"console_output"`
}

// RabbitMQ struct
type RabbitMQ struct {
	Host        string       `json:"host"`
	Port        int          `json:"port"`
	Username    string       `json:"username"`
	Password    string       `json:"password"`
	VirtualHost string       `json:"virtual_host"`
	Queues      []QueueConfig `json:"queues"`
	Exchanges   []ExchangeConfig `json:"exchanges"`
}

// QueueConfig struct
type QueueConfig struct {
	Name       string `json:"name"`
	Durable    bool   `json:"durable"`
	AutoDelete bool   `json:"auto_delete"`
}

// ExchangeConfig struct
type ExchangeConfig struct {
	Name    string `json:"name"`
	Type    string `json:"type"` // e.g., "direct", "topic", "fanout"
	Durable bool   `json:"durable"`
}

// SchedulerSettings struct
type SchedulerSettings struct {
	Enabled         bool              `json:"enabled"`
	DefaultTimezone string            `json:"default_timezone"`
	Tasks           []ScheduledTask   `json:"tasks"`
}

// ScheduledTask struct
type ScheduledTask struct {
	Name     string `json:"name"`
	CronExpr string `json:"cron_expr"`
	Action   string `json:"action"`
	Disabled bool   `json:"disabled"`
}

// LoadConfig loads configuration from a JSON file
func LoadConfig(filePath string) (*Config, error) {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("failed to read config file: %w", err)
	}

	var config Config
	err = json.Unmarshal(data, &config)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal config JSON: %w", err)
	}

	return &config, nil
}

// ValidateConfig checks for the presence and correctness of all required configuration fields
func (c *Config) ValidateConfig() error {
	// Validate GlobalSettings
	if c.GlobalSettings.AppName == "" {
		return fmt.Errorf("global_settings.app_name is required")
	}
	if c.GlobalSettings.Version == "" {
		return fmt.Errorf("global_settings.version is required")
	}

	// Validate APIServices
	apiServicesValue := reflect.ValueOf(c.APIServices)
	apiServicesType := apiServicesValue.Type()
	for i := 0; i < apiServicesValue.NumField(); i++ {
		field := apiServicesValue.Field(i)
		fieldName := apiServicesType.Field(i).Name
		apiConfig, ok := field.Interface().(APIServiceConfig)
		if !ok {
			return fmt.Errorf("invalid type for APIServiceConfig field %s", fieldName)
		}
		if apiConfig.Endpoint == "" {
			return fmt.Errorf("api_services.%s.endpoint is required", fieldName)
		}
		if apiConfig.Timeout <= 0 {
			return fmt.Errorf("api_services.%s.timeout must be positive", fieldName)
		}
		if apiConfig.Retries < 0 {
			return fmt.Errorf("api_services.%s.retries cannot be negative", fieldName)
		}
	}

	// Validate Database
	if c.Database.Type == "" {
		return fmt.Errorf("database.type is required")
	}
	if c.Database.Host == "" {
		return fmt.Errorf("database.host is required")
	}
	if c.Database.Port <= 0 {
		return fmt.Errorf("database.port must be positive")
	}
	if c.Database.Username == "" {
		return fmt.Errorf("database.username is required")
	}
	if c.Database.DBName == "" {
		return fmt.Errorf("database.db_name is required")
	}
	validSSLModes := []string{"disable", "require", "verify-ca", "verify-full"}
	sslModeValid := false
	for _, mode := range validSSLModes {
		if c.Database.SSLMode == mode {
			sslModeValid = true
			break
		}
	}
	if !sslModeValid && c.Database.Type != "sqlite" { // SQLite might not use SSLMode typically
		return fmt.Errorf("database.ssl_mode is invalid: %s", c.Database.SSLMode)
	}
	if c.Database.MaxOpenConns < 0 {
		return fmt.Errorf("database.max_open_conns cannot be negative")
	}
	if c.Database.MaxIdleConns < 0 {
		return fmt.Errorf("database.max_idle_conns cannot be negative")
	}
	if c.Database.ConnMaxLifetime < 0 {
		return fmt.Errorf("database.conn_max_lifetime cannot be negative")
	}


	// Validate Logging
	if c.Logging.Level == "" {
		return fmt.Errorf("logging.level is required")
	}
	validLogLevels := []string{"debug", "info", "warn", "error", "fatal", "panic"}
	levelIsValid := false
	for _, level := range validLogLevels {
		if strings.ToLower(c.Logging.Level) == level {
			levelIsValid = true
			break
		}
	}
	if !levelIsValid {
		return fmt.Errorf("logging.level is invalid: %s", c.Logging.Level)
	}
	if c.Logging.FilePath == "" {
		return fmt.Errorf("logging.file_path is required")
	}
	if c.Logging.RotationSize <= 0 {
		return fmt.Errorf("logging.rotation_size must be positive")
	}
	if c.Logging.MaxBackups < 0 {
		return fmt.Errorf("logging.max_backups cannot be negative")
	}

	// Validate RabbitMQ
	if c.RabbitMQ.Host == "" {
		return fmt.Errorf("rabbitmq.host is required")
	}
	if c.RabbitMQ.Port <= 0 {
		return fmt.Errorf("rabbitmq.port must be positive")
	}
	if c.RabbitMQ.Username == "" {
		return fmt.Errorf("rabbitmq.username is required")
	}
	for _, q := range c.RabbitMQ.Queues {
		if q.Name == "" {
			return fmt.Errorf("rabbitmq.queues.name is required")
		}
	}
	validExchangeTypes := []string{"direct", "topic", "fanout", "headers"}
	for _, ex := range c.RabbitMQ.Exchanges {
		if ex.Name == "" {
			return fmt.Errorf("rabbitmq.exchanges.name is required")
		}
		if ex.Type == "" {
			return fmt.Errorf("rabbitmq.exchanges.type is required for exchange %s", ex.Name)
		}
		typeIsValid := false
		for _, validType := range validExchangeTypes {
			if strings.ToLower(ex.Type) == validType {
				typeIsValid = true
				break
			}
		}
		if !typeIsValid {
			return fmt.Errorf("rabbitmq.exchanges.type is invalid for exchange %s: %s", ex.Name, ex.Type)
		}
	}


	// Validate SchedulerSettings
	if c.SchedulerSettings.Enabled {
		if c.SchedulerSettings.DefaultTimezone == "" {
			return fmt.Errorf("scheduler_settings.default_timezone is required when scheduler is enabled")
		}
		_, err := time.LoadLocation(c.SchedulerSettings.DefaultTimezone)
		if err != nil {
			return fmt.Errorf("scheduler_settings.default_timezone is invalid: %s, error: %w", c.SchedulerSettings.DefaultTimezone, err)
		}

		for i, task := range c.SchedulerSettings.Tasks {
			if task.Name == "" {
				return fmt.Errorf("scheduler_settings.tasks[%d].name is required", i)
			}
			if task.CronExpr == "" { // Basic check, cron expression validation can be complex
				return fmt.Errorf("scheduler_settings.tasks[%d].cron_expr is required for task %s", i, task.Name)
			}
			if task.Action == "" {
				return fmt.Errorf("scheduler_settings.tasks[%d].action is required for task %s", i, task.Name)
			}
		}
	}

	return nil
}

// GetConfigValue retrieves a configuration value using a dot-separated key
func (c *Config) GetConfigValue(key string) (interface{}, error) {
	parts := strings.Split(key, ".")
	currentValue := reflect.ValueOf(c).Elem()

	for _, part := range parts {
		if currentValue.Kind() == reflect.Ptr {
			currentValue = currentValue.Elem()
		}

		// Try to parse part as an array index first
		index, err := parseInt(part)
		if err == nil { // If part is an integer, try to access slice element
			if currentValue.Kind() == reflect.Slice {
				if index >= 0 && index < currentValue.Len() {
					currentValue = currentValue.Index(index)
					continue // Move to the next part of the key
				} else {
					return nil, fmt.Errorf("index out of range for key part '%s' in key '%s'", part, key)
				}
			} else {
				// It's an integer but the current value is not a slice
				return nil, fmt.Errorf("key part '%s' is an index but not a slice in key '%s'", part, key)
			}
		}

		// If not an index, or if it is an index but the current value is not a slice, assume it's a struct field
		if currentValue.Kind() != reflect.Struct {
			return nil, fmt.Errorf("key part '%s' is not a struct in key '%s'", part, key)
		}

		field := currentValue.FieldByNameFunc(func(fieldName string) bool {
			// Attempt to match JSON tag first, then field name
			structField, ok := currentValue.Type().FieldByName(fieldName)
			if !ok {
				return false
			}
			jsonTag := structField.Tag.Get("json")
			if jsonTag == part || strings.Split(jsonTag, ",")[0] == part {
				return true
			}
			return strings.EqualFold(fieldName, part)
		})


		if !field.IsValid() {
			return nil, fmt.Errorf("key part '%s' not found in key '%s'", part, key)
		}
		currentValue = field
	}
	if !currentValue.CanInterface(){
		return nil, fmt.Errorf("cannot get interface for key %s", key)
	}

	return currentValue.Interface(), nil
}

// GetLoggingConfig retrieves the logging configuration section
func (c *Config) GetLoggingConfig() Logging {
	return c.Logging
}

// GetRabbitMQConfig retrieves the RabbitMQ configuration section
func (c *Config) GetRabbitMQConfig() RabbitMQ {
	return c.RabbitMQ
}

// Helper function to parse time, not used in current validation but can be useful
func parseTime(timeStr string) (time.Time, error) {
	return time.Parse("15:04:05", timeStr)
}

// Helper function to parse date, not used in current validation but can be useful
func parseDate(dateStr string) (time.Time, error) {
	return time.Parse("2006-01-02", dateStr)
}

// parseInt is a helper to convert string to int, used for slice indexing.
func parseInt(s string) (int, error) {
	// Using Atoi from strconv, which needs to be imported.
	// For simplicity here, we'll just try to convert.
	// A more robust solution would involve strconv.Atoi and error handling.
	var i int
	_, err := fmt.Sscan(s, &i)
	return i, err
}
