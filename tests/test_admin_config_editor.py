import json
import os

import pytest

from src.admin.config_editor import (
    MASK,
    MAX_BACKUPS,
    PROTECTED_PATHS,
    ConfigConflict,
    ConfigEditor,
    ConfigInvalid,
    align_number_types,
    mask_secrets,
    merge_editable,
)


@pytest.fixture
def config_path(tmp_path):
    with open("tests/test_config.json") as source:
        config = json.load(source)
    config["postgresql"] = {"dsn": "postgresql://wata:db_password@postgres1:5432/wata"}
    config["telegram"] = {"bot_token": "telegram_token", "chat_id": "1", "bot_name": "bot"}
    config["trade"]["rules"].append({
        "rule_type": "day_trading",
        "rule_name": "day_trading",
        "rule_config": {
            "percent_profit_wanted_per_days": 1.7,
            "max_day_loss_percent": -25.0,
            "dont_enter_trade_if_day_profit_is_more_than": 1.25,
            "close_position_time": "21:55",
        },
    })
    config["trade"]["config"]["trading_mode"] = "day_trading"

    path = tmp_path / "etc" / "config.json"
    path.parent.mkdir()
    path.write_text(json.dumps(config, indent=2))
    return str(path)


@pytest.fixture
def editor(config_path):
    return ConfigEditor(config_path)


def _on_disk(config_path):
    with open(config_path) as config_file:
        return json.load(config_file)


def _directory_listing(config_path):
    return sorted(os.listdir(os.path.dirname(config_path)))


def test_secrets_are_masked_when_loading(editor):
    loaded = editor.load()["config"]

    assert loaded["authentication"]["saxo"]["app_config_object"]["AppKey"] == MASK
    assert loaded["authentication"]["saxo"]["app_config_object"]["AppSecret"] == MASK
    assert loaded["webserver"]["app_secret"] == MASK
    assert loaded["rabbitmq"]["authentication"]["password"] == MASK
    assert loaded["postgresql"]["dsn"] == MASK
    assert loaded["telegram"]["bot_token"] == MASK
    for secret in ("test_secret", "db_password", "telegram_token"):
        assert secret not in json.dumps(loaded)


def test_masking_leaves_the_original_untouched_and_skips_absent_secrets():
    config = {"webserver": {"app_secret": "s3cret"}}

    masked = mask_secrets(config)

    assert masked == {"webserver": {"app_secret": MASK}}
    assert config == {"webserver": {"app_secret": "s3cret"}}


def test_saving_what_was_loaded_never_writes_a_mask(editor, config_path):
    before = _on_disk(config_path)
    loaded = editor.load()

    editor.save(loaded["config"], loaded["revision"])

    assert _on_disk(config_path) == before
    assert MASK not in open(config_path).read()


def test_only_editable_sections_are_taken_from_the_submitted_config(editor, config_path):
    loaded = editor.load()
    submitted = loaded["config"]
    submitted["webserver"]["app_secret"] = "attacker_value"
    submitted["rabbitmq"]["hostname"] = "evil.example"
    submitted["admin"] = {"actions_enabled": True}
    submitted["trade"]["config"]["position_management"]["performance_thresholds"]["stoploss_percent"] = -12
    submitted["logging"]["level"] = "DEBUG"

    editor.save(submitted, loaded["revision"])

    saved = _on_disk(config_path)
    assert saved["webserver"]["app_secret"] == "test_secret"
    assert saved["rabbitmq"]["hostname"] == "localhost"
    assert "admin" not in saved
    assert saved["trade"]["config"]["position_management"]["performance_thresholds"]["stoploss_percent"] == -12
    assert saved["logging"]["level"] == "DEBUG"


def test_invalid_config_is_refused_and_leaves_no_trace(editor, config_path):
    before = open(config_path).read()
    listing = _directory_listing(config_path)
    loaded = editor.load()
    thresholds = loaded["config"]["trade"]["config"]["position_management"]["performance_thresholds"]
    thresholds["stoploss_percent"] = 80

    with pytest.raises(ConfigInvalid, match="Stoploss percentage"):
        editor.validate(loaded["config"])
    with pytest.raises(ConfigInvalid, match="Stoploss percentage"):
        editor.save(loaded["config"], loaded["revision"])

    assert open(config_path).read() == before
    assert _directory_listing(config_path) == listing


