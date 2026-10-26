import re

import pytest
import yaml

# Services that authenticate against the web server with its stored token.
# The token file lives under var/lib, so each of them must mount it: a service
# that can't read it silently generates a different token and gets 401s.
SERVICES_USING_THE_WEB_SERVER_TOKEN = ["web_server1", "trader1", "scheduler1"]


@pytest.fixture(scope="module")
def compose():
    with open("deploy/docker-compose.yml") as compose_file:
        return yaml.safe_load(compose_file)


@pytest.fixture(scope="module")
def compose_services(compose):
    return compose["services"]


# Each service's own key and every service's public one (src/vault/), both read-only.
KEYS = {"/app/keys/own": "ro", "/app/keys/public": "ro"}


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

    # It reads the web server's token file of before; Saxo's login it reads from PostgreSQL.
    assert "/app/var/lib/web_server" in container_paths
    assert "/app/var/lib/saxo_auth" not in container_paths
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
        "/app/var/lib/web_server",
        "/app/var/log/wata-admin",
    ]


def test_price_recorder_is_only_started_on_request_and_reachable_by_nothing(compose_services):
    recorder = compose_services["price_recorder1"]

    assert recorder["profiles"] == ["recorder"]
    assert recorder["environment"] == ["WATA_APP_ROLE=price_recorder", "SAXO_REDIRECT_URL=${SAXO_REDIRECT_URL:-}"]
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
        **KEYS,
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
        "WATA_APP_ROLE=position_monitor", "SAXO_REDIRECT_URL=${SAXO_REDIRECT_URL:-}",
        "WATA_HEARTBEAT_URL=${WATA_HEARTBEAT_URL:-}",
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
        **KEYS,
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
        # The web server's token file of before, read until the internal token has its row.
        "/app/var/lib/web_server": "ro",
        "/app/etc/config.json": "ro",
        **KEYS,
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


def test_every_wata_service_is_handed_dockers_stop(compose_services):
    services = {
        name: service for name, service in compose_services.items()
        if service.get("image") == "wata-base:latest" and service.get("profiles") != ["tools"]
    }

    assert len(services) == 11
    for name, service in services.items():
        # tini as PID 1 hands SIGTERM to Python. Without it, a Python with no handler would ignore the
        # signal (PID 1 is spared what it does not handle) and Docker kill it once the time allowed is up.
        assert service.get("init") is True, name
    # The signal in hand (trader) and the sale under way (position monitor) are finished first.
    assert compose_services["trader1"]["stop_grace_period"] == "60s"
    assert compose_services["position_monitor1"]["stop_grace_period"] == "45s"


def test_the_start_script_hands_its_place_to_python():
    script = open("src/start_python_script.sh").read()
    starts = [line.strip() for line in script.splitlines() if "python -u" in line and not line.strip().startswith("#")]

    # A shell left in between would never pass Docker's stop on to Python.
    assert len(starts) == 11
    assert all(line.startswith("exec python -u ") for line in starts)


def test_ci_sees_a_service_stop_at_once_and_cleanly(ci_jobs):
    check = next(step["run"] for step in ci_jobs["image"]["steps"]
                 if step.get("name") == "A service stops at once and cleanly on Docker's stop")

    assert "--init" in check and "docker stop" in check and "WATA scheduler stopped" in check


def test_the_deploy_stops_the_version_running_now():
    with open("deploy/tools/ansible/playbooks/deploy_app.yml") as playbook:
        tasks = yaml.safe_load(playbook)[0]["tasks"]
    stop = next(task for task in tasks if task.get("name") == "Stop the application")

    # A shell, for its cd: `command` runs none, and the stop failed every time.
    assert "command" not in stop
    assert 'cd "/app/wata/$(cat /app/wata/VERSION)/deploy" && docker compose down' in stop["ansible.builtin.shell"]


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


# ── The credentials' keys ──


