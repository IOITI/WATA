#!/usr/bin/env python3
import os
import sys
import getpass
import logging
from pathlib import Path
from src.configuration import ConfigurationManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("saxo_auth_cli")

def save_auth_code(auth_code, config_manager):
    """Save the authorization code to a temporary file"""
    token_file_path = config_manager.get_config_value("authentication.persistant.token_path")
    auth_code_path = os.path.join(os.path.dirname(token_file_path), "saxo_auth_code.txt")
    
    # Ensure the directory exists
    Path(os.path.dirname(auth_code_path)).mkdir(parents=True, exist_ok=True)
    
    # Write the code to the file
    with open(auth_code_path, "w") as file:
        file.write(auth_code)
    
    logger.info(f"Authorization code saved to {auth_code_path}")
    print(f"Authorization code saved successfully! The application will now continue with the authentication process.")

def main():
    try:
        # Get config path from environment variable
        config_path = os.getenv("WATA_CONFIG_PATH")
        if not config_path:
            logger.error("WATA_CONFIG_PATH environment variable not set")
            print("Error: WATA_CONFIG_PATH environment variable not set")
            sys.exit(1)
            
        # Create a ConfigurationManager instance
        config_manager = ConfigurationManager(config_path)
        
        print("Please enter the Saxo Bank authorization code (will not be shown in terminal):")
        auth_code = getpass.getpass("")
        
        if not auth_code:
            logger.error("No authorization code provided")
            print("Error: Authorization code is required")
            sys.exit(1)
        
        # Save the authorization code
        save_auth_code(auth_code, config_manager)
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        print(f"Error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()