def test_malformed_structure_is_reported_as_invalid(editor):
    loaded = editor.load()
    loaded["config"]["trade"]["rules"] = ["not a rule"]

    with pytest.raises(ConfigInvalid):
        editor.validate(loaded["config"])


def test_validate_does_not_change_anything(editor, config_path):
    before = open(config_path).read()
    listing = _directory_listing(config_path)

    editor.validate(editor.load()["config"])

    assert open(config_path).read() == before
    assert _directory_listing(config_path) == listing


def test_save_keeps_a_backup_of_the_previous_version(editor, config_path):
    before = open(config_path).read()
    loaded = editor.load()
    loaded["config"]["logging"]["level"] = "WARNING"

    saved = editor.save(loaded["config"], loaded["revision"])

    backup_path = os.path.join(os.path.dirname(config_path), saved["backup"])
    assert open(backup_path).read() == before
    assert _on_disk(config_path)["logging"]["level"] == "WARNING"
    assert saved["revision"] == editor.load()["revision"]
    assert saved["revision"] != loaded["revision"]


def test_save_is_refused_when_the_file_changed_since_it_was_loaded(editor, config_path):
    loaded = editor.load()
    changed_elsewhere = _on_disk(config_path)
    changed_elsewhere["logging"]["level"] = "ERROR"
    with open(config_path, "w") as config_file:
        json.dump(changed_elsewhere, config_file)
    listing = _directory_listing(config_path)

    loaded["config"]["logging"]["level"] = "DEBUG"
    with pytest.raises(ConfigConflict):
        editor.save(loaded["config"], loaded["revision"])

    assert _on_disk(config_path)["logging"]["level"] == "ERROR"
    assert _directory_listing(config_path) == listing


def test_saved_file_keeps_its_permissions(editor, config_path):
    os.chmod(config_path, 0o640)
    loaded = editor.load()

    editor.save(loaded["config"], loaded["revision"])

    assert os.stat(config_path).st_mode & 0o777 == 0o640


def test_old_backups_are_pruned(editor, config_path):
    directory = os.path.dirname(config_path)
    for index in range(MAX_BACKUPS + 5):
        open(os.path.join(directory, f"config.json.bak-20200101-{index:06d}"), "w").close()
    loaded = editor.load()

    saved = editor.save(loaded["config"], loaded["revision"])

    backups = [name for name in os.listdir(directory) if name.startswith("config.json.bak-")]
    assert len(backups) == MAX_BACKUPS
    assert saved["backup"] in backups
    assert "config.json.bak-20200101-000000" not in backups


def test_whole_numbers_keep_the_float_type_the_validator_requires(editor, config_path):
    # What a browser sends back: -25.0 has become -25.
    loaded = editor.load()
    day_trading = next(rule for rule in loaded["config"]["trade"]["rules"] if rule["rule_type"] == "day_trading")
    day_trading["rule_config"]["max_day_loss_percent"] = -25
    day_trading["rule_config"]["percent_profit_wanted_per_days"] = 2

    editor.save(loaded["config"], loaded["revision"])

    saved_rule = next(rule for rule in _on_disk(config_path)["trade"]["rules"] if rule["rule_type"] == "day_trading")
    assert saved_rule["rule_config"]["max_day_loss_percent"] == -25.0
    assert isinstance(saved_rule["rule_config"]["max_day_loss_percent"], float)
    assert isinstance(saved_rule["rule_config"]["percent_profit_wanted_per_days"], float)


def test_new_day_trading_rule_gets_floats_without_a_counterpart_on_disk():
    current = {"trade": {"rules": []}}
    submitted = {"trade": {"rules": [{
        "rule_type": "day_trading",
        "rule_name": "day_trading",
        "rule_config": {"percent_profit_wanted_per_days": 2, "max_day_loss_percent": -25},
    }]}}

    rule_config = merge_editable(current, submitted)["trade"]["rules"][0]["rule_config"]

    assert isinstance(rule_config["percent_profit_wanted_per_days"], float)
    assert isinstance(rule_config["max_day_loss_percent"], float)


def test_number_alignment_matches_rules_by_type_and_leaves_other_values_alone():
    reference = [
        {"rule_type": "a", "rule_config": {"value": 1.0, "count": 3}},
        {"rule_type": "b", "rule_config": {"value": 2.5}},
    ]
    submitted = [
        {"rule_type": "b", "rule_config": {"value": 3}},
        {"rule_type": "a", "rule_config": {"value": 1, "count": 4, "flag": True}},
    ]

    aligned = align_number_types(submitted, reference)

    assert isinstance(aligned[0]["rule_config"]["value"], float)
    assert isinstance(aligned[1]["rule_config"]["value"], float)
    assert isinstance(aligned[1]["rule_config"]["count"], int)
    assert aligned[1]["rule_config"]["flag"] is True


