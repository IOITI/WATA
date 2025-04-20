import argparse
import os
import sys
from src.configuration import ConfigurationManager
from src.web_server.auth_token import WebServerToken
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def parse_arguments():
    parser = argparse.ArgumentParser(description="WebServer Token CLI")
    parser.add_argument("--display", action="store_true", help="Display the current WebServer token")
    parser.add_argument("--new", action="store_true", help="Generate a new WebServer token")
    return parser.parse_args()

def main():
    args = parse_arguments()
    
    # Get configuration path from environment variable
    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        logger.error("WATA_CONFIG_PATH environment variable is not set")
        sys.exit(1)
        
    try:
        # Initialize configuration and token manager
        config_manager = ConfigurationManager(config_path)
        web_server_token = WebServerToken(config_manager)
        
        if args.new:
            # Generate a new token
            token = os.urandom(32).hex()
            web_server_token.save_token_data(token)
            print("New WebServer token generated successfully!")
            print(f"Token: {token}")
        else:
            # Display the current token (default action)
            token = web_server_token.get_token()
            print(f"WebServer Token: {token}")
            print("\nThis token is required for authenticating webhook requests to your API.")
            print("Keep it secure and use it in your webhook URL as a query parameter:")
            print("Example: https://your-server.com/webhook?token=YOUR_TOKEN")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 