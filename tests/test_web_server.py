import unittest
from unittest.mock import patch
from src.web_server import app


class FlaskTest(unittest.TestCase):

    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        # Load the secret token from a file
        with open("/your_token_path.txt", "r") as file:
            self.SECRET_TOKEN = file.read().strip()

    def test_webhook_unauthorized(self):
        response = self.app.post(
            "/webhook", headers={"Authorization": "Bearer wrong_token"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json, {"error": "Unauthorized"})

    def test_webhook_success(self):
        # Assuming you have a valid token set in your environment or directly in your app for testing
        response = self.app.post(
            "/webhook",
            headers={"Authorization": f"Bearer {self.SECRET_TOKEN}"},
            json={"key": "value"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "success"})

    @patch("src.web_server.request.remote_addr", new_callable=lambda: "127.0.0.1")
    def test_webhook_ip_filtering(self, mock_remote_addr):
        response = self.app.post(
            "/webhook",
            headers={"Authorization": f"Bearer {self.SECRET_TOKEN}"},
            json={"key": "value"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json, {"error": "Forbidden"})


if __name__ == "__main__":
    unittest.main()
