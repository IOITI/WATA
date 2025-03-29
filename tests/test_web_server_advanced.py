import unittest
from unittest.mock import patch, Mock
from src.web_server import app, SECRET_TOKEN, ALLOWED_IPS

class TestBeforeRequest(unittest.TestCase):

    @patch('flask.request.remote_addr', return_value='127.0.0.1')
    @patch('flask.abort')
    def test_allowed_ip(self, mock_abort, mock_remote_addr):
        with app.test_client() as client:
            response = client.get('/webhook')
            mock_abort.assert_not_called()
            mock_remote_addr.assert_called_once_with()
            self.assertEqual(response.status_code, 200)

    @patch('flask.request.remote_addr', return_value='52.89.214.239')
    @patch('flask.abort')
    def test_forbidden_ip(self, mock_abort, mock_remote_addr):
        with app.test_client() as client:
            response = client.get('/webhook')
            mock_abort.assert_called_once_with(403, description="Forbidden")
            mock_remote_addr.assert_called_once_with()
            self.assertEqual(response.status_code, 403)


class TestWebhook(unittest.TestCase):

    def setUp(self):
        self.app = app.test_client()

    def test_webhook_unauthorized(self):
        response = self.app.open('/webhook', content_type='application/json', data=json.dumps({}),
                                  headers={'Authorization': 'Bearer invalid_token'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json['description'], 'Unauthorized')

    def test_webhook_forbidden_ip(self):
        response = self.app.open('/webhook', content_type='application/json', data=json.dumps({}),
                                  headers={'Authorization': f'Bearer {SECRET_TOKEN}'},
                                  remote_addr='123.456.789.0')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json['description'], 'Forbidden')

    def test_webhook_success(self):
        response = self.app.open('/webhook', content_type='application/json', data=json.dumps({}),
                                  headers={'Authorization': f'Bearer {SECRET_TOKEN}'},
                                  remote_addr=ALLOWED_IPS[0])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['status'], 'success')

if __name__ == '__main__':
    unittest.main()