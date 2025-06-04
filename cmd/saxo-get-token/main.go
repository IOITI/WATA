package main

import (
	"encoding/json" // Added
	"fmt"
	"log"
	"os"
	"path/filepath" // Added

	"pymath/go_src/configuration"
	"pymath/go_src/database"
	"pymath/go_src/logging_helper"
	"pymath/go_src/saxo_authen"
)

const (
	appName           = "saxo-get-token"
	configPathEnvVar  = "WATA_CONFIG_PATH"
	defaultConfigPath = "./config/config.json"
)

func main() {
	log.Printf("Starting %s utility...", appName)

	// --- Configuration ---
	configPath := os.Getenv(configPathEnvVar)
	if configPath == "" {
		log.Printf("Environment variable %s not set, using default config path: %s", configPathEnvVar, defaultConfigPath)
		configPath = defaultConfigPath
	}
	cfg, err := configuration.LoadConfig(configPath)
	if err != nil {
		log.Fatalf("Failed to load configuration from %s: %v", configPath, err)
	}
	log.Println("Configuration loaded.")

	// --- Logging Setup ---
	if err := logging_helper.SetupLogging(cfg, appName+"-cli"); err != nil {
		log.Fatalf("Failed to setup logging: %v", err)
	}

	// --- Determine Saxo App Identifier ---
	var saxoAppIdentifier string
	if len(os.Args) > 1 {
		saxoAppIdentifier = os.Args[1]
		log.Printf("Using Saxo App Identifier from command line argument: %s", saxoAppIdentifier)
	} else {
		appConfigsInterface, _ := cfg.GetConfigValue("saxo_app_config")
		if appConfigsMap, ok := appConfigsInterface.(map[string]interface{}); ok && len(appConfigsMap) > 0 {
			for key := range appConfigsMap {
				saxoAppIdentifier = key
				break
			}
			log.Printf("No Saxo App Identifier provided, using first found in config: %s", saxoAppIdentifier)
		}
	}
	if saxoAppIdentifier == "" {
		log.Fatal("Saxo App Identifier is required. Provide as a command line argument or ensure 'saxo_app_config' has entries.")
	}

	// --- Database Setup (for TokenManager) ---
	useInMemoryDB := false
	tradingDB, err := database.NewTradingDB(cfg, useInMemoryDB)
	if err != nil {
		log.Fatalf("Failed to initialize TradingDB: %v", err)
	}
	defer tradingDB.Close()

	tokenMgrForSchema := database.NewTokenManager(tradingDB)
	if err := tokenMgrForSchema.CreateSchemaAuthTokens(); err != nil {
		log.Fatalf("Failed to create auth_tokens schema: %v", err)
	}
	log.Println("Database connection and token schema initialized.")

	// --- Initialize SaxoAuth ---

	// 1. Extract SaxoAppConfig for the given identifier
	appConfigInterface, err := cfg.GetConfigValue("saxo_app_config." + saxoAppIdentifier)
	if err != nil {
		log.Fatalf("Failed to get saxo_app_config for '%s' from main configuration: %v", saxoAppIdentifier, err)
	}

	var appCfg saxo_authen.SaxoAppConfig
	tempJson, jsonErr := json.Marshal(appConfigInterface)
	if jsonErr != nil {
		log.Fatalf("Failed to marshal appConfigInterface for '%s': %v", saxoAppIdentifier, jsonErr)
	}
	if jsonErr = json.Unmarshal(tempJson, &appCfg); jsonErr != nil {
		log.Fatalf("Failed to unmarshal appConfigInterface to SaxoAppConfig for '%s': %v", saxoAppIdentifier, jsonErr)
	}
	if appCfg.AppName == "" {
		appCfg.AppName = saxoAppIdentifier
	}

	// 2. Determine tokenDirPath
	basePathVal, _ := cfg.GetConfigValue("secrets.paths.base_path")
	basePath, _ := basePathVal.(string)
	if basePath == "" {
		basePath = "./secrets"
	}
	tokenDirVal, _ := cfg.GetConfigValue("secrets.paths.saxo_tokens_path")
	tokenDirSpecific, _ := tokenDirVal.(string)
	if tokenDirSpecific == "" {
		tokenDirSpecific = filepath.Join(basePath, "saxo_tokens")
	}
	tokenDirPath := tokenDirSpecific

	// 3. Initialize TokenManager
	tokenDB := database.NewTokenManager(tradingDB)

	// 4. Initialize SaxoAuth
	auth, err := saxo_authen.NewSaxoAuth(appCfg, tokenDirPath, tokenDB, nil)
	if err != nil {
		log.Fatalf("Failed to initialize SaxoAuth: %v", err)
	}

	log.Printf("SaxoAuth initialized for app: %s", appCfg.AppName)

	// --- Get Token ---
	fmt.Println("\nAttempting to retrieve Saxo Access Token...")
	fmt.Println("This may involve opening a URL for authorization and providing the redirect URL/code via the 'watasaxoauth' CLI.")

	accessToken, err := auth.GetToken()
	if err != nil {
		log.Fatalf("Failed to get Saxo token: %v", err)
	}

	if accessToken != "" {
		fmt.Println("\n------------------------------------------------------------------------------")
		fmt.Printf("Successfully obtained/validated Saxo Access Token for app '%s'!\n", appCfg.AppName) // Use appCfg.AppName
		fmt.Printf("Access Token: %s\n", accessToken)
		fmt.Println("(This token has also been saved securely in the database for the main application to use.)")
		fmt.Println("------------------------------------------------------------------------------")
	} else {
		log.Println("GetToken returned an empty access token without an error. This is unexpected.")
	}
}
