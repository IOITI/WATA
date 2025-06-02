package configuration

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

const testConfigDir = "testdata"
const validConfigPath = "testdata/valid_config.json"
const invalidJSONConfigPath = "testdata/invalid_json_config.json"
const missingFieldsConfigPath = "testdata/missing_fields_config.json"
const invalidValuesConfigPath = "testdata/invalid_values_config.json"

// Helper function to create a temporary config file for testing
func createTestConfigFile(t *testing.T, filePath string, content interface{}) {
	dir := filepath.Dir(filePath)
	if err := os.MkdirAll(dir, 0755); err != nil {
		t.Fatalf("Failed to create testdata directory: %v", err)
	}

	data, err := json.MarshalIndent(content, "", "  ")
	if err != nil {
		t.Fatalf("Failed to marshal test config: %v", err)
	}

	err = os.WriteFile(filePath, data, 0644)
	if err != nil {
		t.Fatalf("Failed to write test config file: %v", err)
	}
}

func getDefaultValidConfig() Config {
	return Config{
		GlobalSettings: GlobalSettings{
			AppName:        "TestApp",
			Version:        "1.0.0",
			MaintenanceMode: false,
		},
		FeatureToggles: FeatureToggles{
			EnableNewDashboard:   true,
			EnableEmailAlerts:  true,
			EnableAnalyticsTracking: false,
		},
		APIServices: APIServices{
			UserService:    APIServiceConfig{Endpoint: "http://localhost:8081/users", Timeout: 5, Retries: 3},
			ProductService: APIServiceConfig{Endpoint: "http://localhost:8082/products", Timeout: 10, Retries: 2},
			PaymentGateway: APIServiceConfig{Endpoint: "https://api.payment.com/v1", Timeout: 15, Retries: 3},
		},
		Database: Database{
			Type:         "postgresql",
			Host:         "localhost",
			Port:         5432,
			Username:     "admin",
			Password:     "secret",
			DBName:       "test_db",
			SSLMode:      "disable",
			MaxOpenConns: 10,
			MaxIdleConns: 5,
			ConnMaxLifetime: 60,
		},
		Logging: Logging{
			Level:        "info",
			FilePath:     "/var/log/testapp.log",
			RotationSize: 100, // MB
			MaxBackups:   5,
			ConsoleOutput: true,
		},
		RabbitMQ: RabbitMQ{
			Host:        "localhost",
			Port:        5672,
			Username:    "guest",
			Password:    "guest",
			VirtualHost: "/",
			Queues: []QueueConfig{
				{Name: "task_queue", Durable: true, AutoDelete: false},
				{Name: "notification_queue", Durable: true, AutoDelete: false},
			},
			Exchanges: []ExchangeConfig{
				{Name: "direct_exchange", Type: "direct", Durable: true},
				{Name: "fanout_exchange", Type: "fanout", Durable: true},
			},
		},
		SchedulerSettings: SchedulerSettings{
			Enabled:         true,
			DefaultTimezone: "UTC",
			Tasks: []ScheduledTask{
				{Name: "CleanupTask", CronExpr: "0 0 * * *", Action: "cleanup_data", Disabled: false},
				{Name: "ReportTask", CronExpr: "0 8 * * 1", Action: "generate_report", Disabled: false},
			},
		},
	}
}

func TestMain(m *testing.M) {
	// Create a valid config file for tests that require one
	validConfig := getDefaultValidConfig()
	createTestConfigFile(&testing.T{}, validConfigPath, validConfig)

	// Create an invalid JSON config file
	err := os.MkdirAll(testConfigDir, 0755)
	if err != nil {
		panic("Failed to create testdata directory in TestMain")
	}
	err = os.WriteFile(invalidJSONConfigPath, []byte("{invalid_json: \"test\""), 0644)
	if err != nil {
		panic("Failed to write invalid JSON config file in TestMain")
	}

	// Create a config with missing fields
	missingFieldsConf := getDefaultValidConfig()
	missingFieldsConf.GlobalSettings.AppName = ""
	createTestConfigFile(&testing.T{}, missingFieldsConfigPath, missingFieldsConf)

	// Create a config with invalid values
	invalidValuesConf := getDefaultValidConfig()
	invalidValuesConf.Logging.Level = "invalid_level"
	invalidValuesConf.Database.Port = -1
	invalidValuesConf.APIServices.UserService.Timeout = 0
	invalidValuesConf.RabbitMQ.Exchanges[0].Type = "invalid_type"
	invalidValuesConf.SchedulerSettings.DefaultTimezone = "Invalid/Timezone"
	createTestConfigFile(&testing.T{}, invalidValuesConfigPath, invalidValuesConf)


	// Run tests
	exitVal := m.Run()

	// Clean up test files
	os.RemoveAll(testConfigDir)

	os.Exit(exitVal)
}

