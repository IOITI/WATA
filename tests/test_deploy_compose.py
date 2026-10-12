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


def _container_paths(service):
    return [volume.split(":")[1].rstrip("/") for volume in service["volumes"]]


def _mount_modes(service):
    """Container path -> "ro" or "rw"."""
    modes = {}
    for volume in service["volumes"]:
        _, container_path, *options = volume.split(":")
        modes[container_path.rstrip("/")] = "ro" if "ro" in options else "rw"
    return modes


@pytest.mark.parametrize("service", SERVICES_USING_THE_WEB_SERVER_TOKEN)
def test_service_using_the_web_server_token_mounts_var_lib(compose_services, service):
    assert "/app/var/lib" in _container_paths(compose_services[service])


def test_admin_is_only_started_on_request(compose_services):
    assert compose_services["admin1"]["profiles"] == ["admin"]


def test_admin_mounts_the_token_directories_it_reads_but_not_all_of_var_lib(compose_services):
    container_paths = _container_paths(compose_services["admin1"])

    # It calls web_server with its token and reads the Saxo token status.
    assert "/app/var/lib/web_server" in container_paths
    assert "/app/var/lib/saxo_auth" in container_paths
    assert "/app/var/lib/admin" in container_paths
    # var/lib also holds the PostgreSQL data files.
    assert "/app/var/lib" not in container_paths
    # The Docker socket would let a web-reachable container control the host.
    assert not any("docker.sock" in volume for volume in compose_services["admin1"]["volumes"])


def test_admin_mounts_the_config_directory_so_it_can_replace_the_file(compose_services):
    modes = _mount_modes(compose_services["admin1"])

    assert modes["/app/etc"] == "rw"
    # What else is kept in that directory is not the admin's to change.
    assert modes["/app/etc/rabbitmq"] == "ro"


def test_admin_can_read_every_log_but_only_write_its_own(compose_services):
    modes = _mount_modes(compose_services["admin1"])

    assert modes["/app/var/log"] == "ro"
    # Its log file and the audit trail.
    assert modes["/app/var/log/wata-admin"] == "rw"


def test_admin_writes_nowhere_else(compose_services):
    writable = sorted(path for path, mode in _mount_modes(compose_services["admin1"]).items() if mode == "rw")

    assert writable == [
        "/app/etc",
        "/app/var/lib/admin",
        "/app/var/lib/saxo_auth",
        "/app/var/lib/web_server",
        "/app/var/log/wata-admin",
    ]


def test_price_recorder_is_only_started_on_request_and_reachable_by_nothing(compose_services):
    recorder = compose_services["price_recorder1"]

    assert recorder["profiles"] == ["recorder"]
    assert recorder["environment"] == ["WATA_APP_ROLE=price_recorder"]
    # It serves nothing: no port, no route.
    assert "ports" not in recorder and "expose" not in recorder and "labels" not in recorder


def test_price_recorder_mounts_only_what_it_uses(compose_services):
    modes = _mount_modes(compose_services["price_recorder1"])

    assert modes == {
        "/app/var/log/wata-price-recorder": "rw",
        # The Saxo token it shares with, and renews like, the other services.
        "/app/var/lib/saxo_auth": "rw",
        "/app/var/lib/price_export": "rw",
        "/app/etc/config.json": "ro",
    }
    assert not any("docker.sock" in volume for volume in compose_services["price_recorder1"]["volumes"])


def test_no_trading_service_waits_for_the_price_recorder(compose_services):
    for name, service in compose_services.items():
        depends_on = service.get("depends_on") or {}
        assert "price_recorder1" not in depends_on, name


def test_the_start_script_knows_every_role_of_the_compose_file(compose_services):
    script = open("src/start_python_script.sh").read()
    roles = {
        variable.split("=", 1)[1]
        for service in compose_services.values()
        for variable in service.get("environment") or []
        if isinstance(variable, str) and variable.startswith("WATA_APP_ROLE=")
    }

    assert "price_recorder" in roles
    for role in roles:
        assert f'"{role}")' in script, role


def test_admin_is_routed_on_its_own_domain_behind_its_own_allowlist(compose_services):
    labels = compose_services["admin1"]["labels"]

    assert any("routers.admin.rule=Host(`${ADMIN_DOMAIN" in label for label in labels)
    assert any("admin-ip-allowlist.ipallowlist.sourcerange=${ADMIN_ALLOWED_IPS" in label for label in labels)
    middlewares = next(label for label in labels if "routers.admin.middlewares=" in label)
    assert "admin-ip-allowlist" in middlewares
    # Not the allowlist of the webhook route, which contains TradingView's servers.
    assert "api-ip-whitelist" not in middlewares
