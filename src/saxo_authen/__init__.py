import requests
import secrets
import urllib.parse
import json
import time
import datetime
import os
import logging
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import stat

from src.configuration import ConfigurationManager
from src.mq_telegram.tools import send_message_to_mq_for_telegram
from src.database import DbTokenManager

logger = logging.getLogger(__name__)


class SaxoAuth:
    """
    Handles authentication and token management for Saxo.
    """

    def __init__(self, config_manager, rabbit_connection=None):
        self.state = secrets.token_hex(16)
        self.config_manager = config_manager
        self.rabbit_connection = rabbit_connection
        self.app_data = self.config_manager.get_config_value(
            "authentication.saxo.app_config_object"
        )
        self.token_file_path = self.config_manager.get_config_value(
            "authentication.persistant.token_path"
        )
        self.auth_code_path = os.path.join(os.path.dirname(self.token_file_path), "saxo_auth_code.txt")
        
        # Ensure token directory exists
        os.makedirs(os.path.dirname(self.token_file_path), exist_ok=True)
        
        # Initialize encryption key
        self._initialize_encryption()
        
        # Initialize token database manager
        self.token_db = DbTokenManager(config_manager)
        self.token_id = "saxo_token"  # Unique identifier for Saxo tokens

    def _initialize_encryption(self):
        """
        Initialize encryption for secure token storage.
        The key is derived from APP_SECRET plus a salt stored in a separate file.
        """
        key_file_path = os.path.join(os.path.dirname(self.token_file_path), ".key_salt")
        
        # Create or load salt
        if os.path.exists(key_file_path):
            with open(key_file_path, "rb") as key_file:
                salt = key_file.read()
        else:
            # Generate new salt if doesn't exist
            salt = os.urandom(16)
            with open(key_file_path, "wb") as key_file:
                key_file.write(salt)
            # Set restrictive permissions on the key file
            os.chmod(key_file_path, stat.S_IRUSR | stat.S_IWUSR)
        
        # Derive key from app secret and salt
        password = self.app_data["AppSecret"].encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password))
        self.cipher = Fernet(key)
        
    def _encrypt_data(self, data):
        """Encrypt data before storing"""
        json_data = json.dumps(data).encode()
        return self.cipher.encrypt(json_data)
    
    def _decrypt_data(self, encrypted_data):
        """Decrypt stored data"""
        try:
            decrypted_data = self.cipher.decrypt(encrypted_data)
            return json.loads(decrypted_data)
        except Exception as e:
            logger.error(f"Error decrypting data: {e}")
            return None

    def get_authorization_url(self):
        """
        Generate and return the authorization URL for the user to visit in their browser.
        """
        params = {
            "response_type": "code",
            "client_id": self.app_data["AppKey"],
            "redirect_uri": self.app_data["RedirectUrls"][0],
            "state": self.state,
        }
        auth_url = f"{self.app_data['AuthorizationEndpoint']}?{urllib.parse.urlencode(params)}"
        return auth_url

    def read_auth_code_from_file(self):
        """
        Read the authorization code from the temporary file.
        """
        try:
            if os.path.exists(self.auth_code_path):
                with open(self.auth_code_path, "r") as file:
                    code = file.read().strip()
                    # Remove the file after reading
                    os.remove(self.auth_code_path)
                    if code:
                        logger.info("Successfully read authorization code from file")
                        return code
            return None
        except Exception as e:
            logger.error(f"Error reading authorization code from file: {e}")
            return None

    def get_authorization_code(self):
        """
        Get the authorization code from the temporary file or prompt the user to obtain it.
        """
        # Check if we already have an auth code
        code = self.read_auth_code_from_file()
        if code:
            return code

        # Generate authorization URL for the user to visit
        auth_url = self.get_authorization_url()
        auth_instructions = "\nPlease follow these steps to authorize the application:"
        auth_instructions += "\n1. Open the following URL in your browser:"
        auth_instructions += f"\n\n{auth_url}\n"
        auth_instructions += "\n2. Log in with your Saxo credentials and authorize the application"
        auth_instructions += "\n3. After authorization, you will be redirected to a page with a URL containing a 'code' parameter"
        auth_instructions += "\n4. Copy the code parameter and run the following command on the server:"
        auth_instructions += f"\n   watasaxoauth <CODE>\n"
        auth_instructions += "\nWaiting for authorization code..."
        
        print(auth_instructions)
        
        # Send the instructions to Telegram
        if hasattr(self, 'rabbit_connection'):
            try:
                send_message_to_mq_for_telegram(self.rabbit_connection, 
                                              f"--- SAXO AUTHORIZATION REQUIRED ---\n{auth_instructions}")
                logger.info("Authorization instructions sent to Telegram")
            except Exception as e:
                logger.error(f"Failed to send authorization instructions to Telegram: {e}")
        else:
            logger.warning("No rabbit_connection available, can't send message to Telegram")
        
        # Wait for the auth code file to appear (with timeout)
        max_wait_time = 300  # 5 minutes
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            code = self.read_auth_code_from_file()
            if code:
                if hasattr(self, 'rabbit_connection'):
                    send_message_to_mq_for_telegram(self.rabbit_connection, 
                                                  "✅ Authorization code received successfully!")
                return code
            time.sleep(5)
        
        error_message = "Timeout waiting for authorization code"
        logger.error(error_message)
        
        if hasattr(self, 'rabbit_connection'):
            send_message_to_mq_for_telegram(self.rabbit_connection, 
                                          f"❌ ERROR: {error_message}")
        
        raise TimeoutError(error_message)

    def exchange_code_for_token(self, code):
        """
        Exchange the authorization code for an access token.
        """
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.app_data["RedirectUrls"][0],
            "client_id": self.app_data["AppKey"],
            "client_secret": self.app_data["AppSecret"],
        }
        try:
            response = requests.post(self.app_data["TokenEndpoint"], data=data)
            if response.status_code == 201:
                logger.info("Successfully exchanged code for token")
                return response.json()
            else:
                logger.error(f"Failed to exchange code for token: {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Error exchanging code for token: {e}")
            return None
        except Exception as e:
            logger.error(f"Unknown error exchanging code for token: {e}")
            return None

    def refresh_token(self, refresh_token_param):
        """
        Refresh the access token using a refresh token.
        """
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token_param,
            "client_id": self.app_data["AppKey"],
            "client_secret": self.app_data["AppSecret"],
        }
        try:
            response = requests.post(self.app_data["TokenEndpoint"], data=data)
            if response.status_code == 201:
                logger.info("Successfully refreshed access token")
                return response.json()
            else:
                return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Error exchanging code for token: {e}")
            return None
        except Exception as e:
            logger.error(f"Unknown error exchanging code for token: {e}")
            return None

    def ask_new_token(self):
        """
        Obtain a new authorization code and exchange it for a new set of tokens.
        """
        try:
            # Generate a new authorization code
            code = self.get_authorization_code()
            if not code:
                print("Failed to obtain new authorization code")
                return None
        except Exception as e:
            logger.error(f"Error obtaining new authorization code: {e}")
            return None

        try:
            # Exchange the authorization code for a new set of tokens
            token_response = self.exchange_code_for_token(code)
            if not token_response:
                print("Failed to exchange code for token")
                return None

            return token_response
        except Exception as e:
            logger.error(f"Error exchanging code for token: {e}")
            return None

    def save_token_data(self, token_data):
        """
        Save token data to database in encrypted format.
        """
        token_data["date_saved"] = datetime.datetime.now().isoformat()
        
        # Encrypt the token data
        encrypted_data = self._encrypt_data(token_data)
        
        # Store in database
        metadata = json.dumps({
            "expires_in": token_data["expires_in"],
            "refresh_token_expires_in": token_data.get("refresh_token_expires_in", 0),
            "date_saved": token_data["date_saved"]
        })
        
        self.token_db.store_token(
            token_id=self.token_id,
            token_type="saxo_oauth",
            encrypted_data=encrypted_data,
            metadata=metadata
        )
        
        logger.info("Token data securely saved to database with encryption")
        

    def is_token_expired(self, token_data):
        """
        Check if the access token is expired.
        """
        if (
            not token_data
            or "date_saved" not in token_data
            or "expires_in" not in token_data
        ):
            logger.debug("Token data is missing or incomplete, considered expired")
            return True
        date_saved = datetime.datetime.fromisoformat(token_data["date_saved"])
        expires_in_second = token_data["expires_in"] - 120
        expiration_time = date_saved + datetime.timedelta(
            seconds=expires_in_second
        )
        logger.debug(f"Token wanted expiration time: {expiration_time}, current time: {datetime.datetime.now()}")
        return datetime.datetime.now() > expiration_time

    def is_refresh_token_expired(self, token_data):
        """
        Check if the refresh token is expired.
        """
        if (
            not token_data
            or "date_saved" not in token_data
            or "refresh_token_expires_in" not in token_data
        ):
            logger.debug("Refresh Token data is missing or incomplete, considered expired")
            return True
        date_saved = datetime.datetime.fromisoformat(token_data["date_saved"])
        refresh_token_expires_in_second = token_data["refresh_token_expires_in"] - 60
        refresh_token_expiration_time = date_saved + datetime.timedelta(
            seconds=refresh_token_expires_in_second
        )
        logger.debug(f"Refresh token wanted expiration time: {refresh_token_expiration_time}, current time: {datetime.datetime.now()}")
        return datetime.datetime.now() > refresh_token_expiration_time

    def get_token(self):
        """
        Get a valid access token, either by refreshing an existing token or obtaining a new one.
        """
        try:
            token_data = {}
            
            # Try to get token from database first
            encrypted_data = self.token_db.get_token(self.token_id)
            if encrypted_data:
                token_data = self._decrypt_data(encrypted_data) or {}
                logger.debug("Token data retrieved from database")

            if self.is_token_expired(token_data):
                if self.is_refresh_token_expired(token_data):
                    new_token_data = self.ask_new_token()
                    if new_token_data:
                        self.save_token_data(new_token_data)
                        token_data = new_token_data
                    else:
                        logger.error("Failed to obtain new tokens")
                        raise Exception("Failed to obtain new tokens")
                else:
                    new_token_data = self.refresh_token(token_data["refresh_token"])
                    if new_token_data:
                        self.save_token_data(new_token_data)
                        token_data = new_token_data
                    else:
                        logger.error("Failed to renew token")
                        raise Exception("Failed to renew token")
            if token_data["access_token"]:
                logger.debug("Give token for Saxo API")
            return token_data["access_token"]
        except FileNotFoundError as e:
            logger.error(f"Token file not found: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding token data: {e}")
            raise
        except Exception as e:
            logger.error(f"Error getting token: {e}")
            raise


if __name__ == "__main__":
    try:
        print("Starting")
        config_path = os.getenv("WATA_CONFIG_PATH")
        print("Configuring")
        # Create an instance of ConfigurationManager
        config_manager = ConfigurationManager(config_path)
        
        # Try to initialize rabbit connection if available
        rabbit_connection = None
        try:
            from src.mq_telegram.rabbit_connection import RabbitMQConnection
            rabbit_config = config_manager.get_config_value("mq_telegram")
            rabbit_connection = RabbitMQConnection(rabbit_config)
            print("RabbitMQ connection established")
        except Exception as rabbit_error:
            print(f"Could not establish RabbitMQ connection: {rabbit_error}")
            logger.warning(f"Could not establish RabbitMQ connection: {rabbit_error}")
        
        saxo_auth = SaxoAuth(config_manager, rabbit_connection)
        token = saxo_auth.get_token()
        print(f"Access Token: {token}")
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
