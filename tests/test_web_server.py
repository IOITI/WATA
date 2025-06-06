import unittest
import os
import json # Added json import
from unittest.mock import patch
from fastapi.testclient import TestClient
from src.web_server import app
from src.configuration import ConfigurationManager # Import for patching target

TEST_SECRET_TOKEN = os.environ.get("TEST_SECRET_TOKEN", "default_mock_token_for_testing")

@patch.object(ConfigurationManager, 'validate_config', return_value=None)
class FastApiTest(unittest.TestCase):

    original_wata_config_path = None
    config_path = "/tmp/dummy_test_config.json"
    log_dir = "/tmp/test_logs/wata-api"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Store original WATA_CONFIG_PATH and set it for the test class
        cls.original_wata_config_path = os.environ.get("WATA_CONFIG_PATH")
        os.environ["WATA_CONFIG_PATH"] = cls.config_path

        os.makedirs(cls.log_dir, exist_ok=True)

        dummy_config_content = {
            "logging": {
                "level": "INFO",
                "log_to_file": True,
                "persistant": {
                    "log_path": "/tmp/test_logs"
                },
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            },
            "webserver": {
                "persistant": {
                    "token_path": "/tmp/dummy_web_token.json"
                }
            },
            "network": {
                "allowed_ips": ["127.0.0.1"]
            }
        }
        with open(cls.config_path, 'w') as f:
            json.dump(dummy_config_content, f)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if cls.original_wata_config_path is None:
            if "WATA_CONFIG_PATH" in os.environ:
                 del os.environ["WATA_CONFIG_PATH"]
        else:
            os.environ["WATA_CONFIG_PATH"] = cls.original_wata_config_path

        if os.path.exists(cls.config_path):
            os.remove(cls.config_path)
        if os.path.exists("/tmp/dummy_web_token.json"):
             os.remove("/tmp/dummy_web_token.json")
        if os.path.exists(cls.log_dir) and not os.listdir(cls.log_dir): # Check if empty
            try: # Handle potential race condition if logs are written during cleanup
                os.rmdir(cls.log_dir)
            except OSError: pass # Ignore if not empty
        if os.path.exists("/tmp/test_logs") and not os.listdir("/tmp/test_logs"):
            try:
                os.rmdir("/tmp/test_logs")
            except OSError: pass


    def setUp(self, mock_validate_config_class_level): # mock from class decorator
        # app is imported once globally, its config is set then.
        # We use TestClient which allows per-request header overrides if needed.
        self.client = TestClient(app)
        self.SECRET_TOKEN = TEST_SECRET_TOKEN
        self.secret_token_patch = patch('src.web_server.SECRET_TOKEN', self.SECRET_TOKEN)
        self.secret_token_patch.start()

    def tearDown(self, mock_validate_config_class_level=None):
        self.secret_token_patch.stop()

    @patch("src.web_server.ALLOWED_IPS", ["1.2.3.4"])
    def test_webhook_unauthorized_ip_forbidden(self, mock_allowed_ips_config_method, mock_validate_config_class_level):
        response = self.client.post(
            "/webhook", headers={"Authorization": "Bearer wrong_token"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "Forbidden"})

    @patch("src.web_server.ALLOWED_IPS", ["testclient"])
    def test_webhook_unauthorized_ip_allowed(self, mock_allowed_ips_config_method, mock_validate_config_class_level):
        response = self.client.post(
            "/webhook", headers={"Authorization": "Bearer wrong_token"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Unauthorized"})

    @patch("src.web_server.ALLOWED_IPS", ["testclient"])
    def test_webhook_success(self, mock_allowed_ips_config_method, mock_validate_config_class_level):
        response = self.client.post(
            "/webhook",
            headers={"Authorization": f"Bearer {self.SECRET_TOKEN}"},
            json={"key": "value"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success"})

    @patch("src.web_server.ALLOWED_IPS", ["192.168.1.100"])
    def test_webhook_ip_filtering_forbidden(self, mock_allowed_ips_config_method, mock_validate_config_class_level):
        response = self.client.post(
            "/webhook",
            headers={"Authorization": f"Bearer {self.SECRET_TOKEN}"}, # Token is valid
            json={"key": "value"},
        )
        self.assertEqual(response.status_code, 403) # But IP is not allowed
        self.assertEqual(response.json(), {"error": "Forbidden"})

    @patch("src.web_server.ALLOWED_IPS", ["127.0.0.1", "testclient"])
    def test_webhook_ip_filtering_allowed(self, mock_allowed_ips_config_method, mock_validate_config_class_level):
        response = self.client.post(
            "/webhook",
            headers={"Authorization": f"Bearer {self.SECRET_TOKEN}"}, # Token is valid
            json={"key": "value"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success"})

if __name__ == "__main__":
    unittest.main()
