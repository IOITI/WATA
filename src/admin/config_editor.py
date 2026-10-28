"""
Reading and editing config.json from the admin interface.

Only the trading parameters, and the admin's own limits, can be changed here.
Credentials, infrastructure settings and the admin's switches and addresses
are shown masked or read-only and are always taken from the file on disk, when
saving and when restoring a previous version alike, so nothing sent by a
browser can replace them. That includes the few infrastructure settings that
sit inside the trading sections (PROTECTED_PATHS).

The other services read the config when they start: a saved change takes
effect the next time each of them starts. Each change is written down, key by
key, in a journal beside the file (``<config>.changes.jsonl``), from which the
admin tells what each service still runs with.
"""

import copy
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
from datetime import datetime, timedelta, timezone

from src.admin.config_catalog import CATALOG, EDITABLE, changes, impact
from src.admin.config_catalog.checks import warnings
from src.configuration import ConfigurationManager
from src.capital_feed.settings import CapitalSettings
from src.price_recorder.settings import RecorderSettings
from src.ta_engine.settings import TaEngineSettings

logger = logging.getLogger(__name__)

MASK = "********"
MAX_BACKUPS = 20
# The journal of changes keeps this many saves, the oldest dropped past it.
MAX_JOURNAL_ENTRIES = 1000

SECRET_PATHS = (
    "authentication.saxo.app_config_object.AppKey",
    "authentication.saxo.app_config_object.AppSecret",
    "webserver.app_secret",
    "rabbitmq.authentication.password",
    "postgresql.dsn",
    "telegram.bot_token",
)

# Keys that hold a secret whatever section they are in, so that one added to
# the config later is masked without having to be listed above.
_SECRET_KEY = re.compile(r"secret|password|passwd|token|dsn|api_?key|appkey", re.IGNORECASE)
# ... unless the key only says where something is.
_LOCATION_KEY = re.compile(r"(_path|_file|_url|endpoint)$", re.IGNORECASE)

EDITABLE_PATHS = (
    "trade.rules",
    "trade.config",
    "reporting",
    "logging.level",
    # The admin's limits. Its switches (actions_enabled, config_edit_enabled), the proxies it believes, its
    # address and its folders stay the server's: the page they restrict must not be able to undo them.
    "admin.session_idle_minutes",
    "admin.session_max_age_minutes",
    "admin.backtests_enabled",
    "admin.backtests_max_queued",
    "admin.backtests_max_period_days",
    "admin.backtests_signal_cache_mb",
    "admin.mcp_enabled",
    "admin.mcp",
    "admin.ai_agent",
)

# Inside the editable sections, but they say where the services connect and
# listen. Whoever can change them can point the trader at another server, which
# would then receive the web server token, feed it signals and choose what it
# buys. They are changed on the server only.
PROTECTED_PATHS = (
    "trade.config.signal_polling.service_url",
    "trade.config.watchlist_manager.service_url",
    "trade.config.watchlist_manager.api_host",
    "trade.config.watchlist_manager.api_port",
)

# Sections a page other than the configuration page may read and save alone:
# the price recorder's and the Capital.com feed's, edited from the Data pages
# (src/admin/routers/data.py).
SECTION_PATHS = (
    "trade.config.price_recorder",
    "trade.config.capital_feed",
)

_DAY_TRADING_FLOAT_FIELDS = (
    "percent_profit_wanted_per_days",
    "dont_enter_trade_if_day_profit_is_more_than",
    "max_day_loss_percent",
)

_MISSING = object()


class ConfigConflict(Exception):
    """The config file changed on disk since it was loaded in the editor."""


class BackupNotFound(Exception):
    """No backup of the config file has that name."""


class ConfigInvalid(Exception):
    """The candidate config does not pass ConfigurationManager's validation."""


def _get_path(data: dict, path: str):
    node = data
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return _MISSING
        node = node[key]
    return node


