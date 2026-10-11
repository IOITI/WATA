import os
import logging
import json
import base64
import stat
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)


class WebServerToken:
    """
    Handles token management for flask server with secure encryption.
    """

    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.token_file_path = self.config_manager.get_config_value("webserver.persistant.token_path")
        
        # Ensure token directory exists
        os.makedirs(os.path.dirname(self.token_file_path), exist_ok=True)
        
        # Initialize encryption
        self._initialize_encryption()

    def _initialize_encryption(self):
        """
        Initialize encryption for secure token storage.
        The key is derived from app_secret plus a salt stored in a separate file.
        """
        key_file_path = os.path.join(os.path.dirname(self.token_file_path), ".webserver_key_salt")
        app_secret = self.config_manager.get_config_value("webserver.app_secret", default="DEFAULT_SECRET_CHANGE_ME")
        
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
        password = app_secret.encode()
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
        if isinstance(data, str):
            data = data.encode()
        elif not isinstance(data, bytes):
            data = json.dumps(data).encode()
        return self.cipher.encrypt(data)
    
    def _decrypt_data(self, encrypted_data):
        """Decrypt stored data"""
        try:
            decrypted_data = self.cipher.decrypt(encrypted_data)
            try:
                # Try to parse as JSON
                return json.loads(decrypted_data)
            except json.JSONDecodeError:
                # Return as string if not JSON
                return decrypted_data.decode()
        except Exception as e:
            logger.error(f"Error decrypting data: {e}")
            return None

    def save_token_data(self, token_data):
        """
        Save token data to file in encrypted format.
        """
        try:
            # Encrypt the token data
            encrypted_data = self._encrypt_data(token_data)
            
            # Store in file
            with open(self.token_file_path, "wb") as token_file:
                token_file.write(encrypted_data)
                
            # Set restrictive permissions on the token file
            os.chmod(self.token_file_path, stat.S_IRUSR | stat.S_IWUSR)
            
            logger.info("Token data securely saved to file with encryption")
        except Exception as e:
            logger.error(f"Error saving token data: {e}")
            raise

    def get_token(self):
        """
        Get token from file, decrypting it if it exists, or generate a new one.
        """
        try:
            if os.path.exists(self.token_file_path):
                with open(self.token_file_path, "rb") as token_file:
                    encrypted_data = token_file.read()
                token = self._decrypt_data(encrypted_data)
                logger.info("Token data retrieved and decrypted from file")
            else:
                # Generate a random token
                token = os.urandom(32).hex()
                self.save_token_data(token)
                logger.info("New random token generated and saved")
            return token
        except Exception as e:
            logger.error(f"Error getting token: {e}")
            raise
