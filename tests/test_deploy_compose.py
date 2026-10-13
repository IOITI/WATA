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
        # Backtests: their results, and the prices fetched for them.
        "/app/var/lib/backtests",
        "/app/var/lib/price_history",
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


def _routers(compose_services):
    """Router name -> {setting: value}, from the Traefik labels of every service."""
    routers = {}
    prefix = "traefik.http.routers."
    for service in compose_services.values():
        for label in service.get("labels") or []:
            if label.startswith(prefix):
                key, value = label[len(prefix):].split("=", 1)
                name, setting = key.split(".", 1)
                routers.setdefault(name, {})[setting] = value
    return routers


def test_second_https_port_stays_on_the_machine_unless_asked(compose_services):
    traefik = compose_services["traefik"]

    assert "--entrypoints.adminsecure.address=:8443" in traefik["command"]
    # Published on the loopback address by default: an upgrade opens nothing.
    assert "${ADMIN_PORT:-127.0.0.1:8443}:8443" in traefik["ports"]
    assert not any(port.endswith(":8443") and not port.startswith("${ADMIN_PORT") for port in traefik["ports"])


def test_admin_is_on_the_usual_https_port_unless_asked(compose_services):
    assert _routers(compose_services)["admin"]["entrypoints"] == "${ADMIN_ENTRYPOINT:-websecure}"


def test_webhook_keeps_its_route_when_the_admin_is_given_its_domain(compose_services):
    routers = _routers(compose_services)

    # Two routes for one domain on one port: the higher priority wins, and a
    # route without one has the length of its rule, which is more than 1.
    assert routers["admin"]["priority"] == "1"
    assert "priority" not in routers["fastapi"]
    assert routers["fastapi"]["entrypoints"] == "websecure"


def test_every_route_names_its_entrypoint(compose_services):
    # A route that names none is attached to all of them, the admin's port included.
    for name, router in _routers(compose_services).items():
        assert router.get("entrypoints"), name


def test_admin_keeps_backtests_and_their_prices_outside_the_container(compose_services):
    modes = _mount_modes(compose_services["admin1"])

    # Written by the admin: the prices it fetches and the results of its runs must survive a restart.
    assert modes["/app/var/lib/price_history"] == "rw"
    assert modes["/app/var/lib/backtests"] == "rw"


def test_backtests_cannot_take_more_than_one_processor_from_the_services_that_trade(compose_services):
    limits = compose_services["admin1"]["deploy"]["resources"]["limits"]

    assert float(limits["cpus"]) <= 1.0
    # No other service is capped: the trader and the position monitor keep what the machine has.
    for name, service in compose_services.items():
        if name != "admin1":
            assert "cpus" not in ((service.get("deploy") or {}).get("resources") or {}).get("limits", {}), name


# ── The image and what CI does with it ──


@pytest.fixture(scope="module")
def dockerfile():
    with open("Dockerfile") as image_file:
        return [line.strip() for line in image_file if line.strip() and not line.strip().startswith("#")]


@pytest.fixture(scope="module")
def ci_jobs():
    with open(".github/workflows/ci.yml") as workflow:
        return yaml.safe_load(workflow)["jobs"]


def _image_python(dockerfile) -> str:
    """The Python the server builds the image with: the one named when nothing else is asked."""
    return next(line.split("=", 1)[1] for line in dockerfile if line.startswith("ARG PYTHON_VERSION="))


def test_the_image_is_built_on_the_python_named_at_its_top(dockerfile):
    assert dockerfile[0].startswith("ARG PYTHON_VERSION=3.")
    assert dockerfile[1] == "FROM python:${PYTHON_VERSION}"


def test_the_image_installs_its_libraries_before_it_copies_the_code(dockerfile):
    """Otherwise every deploy downloads them again, while the trading services are stopped."""
    libraries = next(i for i, line in enumerate(dockerfile) if line.startswith("RUN pip install"))
    code = next(i for i, line in enumerate(dockerfile) if line.startswith("COPY ./src"))

    assert dockerfile.index("COPY ./requirements.txt /app/requirements.txt") < libraries < code
    # Nothing compiled on the server: a library with no ready-made build fails at once.
    assert "--only-binary=:all:" in dockerfile[libraries]


@pytest.mark.parametrize("job", ["build-and-test", "image", "infrastructure"])
def test_ci_runs_on_the_python_of_the_image(dockerfile, ci_jobs, job):
    assert _image_python(dockerfile) in ci_jobs[job]["strategy"]["matrix"]["python-version"]


def test_ci_builds_the_image_for_each_python_and_as_the_server_does(ci_jobs):
    build = next(step["run"] for step in ci_jobs["image"]["steps"] if step.get("name") == "Build the image")
    on_the_server = open("docker_build.sh").read()

    assert "--build-arg PYTHON_VERSION=${{ matrix.python-version }}" in build
    assert "--platform=linux/amd64" in build and "--platform=linux/amd64" in on_the_server


def test_each_python_keeps_its_own_coverage_report(ci_jobs):
    """Two uploads under one name make the second one fail."""
    upload = next(step for step in ci_jobs["build-and-test"]["steps"] if "upload-artifact" in step.get("uses", ""))

    assert "${{ matrix.python-version }}" in upload["with"]["name"]


def test_ci_checks_that_nothing_is_installed_that_requirements_does_not_name(ci_jobs):
    for job in ("build-and-test", "image"):
        commands = "\n".join(step.get("run", "") for step in ci_jobs[job]["steps"])
        assert "pip freeze > installed.txt" in commands, job
        assert "tests/requirements_lock.py requirements.txt installed.txt" in commands, job


def test_the_live_tests_get_the_database_and_the_queue_of_the_compose_file(compose_services, ci_jobs):
    services = ci_jobs["infrastructure"]["services"]

    assert services["postgres"]["image"] == compose_services["postgres1"]["image"]
    assert services["rabbitmq"]["image"] == compose_services["rabbitmq1"]["image"]
    step = next(step for step in ci_jobs["infrastructure"]["steps"] if "test_infrastructure_live.py" in step.get("run", ""))
    # Without it the tests would be skipped, and the job green for nothing.
    assert step["env"]["WATA_TEST_REQUIRE_LIVE"] == "1"
    assert step["env"]["WATA_TEST_POSTGRES_DSN"].endswith("@localhost:5432/wata")
