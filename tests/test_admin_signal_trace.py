import sys
import types

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = object
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = None
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.admin.live_breakdowns import TRADINGVIEW_OR_HAND
from src.admin.signal_trace import (
    RUN_NOT_KEPT,
    SENT_AGAIN_START,
    attempts_of,
    fate_of,
    ref_for,
    ref_of,
    trace_answer,
)
from src.ta_engine.engine import SENT_AGAIN

UUID = "0b8f6a52-6c1e-4d55-9a8e-3f1c2d4e5a6b"


@pytest.mark.parametrize("ref,found", [
    (UUID, ("uuid", UUID)),
    ("engine-9", ("engine", 9)),
    ("engine-123456789012345678", ("engine", 123456789012345678)),
])
def test_a_signal_is_addressed_by_its_uuid_or_by_the_engine_s_row_of_it(ref, found):
    assert ref_of(ref) == found


@pytest.mark.parametrize("ref", [
    "", "nope", "engine-", "engine-0", "engine-09", "engine-1234567890123456789", "engine-9; DROP TABLE x",
    UUID.upper(), UUID + "0", "../" + UUID, None,
])
def test_no_other_ref_is_ever_taken(ref):
    assert ref_of(ref) is None


def test_a_signal_s_address_is_its_uuid_once_the_web_server_gave_one():
    assert ref_for(UUID, 9) == UUID
    assert ref_for(None, 9) == "engine-9"
    assert ref_for("not-a-uuid", 9) == "engine-9"
    assert ref_for(None, None) is None


def test_the_attempts_are_read_from_the_engine_s_own_text():
    assert SENT_AGAIN.startswith(SENT_AGAIN_START)
    assert attempts_of(SENT_AGAIN.format(attempts=3)) == 3
    assert attempts_of(None) == attempts_of("") == attempts_of("Refused: too old") == 1


@pytest.mark.parametrize("engine,journal,fate", [
    ({"mode": "live", "outcome": "sent"}, {"outcome": "executed", "reason": None}, ("executed", None)),
    ({"mode": "live", "outcome": "sent"}, {"outcome": "refused", "reason": "Paused"}, ("refused", "Paused")),
    ({"mode": "shadow", "outcome": "shadow"}, None, ("shadow", None)),
    ({"mode": "live", "outcome": "refused", "detail": "Too old"}, None, ("not_sent", "Too old")),
    ({"mode": "live", "outcome": "sent"}, None,
     ("not_journaled", "Sent, but the trader journaled nothing of it (a newer signal replaced it first)")),
    (None, {"outcome": "ignored", "reason": "Closed day"}, ("ignored", "Closed day")),
])
def test_what_became_of_a_signal_is_the_trader_s_once_it_journaled_it(engine, journal, fate):
    assert tuple(fate_of(engine, journal).values()) == fate


def test_a_signal_nobody_recorded_has_no_answer():
    assert trace_answer({"engine": None, "journal": None}) is None


def test_a_signal_of_tradingview_or_sent_by_hand_says_so_and_misses_no_run():
    journal = {"signal_uuid": UUID, "action": "short", "indice": "us100", "outcome": "executed"}

    answer = trace_answer({"engine": None, "run": None, "journal": journal, "orders": [], "requests": []})

    assert (answer["ref"], answer["action"], answer["indice"]) == (UUID, "short", "us100")
    assert answer["source"]["kind"] == "received" and answer["source"]["label"] == TRADINGVIEW_OR_HAND
    assert (answer["engine"], answer["run"], answer["run_missing"]) == (None, None, None)
    assert (answer["orders"], answer["requests"], answer["sold_for_it"]) == ([], [], [])


def test_an_engine_signal_without_its_run_says_why():
    engine = {"id": 9, "rule_id": "0123456789ab", "rule_name": "Kernel", "mode": "live", "outcome": "failed",
              "signal_uuid": None, "action": "long", "indice": "us100", "detail": "No answer"}

    answer = trace_answer({"engine": engine, "run": None, "journal": None})

    assert answer["ref"] == "engine-9" and answer["signal_uuid"] is None
    assert answer["source"]["label"] == "Rule: Kernel" and answer["engine"]["attempts"] == 1
    assert answer["run_missing"] == RUN_NOT_KEPT
    assert answer["fate"] == {"key": "not_sent", "reason": "No answer"}
