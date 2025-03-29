import requests
import secrets
import urllib.parse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException
import json
import time
import datetime
import os
import logging

from src.configuration import ConfigurationManager

logger = logging.getLogger(__name__)


class SaxoAuth:
    """
    Handles authentication and token management for Saxo.
    """

    def __init__(self, config_manager):
        self.state = secrets.token_hex(16)
        self.config_manager = config_manager
        self.username = self.config_manager.get_config_value(
            "authentication.saxo.username"
        )
        self.password = self.config_manager.get_config_value(
            "authentication.saxo.password"
        )
        self.app_data = self.config_manager.get_config_value(
            "authentication.saxo.app_config_object"
        )
        self.token_file_path = self.config_manager.get_config_value(
            "authentication.persistant.token_path"
        )

    # def load_credentials(self, config_path):
    #     """
    #     Load credentials from a JSON file.
    #     """
    #     if not os.path.exists(config_path):
    #         logger.error(f"Config file not found: {config_path}")
    #         raise FileNotFoundError(f"Config file not found: {config_path}")
    #     try:
    #         with open(config_path, "r") as file:
    #             self.credentials = json.load(file)
    #     except json.JSONDecodeError as e:
    #         logger.error(f"Error loading credentials: {e}")
    #         raise
    #     self.username = self.credentials["authentication"]["saxo"]["username"]
    #     self.password = self.credentials["authentication"]["saxo"]["password"]
    #     self.app_data = self.credentials["authentication"]["saxo"]["app_config_object"]

    def get_authorization_code(self):
        """
        Generate an authorization code by navigating to the authorization URL and logging in.
        """
        try:
            params = {
                "response_type": "code",
                "client_id": self.app_data["AppKey"],
                "redirect_uri": self.app_data["RedirectUrls"][0],
                "state": self.state,
            }
            auth_url = f"{self.app_data['AuthorizationEndpoint']}?{urllib.parse.urlencode(params)}"

            options = Options()
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-infobars")
            options.add_argument("--start-maximized")
            options.add_argument("--disable-notifications")
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            driver = webdriver.Chrome(options=options)
            driver.get(auth_url)
            time.sleep(1)

            username_field = driver.find_element(By.ID, "field_userid")
            username_field.send_keys(self.username)
            time.sleep(1)

            password_field = driver.find_element(By.ID, "field_password")
            password_field.send_keys(self.password)
            time.sleep(1)

            login_button = driver.find_element(By.ID, "button_login")
            login_button.click()
            time.sleep(4)

            # Scroll down in the specific div
            driver.execute_script(
                """
                var element = document.querySelector("#app > div > div > div > div.grid.grid--scroll > div > div > div > div > div:nth-child(6) > p:nth-child(2)")
                // var element = document.querySelector("#app > div > div > div.grid.tst-disclaimer-body.grid--scroll > div > div > div > p:nth-child(14)")
                element.scrollIntoView();
                window.scrollBy(0, 1350);
            """
            )
            time.sleep(1)

            # Remove the disabled attribute from the button
            driver.execute_script(
                """
                    var button = document.querySelector('button[data-test-id="risk-warning-accept"]');
                    if (button) {
                        button.removeAttribute('disabled');
                    }
                """
            )

            # Click the button
            accept_button = driver.find_element(
                By.XPATH, '//*[@id="app"]/div/div/div/div[2]/button'
                #By.XPATH,'// *[ @ id = "app"] / div / div / div[2] / div / div / button'
            )
            accept_button.click()
            time.sleep(1)

            redirect_url = driver.current_url
            code = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query)[
                "code"
            ][0]
            driver.quit()
            if code:
                logger.info("Successfully getting code from AuthorizationEndpoint")
            return code
        except NoSuchElementException as e:
            logger.error(f"Element not found: {e}")
            raise
        except TimeoutException as e:
            logger.error(f"Timeout waiting for element: {e}")
            raise
        except Exception as e:
            logger.error(f"Unknown error: {e}")
            raise

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
        Save token data to a JSON file.
        """
        token_data["date_saved"] = datetime.datetime.now().isoformat()
        with open(self.token_file_path, "w") as token_file:
            json.dump(token_data, token_file)

    def is_token_expired(self, token_data):
        """
        Check if the access token is expired.
        """
        if (
            not token_data
            or "date_saved" not in token_data
            or "expires_in" not in token_data
        ):
            return True
        date_saved = datetime.datetime.fromisoformat(token_data["date_saved"])
        expires_in_second = token_data["expires_in"] - 120
        expiration_time = date_saved + datetime.timedelta(
            seconds=expires_in_second
        )
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
            return True
        date_saved = datetime.datetime.fromisoformat(token_data["date_saved"])
        refresh_token_expires_in_second = token_data["refresh_token_expires_in"] - 60
        refresh_token_expiration_time = date_saved + datetime.timedelta(
            seconds=refresh_token_expires_in_second
        )
        return datetime.datetime.now() > refresh_token_expiration_time

    def get_token(self):
        """
        Get a valid access token, either by refreshing an existing token or obtaining a new one.
        """
        try:
            if os.path.exists(self.token_file_path):
                with open(self.token_file_path, "r") as token_file:
                    token_data = json.load(token_file)
            else:
                token_data = {}

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
                logger.info("Give token for Saxo API")
            return token_data["access_token"]
        except FileNotFoundError as e:
            logger.error(f"Token file not found: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding token file: {e}")
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
        saxo_auth = SaxoAuth(config_manager)
        token = saxo_auth.get_token()
        print(f"Access Token: {token}")
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