def _write(config_path, config):
    with open(config_path, "w") as config_file:
        json.dump(config, config_file, indent=2)


def _with_service_addresses(config_path):
    config = _on_disk(config_path)
    config["trade"]["config"]["signal_polling"] = {"service_url": "http://web_server1:80", "interval_ms": 500}
    config["trade"]["config"]["watchlist_manager"] = {
        "enabled": False,
        "api_host": "0.0.0.0",
        "api_port": 8081,
        "service_url": "http://watchlist_manager1:8081",
    }
    _write(config_path, config)
    return config


def test_secrets_are_masked_by_the_name_of_their_key_wherever_they_are():
    config = {
        "notifier": {"webhook_secret": "s3cret", "api_key": "k3y", "Password": "pw", "auth_token": "t0ken"},
        "list": [{"client_secret": "nested"}],
        "empty": {"api_key": "", "token": None},
    }

    masked = mask_secrets(config)

    assert masked["notifier"] == {"webhook_secret": MASK, "api_key": MASK, "Password": MASK, "auth_token": MASK}
    assert masked["list"] == [{"client_secret": MASK}]
    # Nothing to hide, and the operator can see that it is not set.
    assert masked["empty"] == {"api_key": "", "token": None}


def test_keys_that_say_where_a_secret_is_kept_stay_readable():
    config = {
        "persistant": {"token_path": "/app/var/lib/saxo_auth/token", "secret_file": "/run/secrets/x"},
        "authentication": {"TokenEndpoint": "https://sim.logonvalidation.net/token"},
        "streaming": {"token_refresh_margin_seconds": 60, "tokens": {"access_token": "a"}},
    }

    masked = mask_secrets(config)

    assert masked["persistant"] == config["persistant"]
    assert masked["authentication"] == config["authentication"]
    assert masked["streaming"] == {"token_refresh_margin_seconds": 60, "tokens": {"access_token": MASK}}


def test_a_masked_value_inside_an_editable_section_is_saved_as_what_it_was(editor, config_path):
    config = _on_disk(config_path)
    config["reporting"] = {**config.get("reporting", {}), "webhook_secret": "keep-me"}
    _write(config_path, config)
    loaded = editor.load()
    assert loaded["config"]["reporting"]["webhook_secret"] == MASK
    loaded["config"]["logging"]["level"] = "DEBUG"

    editor.save(loaded["config"], loaded["revision"])

    assert _on_disk(config_path)["reporting"]["webhook_secret"] == "keep-me"
    assert MASK not in open(config_path).read()


def test_a_mask_is_never_written_for_a_value_the_server_does_not_have(editor, config_path):
    before = open(config_path).read()
    loaded = editor.load()
    loaded["config"]["reporting"] = {**loaded["config"].get("reporting", {}), "webhook_secret": MASK}

    with pytest.raises(ConfigInvalid, match="reporting.webhook_secret"):
        editor.save(loaded["config"], loaded["revision"])

    assert open(config_path).read() == before


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_a_changed_protected_setting_is_refused_and_leaves_no_trace(editor, config_path, path):
    _with_service_addresses(config_path)
    before = open(config_path).read()
    listing = _directory_listing(config_path)
    loaded = editor.load()
    *parents, key = path.split(".")
    node = loaded["config"]
    for parent in parents:
        node = node[parent]
    node[key] = 9999 if key == "api_port" else "http://attacker.example"

    with pytest.raises(ConfigInvalid, match=f"'{path}' can only be changed on the server"):
        editor.validate(loaded["config"])
    with pytest.raises(ConfigInvalid, match=f"'{path}' can only be changed on the server"):
        editor.save(loaded["config"], loaded["revision"])

    assert open(config_path).read() == before
    assert _directory_listing(config_path) == listing