def _role(service) -> str | None:
    for variable in service.get("environment") or []:
        if isinstance(variable, str) and variable.startswith("WATA_APP_ROLE="):
            return variable.split("=", 1)[1]
    return None


def test_each_service_is_given_its_own_key_and_no_other(compose_services):
    from src.vault.keys import ROLES

    given = {}
    for name, service in compose_services.items():
        private = [volume for volume in service.get("volumes") or [] if volume.startswith("../../keys/") and "/public" not in volume]
        if _role(service) in ROLES:
            assert private == [f"../../keys/{_role(service)}:/app/keys/own:ro"], name
            assert "../../keys/public:/app/keys/public:ro" in service["volumes"], name
            given[_role(service)] = name
        else:
            # The control service, PostgreSQL, RabbitMQ, Traefik, the backups and the tools: no private key.
            assert private == [], name
    assert sorted(given) == sorted(ROLES)
    # The keys are nowhere else: not under var/lib, which five services mount whole, nor etc.
    for name, service in compose_services.items():
        for volume in service.get("volumes") or []:
            assert not volume.startswith(("../../var/lib/keys", "../../etc/keys")), name


def test_the_services_that_read_no_database_before_wait_for_it_now(compose_services):
    for name in ("telegram1", "scheduler1", "watchlist_manager1"):
        assert compose_services[name]["depends_on"]["postgres1"] == {"condition": "service_healthy"}, name


def test_the_credentials_are_moved_by_a_tool_run_by_hand_that_holds_no_private_key(compose_services):
    tools = compose_services["vault_tools"]

    assert tools["profiles"] == ["tools"]
    assert tools["entrypoint"] == ["python", "-m", "src.vault"] and tools["command"] == ["migrate", "--dry-run"]
    modes = _mount_modes(tools)
    # .env is read as lines, never run, and never written.
    assert modes["/app/wata.env"] == "ro" and modes["/app/keys/public"] == "ro"
    assert "/app/keys/own" not in modes
    assert "ports" not in tools and "labels" not in tools
    # Run with every service stopped, at a deploy: it starts the database itself.
    assert tools["depends_on"]["postgres1"] == {"condition": "service_healthy"}


def test_the_install_script_gives_the_operator_the_command_that_moves_the_credentials():
    script = open("deploy/install_package.sh").read()

    assert "cat << 'EOF' > /usr/local/bin/watavault" in script
    assert 'exec docker compose --env-file "$APP_BASE/.env" --profile tools run --rm vault_tools "$@"' in script
    # Saxo's login is written back by a container that can open it.
    assert "run --rm trader1 python -m src.vault export-saxo" in script
    assert "chmod +x /usr/local/bin/watavault" in script


def test_the_install_script_makes_the_keys_once_for_root_alone():
    script = open("deploy/install_package.sh").read()

    keygen = 'docker run --rm -v "$APP_BASE_DIR/keys:/keys" wata-base:latest python -m src.vault keygen --root /keys'
    assert keygen in script
    # After the image it runs in is built, and after the playbook loosened /app/wata.
    assert script.index("docker_build.sh\"\nelse") < script.index(keygen)
    assert 'find "$APP_BASE_DIR/keys" -name private.key -exec chmod 600 {} +' in script
    assert 'chmod 700 "$APP_BASE_DIR/keys" "$APP_BASE_DIR/keys"/*' in script


def test_the_saxo_redirect_is_the_servers_given_to_who_logs_in_and_to_the_admin(compose_services):
    told = sorted(
        name for name, service in compose_services.items()
        if "SAXO_REDIRECT_URL=${SAXO_REDIRECT_URL:-}" in (service.get("environment") or [])
    )

    assert told == ["admin1", "position_monitor1", "price_recorder1", "trader1", "watchlist_manager1"]
    assert "# SAXO_REDIRECT_URL=" in open("deploy/.env.example", encoding="utf-8").read()


def test_the_admin_is_told_the_api_domain_for_the_webhooks_address(compose_services):
    assert "WATA_API_DOMAIN=${API_DOMAIN:-}" in compose_services["admin1"]["environment"]


