package main

import (
	"bufio"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"net/url" // Added import

	"pymath/go_src/configuration"
	// Assuming logrus is already set up if this CLI is part of a larger system.
	// For a standalone tool, basic log or fmt is fine.
)

const (
	configPathEnvVar  = "WATA_CONFIG_PATH"
	defaultConfigPath = "./config/config.json" // Default if env var is not set
	// Default file names, should align with saxo_authen package if possible
	defaultAuthCodeFileName = "saxo_auth_code.txt"
)

func main() {
	log.Println("Starting WataSaxoAuth CLI tool...")

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

	// --- Determine Auth Code Path ---
	// This logic should mirror how SaxoAuth determines it, typically based on AppName.
	// The user needs to specify which Saxo App config to use if multiple are defined.
	// For simplicity, let's assume there's a primary app or prompt the user.
	// Or, this tool is generic and just needs the base path for secrets.

	var appName string
	if len(os.Args) > 1 {
		appName = os.Args[1]
		log.Printf("Using AppName from command line argument: %s", appName)
	} else {
		// Attempt to get a "default" or first app_name from saxo_app_config if possible,
		// or ask the user.
		// For this example, let's try to find one or ask.
		// This part requires knowing the structure of "saxo_app_config".
		// If it's a map: map[string]SaxoAppConfig
		appConfigsInterface, _ := cfg.GetConfigValue("saxo_app_config")
		if appConfigsMap, ok := appConfigsInterface.(map[string]interface{}); ok && len(appConfigsMap) > 0 {
			// Get the first key from the map as a default appName
			for key := range appConfigsMap {
				appName = key
				log.Printf("No AppName provided via argument, using first found in config: %s", appName)
				break
			}
		}
		if appName == "" {
			log.Println("Please provide the Saxo AppName as a command line argument.")
			log.Println("Example: go run cmd/watasaxoauth/main.go <YourSaxoAppName>")
			reader := bufio.NewReader(os.Stdin)
			fmt.Print("Enter Saxo AppName (from config file): ")
			appNameInput, _ := reader.ReadString('\n')
			appName = strings.TrimSpace(appNameInput)
			if appName == "" {
				log.Fatal("AppName is required.")
			}
		}
	}


	basePathVal, _ := cfg.GetConfigValue("secrets.paths.base_path")
	basePath, _ := basePathVal.(string)
	if basePath == "" {
		basePath = "./secrets" // Default base path
		log.Printf("'secrets.paths.base_path' not found in config, using default: %s", basePath)
	}

	tokenDirVal, _ := cfg.GetConfigValue("secrets.paths.saxo_tokens_path")
	tokenDir, _ := tokenDirVal.(string)
	if tokenDir == "" {
		tokenDir = filepath.Join(basePath, "saxo_tokens")
		log.Printf("'secrets.paths.saxo_tokens_path' not found in config, using default: %s", tokenDir)
	}

	authCodeFileName := fmt.Sprintf("%s_%s", appName, defaultAuthCodeFileName)
	authCodePath := filepath.Join(tokenDir, authCodeFileName)

	log.Printf("This tool will save the authorization code/URL to: %s", authCodePath)

	// --- Ensure directory exists ---
	if err := os.MkdirAll(filepath.Dir(authCodePath), 0700); err != nil {
		log.Fatalf("Failed to create directory for auth code file '%s': %v", authCodePath, err)
	}

	// --- Prompt user for authorization code/URL ---
	fmt.Println("\nSaxo Authentication - Authorization Code Input")
	fmt.Println("----------------------------------------------")
	fmt.Println("After authorizing the application with Saxo Bank, you will be redirected to a URL.")
	fmt.Println("This URL contains the authorization code needed to obtain tokens.")
	fmt.Print("Please paste the ENTIRE redirect URL here: ")

	reader := bufio.NewReader(os.Stdin)
	inputURL, err := reader.ReadString('\n')
	if err != nil {
		log.Fatalf("Failed to read input: %v", err)
	}
	inputURL = strings.TrimSpace(inputURL)

	if inputURL == "" {
		log.Fatal("No input provided. Exiting.")
	}

	// Extract the code if it's a full URL.
	// Saxo typically provides `code=YOUR_CODE&state=YOUR_STATE` in the query params.
	// Some simple implementations might just expect the code itself.
	// The Python code expects the full URL and parses it. Let's do that.
	parsedURL, err := url.Parse(inputURL)
	var authCode string
	if err == nil {
		authCode = parsedURL.Query().Get("code")
		retrievedState := parsedURL.Query().Get("state") // Optional: validate state if known
		if authCode == "" {
			log.Println("Could not find 'code' parameter in the provided URL. Saving the entire input.")
			authCode = inputURL // Save entire input if code not found
		} else {
			log.Printf("Extracted authorization code. (State: %s)", retrievedState)
		}
	} else {
		log.Printf("Could not parse input as URL (%v). Saving the entire input as code.", err)
		authCode = inputURL
	}


	// --- Write the code to authCodePath ---
	err = os.WriteFile(authCodePath, []byte(authCode), 0600) // Read/write for owner only
	if err != nil {
		log.Fatalf("Failed to write authorization code to file '%s': %v", authCodePath, err)
	}
	// Explicitly chmod, as WriteFile mode can be affected by umask
	if err := os.Chmod(authCodePath, 0600); err != nil {
		log.Printf("Warning: Failed to chmod auth code file %s: %v", authCodePath, err)
	}


	log.Printf("Authorization code/URL successfully written to: %s", authCodePath)
	log.Println("The main application can now use this code to obtain tokens.")
}
