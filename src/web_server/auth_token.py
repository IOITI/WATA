import os
import logging
import json

logger = logging.getLogger(__name__)


class WebServerToken:
    """
    Handles token management for flask server.
    """

    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.token_file_path = self.config_manager.get_config_value("webserver.persistant.token_path")

    def save_token_data(self, token_data):
        """
        Save token data to a JSON file.
        """
        with open(self.token_file_path, "w") as token_file:
            json.dump(token_data, token_file)

    def get_token(self):
        try:
            if os.path.exists(self.token_file_path):
                with open(self.token_file_path, "r") as token_file:
                    token = json.load(token_file)
            else:
                # Generate a random token
                token = os.urandom(32).hex()
                self.save_token_data(token)
            return token
        except Exception as e:
            logger.error(f"Error getting token: {e}")
            raise