# ── The control service ──


def test_only_traefik_and_the_control_service_hold_dockers_socket(compose_services):
    holders = sorted(
        name for name, service in compose_services.items()
        if any("docker.sock" in volume for volume in service.get("volumes") or [])
    )

    # Read-only or not, the socket answers anything: the control service's code is what limits it.
    assert holders == ["service_control1", "traefik"]


def test_the_control_service_is_reached_by_the_admin_alone(compose, compose_services):
    control = compose_services["service_control1"]
    on_control_net = sorted(name for name, service in compose_services.items() if "control-net" in (service.get("networks") or []))

    assert on_control_net == ["admin1", "service_control1"]
    # No route out of that network, and nothing published or routed by Traefik.
    assert compose["networks"]["control-net"] == {"internal": True}
    assert control["networks"] == ["control-net"]
    assert "ports" not in control and "expose" not in control and "labels" not in control
    # Started with the admin, which is what it serves.
    assert control["profiles"] == ["admin"]
    assert control["environment"] == ["WATA_APP_ROLE=service_control"]
    assert control["init"] is True


def test_the_control_service_writes_only_its_log(compose_services):
    assert _mount_modes(compose_services["service_control1"]) == {
        "/var/run/docker.sock": "rw",
        "/app/var/log/wata-service-control": "rw",
    }


def test_the_control_services_token_is_handed_to_it_and_the_admin_alone(compose, compose_services):
    holders = sorted(name for name, service in compose_services.items() if "service_control_token" in (service.get("secrets") or []))

    assert holders == ["admin1", "service_control1"]
    assert compose["secrets"]["service_control_token"] == {"file": "../../var/lib/service_control/token"}
    # Its folder is no container's: the token reaches the two only as a secret, read-only.
    for name, service in compose_services.items():
        assert not any("var/lib/service_control" in volume for volume in service.get("volumes") or []), name


def test_traefik_reaches_the_admin_on_trade_net_alone(compose_services):
    admin = compose_services["admin1"]

    assert admin["networks"] == ["trade-net", "control-net"]
    # On two networks, Traefik would otherwise route to whichever address it finds first.
    assert "traefik.docker.network=${COMPOSE_PROJECT_NAME:-deploy}_trade-net" in admin["labels"]


def test_the_install_script_makes_the_control_services_token_once_for_root_alone():
    script = open("deploy/install_package.sh").read()

    assert 'mkdir -p "$APP_BASE_DIR/var/lib/service_control"' in script
    assert 'mkdir -p "$APP_BASE_DIR/var/log/wata-service-control"' in script
    assert 'if [ ! -s "$CONTROL_TOKEN" ]; then' in script
    assert '(umask 077 && openssl rand -hex 32 > "$CONTROL_TOKEN")' in script
    assert 'chmod 600 "$CONTROL_TOKEN"' in script


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
    backup = next(step["run"] for step in steps if step.get("name") == "Build the backup image with the server's own command")
    on_the_server = [line.strip() for line in open("docker_build.sh") if line.startswith("docker build")]

    assert on_the_server == [build, backup]
    assert "--build-arg" not in build
    check = next(step["run"] for step in steps if "python --version" in step.get("run", ""))
    assert "ARG PYTHON_VERSION=" in check and 'grep -F "Python $expected."' in check


def test_the_documents_name_the_python_of_the_image(dockerfile):
    project = open("AGENTS.md", encoding="utf-8").read().split("## Service map")[0]

    assert f"Python {_image_python(dockerfile)}," in project


# ── What keeps the trading path alive ──


def test_out_of_memory_the_kernel_kills_research_before_the_trading_path(compose_services):
    trading = ("postgres1", "rabbitmq1", "web_server1", "telegram1", "scheduler1", "trader1", "position_monitor1")

    for name in trading:
        assert compose_services[name]["oom_score_adj"] == -500, name
    assert compose_services["admin1"]["oom_score_adj"] == 500
    assert compose_services["pg_backup1"]["oom_score_adj"] == 300
    # The TA engine may run live rules and the recorders keep what cannot be fetched again: left as they are.
    for name in ("ta_engine1", "price_recorder1", "capital_feed1", "watchlist_manager1"):
        assert "oom_score_adj" not in compose_services[name], name