func TestLoadConfig_Success(t *testing.T) {
	config, err := LoadConfig(validConfigPath)
	if err != nil {
		t.Fatalf("Expected no error, got %v", err)
	}
	if config == nil {
		t.Fatal("Expected config to be loaded, but it was nil")
	}

	expectedConfig := getDefaultValidConfig()
	if !reflect.DeepEqual(*config, expectedConfig) {
		t.Errorf("Loaded config does not match expected config.\nExpected: %+v\nGot:      %+v", expectedConfig, *config)
	}
}

func TestLoadConfig_FileNotExist(t *testing.T) {
	_, err := LoadConfig("non_existent_config.json")
	if err == nil {
		t.Fatal("Expected error for non-existent file, got nil")
	}
	if !os.IsNotExist(err) { // Check if the error is specifically a "file does not exist" error
		// If not, check if the error message contains the expected text.
		// This is a fallback because os.IsNotExist might not always be true
		// for errors wrapped with fmt.Errorf.
		expectedErrorMsg := "failed to read config file"
		if !strings.Contains(err.Error(), expectedErrorMsg) {
			t.Errorf("Expected error message to contain '%s', got '%v'", expectedErrorMsg, err)
		}
	}
}


func TestLoadConfig_InvalidJSON(t *testing.T) {
	_, err := LoadConfig(invalidJSONConfigPath)
	if err == nil {
		t.Fatal("Expected error for invalid JSON, got nil")
	}
	expectedErrorMsg := "failed to unmarshal config JSON"
	if !strings.Contains(err.Error(), expectedErrorMsg) {
		t.Errorf("Expected error message to contain '%s', got '%v'", expectedErrorMsg, err)
	}
}

func TestValidateConfig_Success(t *testing.T) {
	config := getDefaultValidConfig()
	err := config.ValidateConfig()
	if err != nil {
		t.Fatalf("Expected no validation error for a valid config, got %v", err)
	}
}

func TestValidateConfig_MissingFields(t *testing.T) {
	testCases := []struct {
		name        string
		modifier    func(c *Config)
		expectedErr string
	}{
		{"Missing GlobalSettings.AppName", func(c *Config) { c.GlobalSettings.AppName = "" }, "global_settings.app_name is required"},
		{"Missing APIServices.UserService.Endpoint", func(c *Config) { c.APIServices.UserService.Endpoint = "" }, "api_services.UserService.endpoint is required"},
		{"Missing Database.Type", func(c *Config) { c.Database.Type = "" }, "database.type is required"},
		{"Missing Logging.Level", func(c *Config) { c.Logging.Level = "" }, "logging.level is required"},
		{"Missing RabbitMQ.Host", func(c *Config) { c.RabbitMQ.Host = "" }, "rabbitmq.host is required"},
		{"Missing SchedulerSettings.DefaultTimezone (when enabled)", func(c *Config) { c.SchedulerSettings.Enabled = true; c.SchedulerSettings.DefaultTimezone = "" }, "scheduler_settings.default_timezone is required"},
		{"Missing SchedulerSettings.TaskName", func(c *Config) { c.SchedulerSettings.Tasks[0].Name = "" }, "scheduler_settings.tasks[0].name is required"},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			config := getDefaultValidConfig()
			tc.modifier(&config)
			err := config.ValidateConfig()
			if err == nil {
				t.Fatalf("Expected validation error '%s', but got nil", tc.expectedErr)
			}
			if !strings.Contains(err.Error(), tc.expectedErr) {
				t.Errorf("Expected error message to contain '%s', got '%v'", tc.expectedErr, err)
			}
		})
	}
}

func TestValidateConfig_InvalidValues(t *testing.T) {
	testCases := []struct {
		name        string
		modifier    func(c *Config)
		expectedErr string
	}{
		{"Invalid Logging.Level", func(c *Config) { c.Logging.Level = "invalid" }, "logging.level is invalid: invalid"},
		{"Invalid APIServices.UserService.Timeout", func(c *Config) { c.APIServices.UserService.Timeout = 0 }, "api_services.UserService.timeout must be positive"},
		{"Invalid APIServices.ProductService.Retries", func(c *Config) { c.APIServices.ProductService.Retries = -1 }, "api_services.ProductService.retries cannot be negative"},
		{"Invalid Database.Port", func(c *Config) { c.Database.Port = 0 }, "database.port must be positive"},
		{"Invalid Database.SSLMode", func(c *Config) { c.Database.SSLMode = "invalid-ssl" }, "database.ssl_mode is invalid: invalid-ssl"},
		{"Invalid Logging.RotationSize", func(c *Config) { c.Logging.RotationSize = 0 }, "logging.rotation_size must be positive"},
		{"Invalid RabbitMQ.Port", func(c *Config) { c.RabbitMQ.Port = -1 }, "rabbitmq.port must be positive"},
		{"Invalid RabbitMQ.ExchangeType", func(c *Config) {c.RabbitMQ.Exchanges[0].Type = "bad_type"}, "rabbitmq.exchanges.type is invalid for exchange direct_exchange: bad_type"},
		{"Invalid SchedulerSettings.DefaultTimezone", func(c *Config) { c.SchedulerSettings.DefaultTimezone = "Invalid/Zone" }, "scheduler_settings.default_timezone is invalid: Invalid/Zone"},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			config := getDefaultValidConfig()
			tc.modifier(&config)
			err := config.ValidateConfig()
			if err == nil {
				t.Fatalf("Expected validation error containing '%s', but got nil", tc.expectedErr)
			}
			if !strings.Contains(err.Error(), tc.expectedErr) {
				t.Errorf("Expected error message to contain '%s', got '%v'", tc.expectedErr, err)
			}
		})
	}
}


