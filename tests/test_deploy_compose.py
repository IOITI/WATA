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


def test_the_capital_com_feed_is_only_started_on_request_and_reachable_by_nothing(compose_services):
    feed = compose_services["capital_feed1"]

    assert feed["profiles"] == ["capital"]
    # It serves nothing: no port, no route. Its live prices leave through RabbitMQ.
    assert "ports" not in feed and "expose" not in feed and "labels" not in feed
    for name, service in compose_services.items():
        assert "capital_feed1" not in (service.get("depends_on") or {}), name


def test_the_capital_com_login_is_handed_to_the_feed_alone_and_never_written_in_the_file(compose_services):
    variables = ("CAPITAL_API_KEY", "CAPITAL_IDENTIFIER", "CAPITAL_PASSWORD")

    assert compose_services["capital_feed1"]["environment"] == [
        "WATA_APP_ROLE=capital_feed", *(f"{name}=${{{name}:-}}" for name in variables),
    ]
    for name, service in compose_services.items():
        if name == "capital_feed1":
            continue
        assert not any("CAPITAL_" in str(variable) for variable in service.get("environment") or []), name
    example = open("deploy/.env.example", encoding="utf-8").read()
    for name in variables:
        assert f"# {name}=" in example


def test_the_outside_watchers_address_is_handed_to_the_position_monitor_alone(compose_services):
    assert compose_services["position_monitor1"]["environment"] == [
        "WATA_APP_ROLE=position_monitor", "WATA_HEARTBEAT_URL=${WATA_HEARTBEAT_URL:-}",
    ]
    for name, service in compose_services.items():
        if name == "position_monitor1":
            continue
        assert not any("WATA_HEARTBEAT_URL" in str(variable) for variable in service.get("environment") or []), name
    assert "# WATA_HEARTBEAT_URL=" in open("deploy/.env.example", encoding="utf-8").read()


def test_the_capital_com_feed_mounts_only_what_it_uses(compose_services):
    modes = _mount_modes(compose_services["capital_feed1"])

    assert modes == {
        "/app/var/log/wata-capital-feed": "rw",
        # Its day files, which the admin reads through its own mount of the history of prices.
        "/app/var/lib/price_history/capital": "rw",
        "/app/etc/config.json": "ro",
    }
    assert not any("docker.sock" in volume for volume in compose_services["capital_feed1"]["volumes"])


def test_the_install_script_makes_the_folders_of_the_capital_com_feed():
    script = open("deploy/install_package.sh").read()

    assert 'mkdir -p "$APP_BASE_DIR/var/log/wata-capital-feed"' in script
    assert 'mkdir -p "$APP_BASE_DIR/var/lib/price_history/capital"' in script


def test_the_ta_engine_is_only_started_on_request_and_reachable_by_nothing(compose_services):
    engine = compose_services["ta_engine1"]

    assert engine["profiles"] == ["ta"]
    assert engine["environment"] == ["WATA_APP_ROLE=ta_engine"]
    # It serves nothing: no port, no route. Its signals leave through the web server.
    assert "ports" not in engine and "expose" not in engine and "labels" not in engine
    for name, service in compose_services.items():
        assert "ta_engine1" not in (service.get("depends_on") or {}), name


def test_the_ta_engine_has_room_for_its_runners_beyond_their_heaps(compose_services):
    # Its two runners' heaps, the native memory PineTS's compiles keep beside them, and the engine:
    # at 1024M, a shadow runner of 256 MB of heap held 729 MB and the container 768 MB.
    assert compose_services["ta_engine1"]["deploy"]["resources"]["limits"]["memory"] == "2560M"


def test_the_ta_engine_writes_only_its_log_and_reads_only_what_it_runs(compose_services):
    modes = _mount_modes(compose_services["ta_engine1"])

    assert modes == {
        "/app/var/log/wata-ta-engine": "rw",
        # The rules and scripts the admin keeps: not the rest of the admin's folder, its credentials.
        "/app/var/lib/ta": "ro",
        # The web server's token, to send the signals of live rules with.
        "/app/var/lib/web_server": "ro",
        "/app/etc/config.json": "ro",
    }
    hosts = {volume.split(":")[0] for volume in compose_services["ta_engine1"]["volumes"]}
    assert "../../var/lib/admin/ta" in hosts and "../../var/lib/admin" not in hosts
    assert not any("docker.sock" in volume for volume in compose_services["ta_engine1"]["volumes"])


def test_the_install_script_makes_the_folders_of_the_ta_engine():
    script = open("deploy/install_package.sh").read()

    assert 'mkdir -p "$APP_BASE_DIR/var/log/wata-ta-engine"' in script
    assert 'mkdir -p "$APP_BASE_DIR/var/lib/admin/ta"' in script
    assert "# COMPOSE_PROFILES=ta" in open("deploy/.env.example", encoding="utf-8").read()


def test_the_install_script_gives_the_operator_the_command_that_makes_mcp_tokens():
    script = open("deploy/install_package.sh").read()

    assert "cat << 'EOF' > /usr/local/bin/watamcptoken" in script
    assert 'docker exec -it admin1 python -m src.admin.cli --mcp-token "$@"' in script
    assert "chmod +x /usr/local/bin/watamcptoken" in script
    assert "watamcptoken create <name>" in script


def test_the_mcp_tokens_stay_in_the_admins_folder_out_of_the_ta_engines_reach(compose_services):
    from src.admin.context import AdminSettings

    assert AdminSettings.mcp_tokens_path.startswith("/app/var/lib/admin/")
    assert _mount_modes(compose_services["admin1"])["/app/var/lib/admin"] == "rw"
    assert "/app/var/lib/admin" not in _mount_modes(compose_services["ta_engine1"])


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