def _set_path(data: dict, path: str, value) -> None:
    *parents, last = path.split(".")
    node = data
    for key in parents:
        node = node.setdefault(key, {})
    node[last] = value


def _delete_path(data: dict, path: str) -> None:
    *parents, last = path.split(".")
    node = data
    for key in parents:
        node = node.get(key) if isinstance(node, dict) else None
        if node is None:
            return
    if isinstance(node, dict):
        node.pop(last, None)


def _is_secret_key(key) -> bool:
    return isinstance(key, str) and bool(_SECRET_KEY.search(key)) and not _LOCATION_KEY.search(key)


def _mask_by_key(node) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                _mask_by_key(value)
            elif _is_secret_key(key) and isinstance(value, str) and value:
                # A number or a switch under such a name is a setting about the secret, not the secret.
                node[key] = MASK
    elif isinstance(node, list):
        for item in node:
            _mask_by_key(item)


def mask_secrets(config: dict) -> dict:
    masked = copy.deepcopy(config)
    for path in SECRET_PATHS:
        if _get_path(masked, path) is not _MISSING:
            _set_path(masked, path, MASK)
    _mask_by_key(masked)
    return masked


def _unmask(node, reference, location: str = ""):
    """
    ``node`` with every mask replaced by the value it stands for in ``reference``.

    A value shown masked comes back from the browser as the mask. It must be
    written as what it was, never as the mask itself.
    """
    if node == MASK:
        if reference is None or reference is _MISSING or isinstance(reference, (dict, list)):
            raise ConfigInvalid(f"'{location}' was sent masked but has no value on the server to keep")
        return reference
    if isinstance(node, dict):
        reference = reference if isinstance(reference, dict) else {}
        return {
            key: _unmask(value, reference.get(key, _MISSING), f"{location}.{key}" if location else key)
            for key, value in node.items()
        }
    if isinstance(node, list):
        reference = reference if isinstance(reference, list) else []
        return [
            _unmask(item, reference[index] if index < len(reference) else _MISSING, f"{location}[{index}]")
            for index, item in enumerate(node)
        ]
    return node


def _is_whole_number(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def align_number_types(submitted, reference):
    """
    Give whole numbers back the float type they have in ``reference``.

    A browser cannot tell 25.0 from 25, so every float with no decimals comes
    back as an integer. The validator insists on floats for some fields, and the
    file should not change where the operator changed nothing.
    """
    if _is_whole_number(submitted) and isinstance(reference, float):
        return float(submitted)
    if isinstance(submitted, dict) and isinstance(reference, dict):
        return {key: align_number_types(value, reference.get(key)) for key, value in submitted.items()}
    if isinstance(submitted, list) and isinstance(reference, list):
        # Rules are matched by type rather than position, so reordering them is harmless.
        rules_by_type = {
            item["rule_type"]: item for item in reference if isinstance(item, dict) and "rule_type" in item
        }
        aligned = []
        for index, item in enumerate(submitted):
            if isinstance(item, dict) and item.get("rule_type") in rules_by_type:
                item_reference = rules_by_type[item["rule_type"]]
            else:
                item_reference = reference[index] if index < len(reference) else None
            aligned.append(align_number_types(item, item_reference))
        return aligned
    return submitted


def _coerce_day_trading_floats(config: dict) -> None:
    """The validator requires these to be floats even in a rule that has no counterpart on disk."""
    rules = _get_path(config, "trade.rules")
    if not isinstance(rules, list):
        return
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("rule_type") != "day_trading":
            continue
        rule_config = rule.get("rule_config")
        if not isinstance(rule_config, dict):
            continue
        for field in _DAY_TRADING_FLOAT_FIELDS:
            if _is_whole_number(rule_config.get(field)):
                rule_config[field] = float(rule_config[field])


def check_admin_limits(config: dict) -> None:
    """
    The admin's limits the page may change, held to the catalog's bounds: the
    admin reads them at its start without checking all of them, and a wrong
    one would stop it there.
    """
    for param in CATALOG.params:
        # admin.mcp and admin.ai_agent are checked whole, by McpLimits and AgentSettings (AdminSettings.from_config).
        if (not param.path.startswith("admin.") or param.path.startswith(("admin.mcp.", "admin.ai_agent."))
                or param.access != EDITABLE):
            continue
        value = _get_path(config, param.path)
        if value is _MISSING:
            continue
        if param.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"Config field '{param.path}' must be true or false")
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not param.minimum <= value <= param.maximum:
            raise ValueError(f"Config field '{param.path}' must be a whole number from {param.minimum:g} to {param.maximum:g}")