func TestGetConfigValue_Success(t *testing.T) {
	config := getDefaultValidConfig()

	testCases := []struct {
		key          string
		expectedValue interface{}
	}{
		{"GlobalSettings.AppName", "TestApp"},
		{"FeatureToggles.EnableEmailAlerts", true},
		{"APIServices.UserService.Endpoint", "http://localhost:8081/users"},
		{"APIServices.ProductService.Timeout", 10},
		{"Database.Host", "localhost"},
		{"Database.Port", 5432},
		{"Logging.Level", "info"},
		{"RabbitMQ.Host", "localhost"},
		{"RabbitMQ.Queues.0.Name", "task_queue"}, // Example of accessing an array element by index
		{"SchedulerSettings.Enabled", true},
		{"SchedulerSettings.Tasks.1.Name", "ReportTask"},
	}

	for _, tc := range testCases {
		t.Run(tc.key, func(t *testing.T) {
			value, err := config.GetConfigValue(tc.key)
			if err != nil {
				t.Fatalf("Expected no error for key '%s', got %v", tc.key, err)
			}
			// Need to handle type assertion carefully for comparison
			switch expected := tc.expectedValue.(type) {
			case int:
				if val, ok := value.(int); !ok || val != expected {
					t.Errorf("For key '%s', expected %v (type %T), got %v (type %T)", tc.key, expected, expected, value, value)
				}
			case string:
				if val, ok := value.(string); !ok || val != expected {
					t.Errorf("For key '%s', expected %v (type %T), got %v (type %T)", tc.key, expected, expected, value, value)
				}
			case bool:
				if val, ok := value.(bool); !ok || val != expected {
					t.Errorf("For key '%s', expected %v (type %T), got %v (type %T)", tc.key, expected, expected, value, value)
				}
			default:
				if !reflect.DeepEqual(value, tc.expectedValue) {
					t.Errorf("For key '%s', expected %v (type %T), got %v (type %T)", tc.key, tc.expectedValue, tc.expectedValue, value, value)
				}
			}
		})
	}
}


func TestGetConfigValue_NotFound(t *testing.T) {
	config := getDefaultValidConfig()
	testCases := []string{
		"GlobalSettings.NonExistent",
		"NonExistentSection.Value",
		"APIServices.UserService.NonExistentField",
		"Database.Invalid.Path",
		"RabbitMQ.Queues.5.Name", // Index out of bounds
		"SchedulerSettings.Tasks.NonExistentTask.Name",
	}

	for _, key := range testCases {
		t.Run(key, func(t *testing.T) {
			_, err := config.GetConfigValue(key)
			if err == nil {
				t.Fatalf("Expected error for non-existent key '%s', got nil", key)
			}
			if !strings.Contains(err.Error(), "not found") && !strings.Contains(err.Error(), "is not a struct") && !strings.Contains(err.Error(), "index out of range") {
				t.Errorf("Expected error message for key '%s' to indicate 'not found' or 'not a struct' or 'index out of range', but got '%v'", key, err)
			}
		})
	}
}

func TestGetLoggingConfig(t *testing.T) {
	config := getDefaultValidConfig()
	loggingConfig := config.GetLoggingConfig()

	if !reflect.DeepEqual(loggingConfig, config.Logging) {
		t.Errorf("GetLoggingConfig() returned %+v, expected %+v", loggingConfig, config.Logging)
	}
}

func TestGetRabbitMQConfig(t *testing.T) {
	config := getDefaultValidConfig()
	rabbitmqConfig := config.GetRabbitMQConfig()

	if !reflect.DeepEqual(rabbitmqConfig, config.RabbitMQ) {
		t.Errorf("GetRabbitMQConfig() returned %+v, expected %+v", rabbitmqConfig, config.RabbitMQ)
	}
}

func TestGetConfigValue_NestedStructs(t *testing.T) {
	config := getDefaultValidConfig()
	value, err := config.GetConfigValue("APIServices.UserService")
	if err != nil {
		t.Fatalf("Error getting nested struct: %v", err)
	}
	userServiceConfig, ok := value.(APIServiceConfig)
	if !ok {
		t.Fatalf("Expected APIServiceConfig, got %T", value)
	}
	if userServiceConfig.Endpoint != "http://localhost:8081/users" {
		t.Errorf("Unexpected endpoint: %s", userServiceConfig.Endpoint)
	}
}
