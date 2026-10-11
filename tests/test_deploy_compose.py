import pytest
import yaml

# Services that authenticate against the web server with its stored token.
# The token file lives under var/lib, so each of them must mount it: a service
# that can't read it silently generates a different token and gets 401s.
SERVICES_USING_THE_WEB_SERVER_TOKEN = ["web_server1", "trader1", "scheduler1"]


@pytest.fixture(scope="module")
def compose_services():
    with open("deploy/docker-compose.yml") as compose_file:
        return yaml.safe_load(compose_file)["services"]


@pytest.mark.parametrize("service", SERVICES_USING_THE_WEB_SERVER_TOKEN)
def test_service_using_the_web_server_token_mounts_var_lib(compose_services, service):
    container_paths = [volume.split(":")[1].rstrip("/") for volume in compose_services[service]["volumes"]]

    assert "/app/var/lib" in container_paths