def _backup_image() -> list[str]:
    with open("deploy/pg_backup.Dockerfile") as image_file:
        return [line.strip() for line in image_file if line.strip() and not line.strip().startswith("#")]


def test_the_database_is_dumped_by_a_container_that_reaches_it_alone(compose_services):
    backup = compose_services["pg_backup1"]

    # The database's own image, with age and rclone: pg_dump of the server's version.
    assert backup["image"] == "wata-backup:latest"
    assert _backup_image()[0] == f"FROM {compose_services['postgres1']['image']}"
    assert backup["entrypoint"] == ["sh", "/pg_backup.sh"]
    assert _mount_modes(backup) == {"/pg_backup.sh": "ro", "/backups": "rw"}
    assert "PGPASSWORD=${POSTGRES_PASSWORD:-wata_secret_change_me}" in backup["environment"]
    assert "ports" not in backup and "expose" not in backup and "labels" not in backup
    # The admin reads the dumps' report, never writes there.
    assert _mount_modes(compose_services["admin1"])["/app/var/lib/backups"] == "ro"


def test_the_package_carries_the_backup_script_and_the_server_makes_its_folder():
    assert "cp ./deploy/pg_backup.sh _tmp_package/wata/$VERSION/deploy/" in open("package.sh").read()
    assert "cp ./deploy/pg_backup.Dockerfile _tmp_package/wata/$VERSION/deploy/" in open("package.sh").read()
    assert 'mkdir -p "$APP_BASE_DIR/var/lib/backups"' in open("deploy/install_package.sh").read()


def test_the_backup_image_takes_age_and_rclone_at_one_version_checked_by_its_sha256():
    image = _backup_image()
    arguments = dict(line[len("ARG "):].split("=", 1) for line in image if line.startswith("ARG "))

    assert set(arguments) == {"PINNED_AGE", "PINNED_AGE_SHA256", "PINNED_RCLONE", "PINNED_RCLONE_SHA256"}
    assert re.fullmatch(r"v\d+\.\d+\.\d+", arguments["PINNED_AGE"]) and re.fullmatch(r"v\d+\.\d+\.\d+", arguments["PINNED_RCLONE"])
    assert re.fullmatch(r"[0-9a-f]{64}", arguments["PINNED_AGE_SHA256"])
    assert re.fullmatch(r"[0-9a-f]{64}", arguments["PINNED_RCLONE_SHA256"])
    commands = " ".join(image)
    assert 'echo "${PINNED_AGE_SHA256}  age.tar.gz" | sha256sum -c -' in commands
    assert 'echo "${PINNED_RCLONE_SHA256}  rclone.zip" | sha256sum -c -' in commands
    assert "releases/download/${PINNED_AGE}/age-${PINNED_AGE}-linux-amd64.tar.gz" in commands
    assert "downloads.rclone.org/${PINNED_RCLONE}/rclone-${PINNED_RCLONE}-linux-amd64.zip" in commands
    # Built from the file alone: nothing of the server is sent to the build.
    assert "docker build -t wata-backup --platform=linux/amd64 - < deploy/pg_backup.Dockerfile" in open("docker_build.sh").read()


def test_rclone_is_given_no_variable_it_would_take_for_a_flag(compose_services):
    """rclone takes every RCLONE_<flag> of its environment for that flag: the build's RCLONE_VERSION was --version."""
    arguments = [line.split()[1].split("=", 1)[0] for line in _backup_image() if line.startswith("ARG ")]
    names = [variable.split("=", 1)[0] for variable in compose_services["pg_backup1"]["environment"]]

    assert [name for name in arguments if name.startswith("RCLONE_")] == []
    assert [name for name in names if name.startswith("RCLONE_") and not name.startswith("RCLONE_CONFIG_OFFSITE_")] == []