def test_a_protected_setting_can_be_neither_added_nor_removed(editor, config_path):
    # Absent from the file: the services use their built-in address.
    loaded = editor.load()
    loaded["config"]["trade"]["config"]["signal_polling"] = {"service_url": "http://attacker.example"}
    with pytest.raises(ConfigInvalid, match="signal_polling.service_url"):
        editor.save(loaded["config"], loaded["revision"])

    _with_service_addresses(config_path)
    loaded = editor.load()
    del loaded["config"]["trade"]["config"]["watchlist_manager"]["service_url"]
    with pytest.raises(ConfigInvalid, match="watchlist_manager.service_url"):
        editor.save(loaded["config"], loaded["revision"])

    loaded = editor.load()
    del loaded["config"]["trade"]["config"]["signal_polling"]
    with pytest.raises(ConfigInvalid, match="signal_polling.service_url"):
        editor.save(loaded["config"], loaded["revision"])


def test_the_rest_of_a_section_holding_a_protected_setting_stays_editable(editor, config_path):
    _with_service_addresses(config_path)
    loaded = editor.load()
    loaded["config"]["trade"]["config"]["signal_polling"]["interval_ms"] = 750
    loaded["config"]["trade"]["config"]["watchlist_manager"]["enabled"] = True

    editor.save(loaded["config"], loaded["revision"])

    saved = _on_disk(config_path)["trade"]["config"]
    assert saved["signal_polling"] == {"service_url": "http://web_server1:80", "interval_ms": 750}
    assert saved["watchlist_manager"]["enabled"] is True
    assert saved["watchlist_manager"]["service_url"] == "http://watchlist_manager1:8081"


def test_restore_puts_back_the_trading_parameters_and_nothing_else(editor, config_path):
    _with_service_addresses(config_path)
    loaded = editor.load()
    loaded["config"]["logging"]["level"] = "DEBUG"
    loaded["config"]["trade"]["config"]["signal_polling"]["interval_ms"] = 750
    backup = editor.save(loaded["config"], loaded["revision"])["backup"]
    # Changed on the server since that backup was made.
    on_server = _on_disk(config_path)
    on_server["admin"] = {"actions_enabled": False, "config_edit_enabled": False}
    on_server["webserver"]["app_secret"] = "rotated_secret"
    on_server["rabbitmq"]["hostname"] = "rabbitmq2"
    on_server["rabbitmq"]["authentication"]["password"] = "rotated_password"
    on_server["postgresql"]["dsn"] = "postgresql://wata:rotated@postgres1:5432/wata"
    on_server["trade"]["config"]["signal_polling"]["service_url"] = "http://web_server2:80"
    _write(config_path, on_server)

    restored = editor.restore_backup(backup, editor.load()["revision"])

    now = _on_disk(config_path)
    assert now["logging"]["level"] == "INFO"
    assert now["trade"]["config"]["signal_polling"]["interval_ms"] == 500
    for section in ("admin", "webserver", "rabbitmq", "postgresql", "authentication", "telegram"):
        assert now[section] == on_server[section]
    # The address the backup had is older, not a change asked for: the one on the server stays.
    assert now["trade"]["config"]["signal_polling"]["service_url"] == "http://web_server2:80"
    assert restored["restored"] == backup
    kept = os.path.join(os.path.dirname(config_path), restored["backup"])
    assert json.load(open(kept)) == on_server


def test_restore_does_not_bring_back_a_protected_setting_the_server_no_longer_has(editor, config_path):
    _with_service_addresses(config_path)
    loaded = editor.load()
    loaded["config"]["logging"]["level"] = "DEBUG"
    backup = editor.save(loaded["config"], loaded["revision"])["backup"]
    on_server = _on_disk(config_path)
    del on_server["trade"]["config"]["signal_polling"]["service_url"]
    _write(config_path, on_server)

    editor.restore_backup(backup, editor.load()["revision"])

    now = _on_disk(config_path)
    assert now["logging"]["level"] == "INFO"
    assert "service_url" not in now["trade"]["config"]["signal_polling"]


def test_restore_refuses_a_backup_that_is_no_longer_valid_and_leaves_no_trace(editor, config_path):
    loaded = editor.load()
    loaded["config"]["logging"]["level"] = "DEBUG"
    backup = editor.save(loaded["config"], loaded["revision"])["backup"]
    backup_path = os.path.join(os.path.dirname(config_path), backup)
    broken = json.load(open(backup_path))
    broken["trade"]["config"]["position_management"]["performance_thresholds"]["stoploss_percent"] = 80
    _write(backup_path, broken)
    before = open(config_path).read()
    listing = _directory_listing(config_path)

    with pytest.raises(ConfigInvalid, match="no longer a valid configuration"):
        editor.restore_backup(backup, editor.load()["revision"])

    assert open(config_path).read() == before
    assert _directory_listing(config_path) == listing