def test_http_and_https_ports_of_the_server_are_80_and_443_unless_asked(compose_services):
    traefik = compose_services["traefik"]

    assert "${HTTP_PORT:-80}:80" in traefik["ports"]
    assert "${HTTPS_PORT:-443}:443" in traefik["ports"]
    # The ports inside the container stay the same: the routes and the redirection name them.
    assert "--entrypoints.web.address=:80" in traefik["command"]
    assert "--entrypoints.websecure.address=:443" in traefik["command"]
    assert len(traefik["ports"]) == 3


def test_http_is_redirected_to_the_port_https_is_published_on(compose_services):
    # The name of the entrypoint would send browsers to 443 whatever HTTPS_PORT says.
    assert (
        "--entrypoints.web.http.redirections.entryPoint.to=:${HTTPS_PORT:-443}"
        in compose_services["traefik"]["command"]
    )


def test_the_install_script_opens_the_ports_of_the_env_file():
    script = open("deploy/install_package.sh").read()

    assert 'sudo ufw allow "$HTTP_PORT/tcp"' in script
    assert 'sudo ufw allow "$HTTPS_PORT/tcp"' in script
    assert "HTTP_PORT=$(env_port HTTP_PORT 80)" in script
    assert "HTTPS_PORT=$(env_port HTTPS_PORT 443)" in script


def test_the_env_example_names_every_port_the_compose_file_reads():
    compose = open("deploy/docker-compose.yml").read()
    example = open("deploy/.env.example").read()

    for variable in ("HTTP_PORT", "HTTPS_PORT", "ADMIN_PORT"):
        assert "${" + variable + ":-" in compose, variable
        assert f"# {variable}=" in example, variable


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
    # The admin, a backtest's command with a day of recorded turbos, the Pine runner it starts,
    # and one of Claude's Pine studies beside them, on a server of 8 GB.
    assert limits["memory"] == "3072M"
    # No other service is capped: the trader and the position monitor keep what the machine has.
    for name, service in compose_services.items():
        if name != "admin1":
            assert "cpus" not in ((service.get("deploy") or {}).get("resources") or {}).get("limits", {}), name


def test_the_database_keeps_the_price_tables_in_memory(compose_services):
    postgres = compose_services["postgres1"]
    command = postgres["command"]
    settings = dict(value.split("=", 1) for flag, value in zip(command[1::2], command[2::2]) if flag == "-c")

    assert command[0] == "postgres" and len(command) == 1 + 2 * len(settings)
    assert postgres["deploy"]["resources"]["limits"]["memory"] == "1536M"
    # A third of the container for its own buffers, the file cache the planner counts on beside them.
    assert settings == {"shared_buffers": "512MB", "effective_cache_size": "1GB", "work_mem": "16MB"}
    # Parallel queries share memory through /dev/shm, 64 MB in a container unless it is said.
    assert postgres["shm_size"] == "256m"


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


def test_the_image_has_the_node_the_interface_is_built_with_before_the_code(dockerfile):
    node = next(index for index, line in enumerate(dockerfile) if line.startswith("COPY --from=node:"))
    code = next(index for index, line in enumerate(dockerfile) if line.startswith("COPY ./src"))
    version = open("admin_ui/.nvmrc").read().strip()

    # The binary alone, from Node's own image: no npm, the runner being one file built by package.sh.
    assert dockerfile[node] == f"COPY --from=node:{version}-bookworm-slim /usr/local/bin/node /usr/local/bin/node"
    assert node < code
    assert "npm run build:runner" in open("package.sh").read()


def test_the_checker_of_tradingviews_rules_is_built_beside_the_runner_never_inside_it(ci_jobs):
    assert "npm run build:runner && npm run build:checker" in open("package.sh").read()
    built = next(step["run"] for step in ci_jobs["image"]["steps"] if step.get("name") == "Build the Pine runner")
    assert "npm run build:checker" in built
    config = open("admin_ui/vite.checker.config.ts").read()
    assert "outDir: '../src/ta_engine/checker'" in config and "entryFileNames: 'check.mjs'" in config
    assert "src/ta_engine/checker/" in open(".gitignore").read()


def test_ci_builds_the_pine_runner_into_the_image_and_runs_it_there(ci_jobs):
    steps = ci_jobs["image"]["steps"]
    names = [step.get("name") for step in steps]

    assert names.index("Build the Pine runner") < names.index("Build the image")
    check = next(step["run"] for step in steps if step.get("name") == "The TA engine's Pine runner runs in it, confined")
    assert "node --version" in check and "python -m src.ta_engine.runner" in check


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


def test_ci_builds_the_image_once_exactly_as_the_server_does(dockerfile, ci_jobs):
    """The server asks for no Python: what it gets is the one the Dockerfile names."""
    steps = ci_jobs["image-as-deployed"]["steps"]
    build = next(step["run"] for step in steps if step.get("name") == "Build the image with the server's own command")
    on_the_server = [line.strip() for line in open("docker_build.sh") if line.startswith("docker build")]

    assert on_the_server == [build]
    assert "--build-arg" not in build
    check = next(step["run"] for step in steps if "python --version" in step.get("run", ""))
    assert "ARG PYTHON_VERSION=" in check and 'grep -F "Python $expected."' in check


def test_the_documents_name_the_python_of_the_image(dockerfile):
    project = open("AGENTS.md", encoding="utf-8").read().split("## Service map")[0]

    assert f"Python {_image_python(dockerfile)}," in project