def test_no_key_that_opens_the_copies_off_the_server_is_ever_on_it(compose_services):
    backup = compose_services["pg_backup1"]
    script = open("deploy/pg_backup.sh").read()
    names = [variable.split("=", 1)[0] for variable in backup["environment"]]

    assert _mount_modes(backup) == {"/pg_backup.sh": "ro", "/backups": "rw"}
    assert [name for name in names if "IDENTITY" in name or "PRIVATE" in name] == []
    assert "BACKUP_AGE_RECIPIENT" in names
    # The script encrypts, never decrypts, and sends only what it encrypted.
    assert not re.search(r"\bage\b[^\n]*(\s-d\b|--decrypt|\s-i\b|--identity)", script)
    assert 'copyto "$SEALED/$name.age"' in script and script.count(" copyto ") == 1
    assert "nothing is sent unencrypted" in script


def test_the_env_example_names_every_variable_of_the_backups():
    compose = open("deploy/docker-compose.yml").read()
    example = open("deploy/.env.example").read()
    variables = sorted(set(re.findall(r"\$\{(BACKUP_[A-Z0-9_]+):-", compose)))

    assert len(variables) >= 10
    for variable in variables:
        assert f"# {variable}=" in example, variable


def test_ci_sends_a_dump_off_the_server_and_opens_it_with_the_key_kept_elsewhere(ci_jobs):
    step = next(step for step in ci_jobs["image-as-deployed"]["steps"] if "/pg_backup.sh now" in step.get("run", ""))

    assert "RCLONE_CONFIG_OFFSITE_TYPE=local" in step["run"] and "age-keygen" in step["run"]
    assert "age -d -i /key/identity.txt" in step["run"] and "pg_restore --list" in step["run"]
    # Read without the key, the step fails (a "!" would not fail it under bash -e).
    assert 'pg_restore --list "/bucket/$sealed"; then' in step["run"] and "exit 1" in step["run"]


def test_the_mac_does_not_pull_a_dump_being_encrypted():
    assert "--exclude '.offsite/'" in open("lab/sync.sh").read()


def test_a_deploy_is_refused_while_a_position_or_the_market_is_open():
    with open("deploy/tools/ansible/playbooks/deploy_app.yml") as playbook:
        tasks = yaml.safe_load(playbook)[0]["tasks"]
    names = [task.get("name", "") for task in tasks]

    refusal = names.index("Refuse to deploy while a position is open or the market is open")
    assert refusal < names.index("Transfer the application package") < names.index("Stop the application")
    when = tasks[refusal]["when"]
    assert when[0] == "not (force | default(false) | bool)"
    assert '"$@"' in open("deploy/tools/deploy_app_to_your_server.sh").read()


@pytest.mark.parametrize("weekday_hour, said", [((2026, 10, 26, 10), "open"), ((2026, 10, 24, 10), "closed"),
                                                 ((2026, 10, 26, 23), "closed")])
def test_the_deploys_market_check_reads_the_hours_of_the_configuration(tmp_path, weekday_hour, said):
    import subprocess
    import sys
    from datetime import datetime
    from zoneinfo import ZoneInfo

    with open("deploy/tools/ansible/playbooks/deploy_app.yml") as playbook:
        tasks = yaml.safe_load(playbook)[0]["tasks"]
    check = next(task for task in tasks if task.get("name") == "Say whether the market is open by WATA's hours")
    script = check["ansible.builtin.command"]["argv"][2].replace("/app/wata/etc/config.json", "etc/config_example.json")
    frozen = datetime(*weekday_hour, tzinfo=ZoneInfo("Europe/Paris"))
    script = script.replace("now = datetime.now(ZoneInfo(zone))", f"now = datetime.fromisoformat({frozen.isoformat()!r}).astimezone(ZoneInfo(zone))")

    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)

    assert done.stdout.strip() == said