def test_restore_is_refused_when_the_file_changed_since_it_was_loaded(editor, config_path):
    loaded = editor.load()
    loaded["config"]["logging"]["level"] = "DEBUG"
    backup = editor.save(loaded["config"], loaded["revision"])["backup"]
    before = open(config_path).read()
    listing = _directory_listing(config_path)

    with pytest.raises(ConfigConflict):
        editor.restore_backup(backup, loaded["revision"])

    assert open(config_path).read() == before
    assert _directory_listing(config_path) == listing


def test_a_mistake_in_the_price_recorder_section_is_refused_like_any_other(editor, config_path):
    before = open(config_path).read()
    listing = _directory_listing(config_path)
    loaded = editor.load()
    loaded["config"]["trade"]["config"]["price_recorder"] = {"enabled": True, "max_instruments": "many"}

    with pytest.raises(ConfigInvalid, match=r"trade\.config\.price_recorder\.max_instruments"):
        editor.validate(loaded["config"])
    with pytest.raises(ConfigInvalid, match=r"trade\.config\.price_recorder\.max_instruments"):
        editor.save(loaded["config"], loaded["revision"])

    assert open(config_path).read() == before
    assert _directory_listing(config_path) == listing


def test_a_valid_price_recorder_section_is_saved(editor, config_path):
    loaded = editor.load()
    loaded["config"]["trade"]["config"]["price_recorder"] = {
        "enabled": True,
        "instruments": [{"label": "us100", "uic": 4913, "asset_type": "CfdOnIndex"}],
    }

    editor.save(loaded["config"], loaded["revision"])

    assert _on_disk(config_path)["trade"]["config"]["price_recorder"]["instruments"][0]["uic"] == 4913


# ── one section saved alone, from the Data pages ──

RECORDER = "trade.config.price_recorder"


def test_the_recorder_section_is_read_alone_with_the_revision_it_was_read_at(editor, config_path):
    assert editor.load_section(RECORDER) == {"section": None, "revision": editor.load()["revision"]}

    config = _on_disk(config_path)
    config["trade"]["config"]["price_recorder"] = {"enabled": True, "max_instruments": 80}
    _write(config_path, config)

    assert editor.load_section(RECORDER)["section"] == {"enabled": True, "max_instruments": 80}


def test_the_recorder_section_is_saved_alone_and_nothing_else_moves(editor, config_path):
    before = _on_disk(config_path)
    revision = editor.load_section(RECORDER)["revision"]

    saved = editor.save_section(RECORDER, {"enabled": True, "record_volume": True, "retention_months": 6}, revision)

    after = _on_disk(config_path)
    assert after["trade"]["config"]["price_recorder"] == {"enabled": True, "record_volume": True, "retention_months": 6}
    after["trade"]["config"].pop("price_recorder")
    assert after == before
    # Kept like any save: the version it replaced, and a new revision to save against.
    assert saved["backup"] in _directory_listing(config_path)
    assert saved["revision"] == editor.load()["revision"] != revision


def test_a_section_saved_alone_is_checked_like_the_whole(editor, config_path):
    before = open(config_path).read()
    listing = _directory_listing(config_path)
    revision = editor.load_section(RECORDER)["revision"]

    with pytest.raises(ConfigInvalid, match=r"trade\.config\.price_recorder\.refresh_rate_ms"):
        editor.save_section(RECORDER, {"enabled": True, "refresh_rate_ms": 5}, revision)

    assert open(config_path).read() == before
    assert _directory_listing(config_path) == listing


def test_a_section_is_not_saved_over_a_file_that_changed_since_it_was_read(editor, config_path):
    revision = editor.load_section(RECORDER)["revision"]
    config = _on_disk(config_path)
    config["logging"]["level"] = "DEBUG"
    _write(config_path, config)

    with pytest.raises(ConfigConflict):
        editor.save_section(RECORDER, {"enabled": True}, revision)

    assert "price_recorder" not in _on_disk(config_path)["trade"]["config"]


@pytest.mark.parametrize("path", ["trade.config", "trade.rules", "postgresql", "trade.config.signal_polling"])
def test_only_the_sections_listed_are_read_or_saved_alone(editor, path):
    revision = editor.load()["revision"]

    with pytest.raises(ConfigInvalid, match="alone"):
        editor.load_section(path)
    with pytest.raises(ConfigInvalid, match="alone"):
        editor.save_section(path, {}, revision)