def merge_editable(current: dict, submitted: dict, *, keep_protected: bool = False) -> dict:
    """
    ``current`` with only its editable parts replaced by those of ``submitted``.

    The protected settings inside those parts always end up as they are in
    ``current``: a change to one is refused, or quietly left out with
    ``keep_protected`` (restoring an older version, whose values for them may
    simply be older).
    """
    merged = copy.deepcopy(current)
    for path in EDITABLE_PATHS:
        value = _get_path(submitted, path)
        if value is not _MISSING:
            reference = _get_path(current, path)
            value = _unmask(copy.deepcopy(value), reference, path)
            _set_path(merged, path, align_number_types(value, reference))

    for path in PROTECTED_PATHS:
        on_disk = _get_path(current, path)
        if _get_path(merged, path) == on_disk:
            continue
        if not keep_protected:
            raise ConfigInvalid(f"'{path}' can only be changed on the server, not from this interface")
        if on_disk is _MISSING:
            _delete_path(merged, path)
        else:
            _set_path(merged, path, copy.deepcopy(on_disk))

    _coerce_day_trading_floats(merged)
    return merged


class ConfigEditor:
    def __init__(self, config_path: str):
        self.config_path = config_path
        # What each save changed, key by key: beside the backups, in the folder the editor already writes.
        self.journal_path = f"{config_path}.changes.jsonl"

    def _read(self) -> tuple[dict, str]:
        with open(self.config_path, "rb") as config_file:
            content = config_file.read()
        return json.loads(content), hashlib.sha256(content).hexdigest()

    def modified_at(self) -> float:
        return os.path.getmtime(self.config_path)

    def load(self) -> dict:
        config, revision = self._read()
        return {
            "config": mask_secrets(config),
            "revision": revision,
            "editable_paths": list(EDITABLE_PATHS),
            "protected_paths": list(PROTECTED_PATHS),
        }

    def _write_candidate(self, candidate: dict) -> str:
        """Write ``candidate`` next to the config (same filesystem, so it can replace it atomically)."""
        directory = os.path.dirname(os.path.abspath(self.config_path))
        candidate_path = os.path.join(directory, f".config.candidate-{secrets.token_hex(8)}.json")
        file_descriptor = os.open(candidate_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(file_descriptor, "w") as candidate_file:
            json.dump(candidate, candidate_file, indent=2)
            candidate_file.write("\n")
            candidate_file.flush()
            os.fsync(candidate_file.fileno())
        return candidate_path

    def _validated_candidate(self, submitted: dict, *, keep_protected: bool = False) -> tuple[str, str, dict]:
        """
        Merge the editable parts of ``submitted`` into the config on disk and
        validate the result with the validator the services use. Returns
        (candidate path, revision merged from, the config merged from).
        """
        if not isinstance(submitted, dict):
            raise ConfigInvalid("The configuration must be a JSON object")
        current, revision = self._read()
        candidate_path = self._write_candidate(merge_editable(current, submitted, keep_protected=keep_protected))
        try:
            # The price recorder, the Capital.com feed and the TA engine check
            # their own sections, which the shared validator leaves alone, and
            # the admin its own: said here, rather than found idle later.
            from src.admin.context import AdminSettings

            candidate = ConfigurationManager(candidate_path)
            RecorderSettings.from_config(candidate)
            CapitalSettings.from_config(candidate)
            TaEngineSettings.from_config(candidate)
            AdminSettings.from_config(candidate)
            check_admin_limits(candidate.config_data)
        except Exception as e:
            os.remove(candidate_path)
            # The validator raises ValueError for the cases it checks, and
            # whatever the malformed structure triggers for the ones it doesn't.
            message = str(e) if isinstance(e, ValueError) else f"Invalid configuration structure: {e!r}"
            raise ConfigInvalid(message) from e
        return candidate_path, revision, current

    def _install_candidate(self, candidate_path: str, revision: str, expected_revision: str) -> str:
        """
        Make the validated candidate the config, keeping the file it replaces.
        Returns the path of that backup. The candidate is removed if it is not installed.
        """
        try:
            if revision != expected_revision:
                raise ConfigConflict("The configuration file changed on the server since it was loaded")

            backup_path = self._new_backup_path()
            shutil.copy2(self.config_path, backup_path)
            os.chmod(candidate_path, os.stat(self.config_path).st_mode & 0o777)
            os.replace(candidate_path, self.config_path)
        except Exception:
            if os.path.exists(candidate_path):
                os.remove(candidate_path)
            raise

        self._prune_backups()
        return backup_path

    def validate(self, submitted: dict) -> None:
        candidate_path, _, _ = self._validated_candidate(submitted)
        os.remove(candidate_path)

    def preview(self, submitted: dict, live_indices=()) -> dict:
        """
        What saving ``submitted`` would change, checked as a save is: each
        parameter changed, the services that must start again for it, the pages
        and runs that read it, and what works against what (warnings), the
        indices signal rules are live on (``live_indices``) included.
        """
        candidate_path, _, current = self._validated_candidate(submitted)
        try:
            with open(candidate_path, "rb") as candidate_file:
                candidate = json.loads(candidate_file.read())
        finally:
            os.remove(candidate_path)
        found = changes(mask_secrets(current), mask_secrets(candidate))
        return {"changes": found, "impact": impact(found), "warnings": warnings(candidate, live_indices)}

    def _record(self, action: str, before: dict, after: dict, by: str | None, revisions: tuple[str, str]) -> list[dict]:
        """
        Write down what a save changed, key by key, values masked, and return
        it. Never raises: the save is done, and its backup kept, whatever
        becomes of its note.
        """
        found = changes(mask_secrets(before), mask_secrets(after))
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "by": by,
            "revision_before": revisions[0],
            "revision_after": revisions[1],
            "changes": found,
        }
        try:
            lines = []
            if os.path.exists(self.journal_path):
                with open(self.journal_path, encoding="utf-8") as journal:
                    lines = journal.read().splitlines()
            lines = (lines + [json.dumps(entry)])[-MAX_JOURNAL_ENTRIES:]
            temporary = f"{self.journal_path}.tmp"
            with open(temporary, "w", encoding="utf-8") as journal:
                journal.write("\n".join(lines) + "\n")
            os.replace(temporary, self.journal_path)
        except Exception as e:
            logger.warning("Could not write down the configuration's changes: %r", e)
        return found

    def history(self, limit: int = 100) -> list[dict]:
        """The saves written down, newest first."""
        try:
            with open(self.journal_path, encoding="utf-8") as journal:
                lines = journal.read().splitlines()
        except OSError:
            return []
        entries = []
        for line in reversed(lines):
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
            if len(entries) >= limit:
                break
        return entries

    def _directory(self) -> str:
        return os.path.dirname(os.path.abspath(self.config_path))

    def _backup_names(self) -> list[str]:
        """Names of the backups kept beside the config, oldest first (the timestamp sorts)."""
        pattern = re.compile(rf"^{re.escape(os.path.basename(self.config_path))}\.bak-\d{{8}}-\d{{6}}$")
        return sorted(name for name in os.listdir(self._directory()) if pattern.match(name))

    def _backup_path(self, name: str) -> str:
        # Only a name from the listing is ever joined to the directory.
        if name not in self._backup_names():
            raise BackupNotFound(name)
        return os.path.join(self._directory(), name)

    def _new_backup_path(self) -> str:
        """A backup name that is not taken, so two saves in the same second keep both versions."""
        moment = datetime.now()
        while True:
            path = f"{self.config_path}.bak-{moment.strftime('%Y%m%d-%H%M%S')}"
            if not os.path.exists(path):
                return path
            moment += timedelta(seconds=1)

    def _prune_backups(self) -> None:
        for name in self._backup_names()[:-MAX_BACKUPS]:
            os.remove(os.path.join(self._directory(), name))

    def list_backups(self) -> list[dict]:
        """Backups, newest first."""
        backups = []
        for name in reversed(self._backup_names()):
            stat = os.stat(os.path.join(self._directory(), name))
            backups.append({
                "name": name,
                # When the backup was made: its content is the config as it was just before that.
                "created_at": datetime.strptime(name[-15:], "%Y%m%d-%H%M%S").astimezone(timezone.utc).isoformat(),
                "size": stat.st_size,
            })
        return backups

    def load_backup(self, name: str) -> dict:
        with open(self._backup_path(name), "rb") as backup_file:
            return {"name": name, "config": mask_secrets(json.loads(backup_file.read()))}

    def restore_backup(self, name: str, expected_revision: str, by: str | None = None) -> dict:
        """
        Put the trading parameters of a backup back. Like a save, it only
        touches the editable sections: credentials, infrastructure and the
        admin's own settings stay as they are in the file now, whatever the
        backup holds for them.
        """
        with open(self._backup_path(name), "rb") as backup_file:
            try:
                backup = json.loads(backup_file.read())
            except ValueError as e:
                raise ConfigInvalid(f"This backup is not readable: {e}") from e
        try:
            candidate_path, revision, current = self._validated_candidate(backup, keep_protected=True)
        except ConfigInvalid as e:
            raise ConfigInvalid(f"This backup is no longer a valid configuration: {e}") from e

        saved_backup = self._install_candidate(candidate_path, revision, expected_revision)
        logger.info(f"Trading parameters restored from {name}, previous version kept as {saved_backup}")
        restored, new_revision = self._read()
        found = self._record(f"restored {name}", current, restored, by, (revision, new_revision))
        return {
            "revision": new_revision, "backup": os.path.basename(saved_backup), "restored": name,
            "changes": found, "impact": impact(found),
        }

    def save(self, submitted: dict, expected_revision: str, by: str | None = None, action: str = "saved") -> dict:
        candidate_path, revision, current = self._validated_candidate(submitted)
        backup_path = self._install_candidate(candidate_path, revision, expected_revision)
        logger.info(f"Configuration saved, previous version kept as {backup_path}")
        saved, new_revision = self._read()
        found = self._record(action, current, saved, by, (revision, new_revision))
        return {"revision": new_revision, "backup": os.path.basename(backup_path), "changes": found, "impact": impact(found)}

    def load_section(self, path: str) -> dict:
        """One of SECTION_PATHS as it is on disk (None when it is not there), and the revision it was read at."""
        if path not in SECTION_PATHS:
            raise ConfigInvalid(f"'{path}' cannot be read alone")
        config, revision = self._read()
        value = _get_path(config, path)
        return {"section": None if value is _MISSING else mask_secrets(value), "revision": revision}

    def save_section(self, path: str, value, expected_revision: str, by: str | None = None) -> dict:
        """
        Replace one of SECTION_PATHS and save the whole as ``save`` does: merged
        into the file on disk, checked with the services' own validator and the
        recorder's, refused when the file changed since ``expected_revision``,
        the version it replaces kept.
        """
        if path not in SECTION_PATHS:
            raise ConfigInvalid(f"'{path}' cannot be saved alone")
        current, _ = self._read()
        submitted = copy.deepcopy(current)
        _set_path(submitted, path, value)
        return self.save(submitted, expected_revision, by=by, action=f"saved {path}")
