import pytest

from src.trade.refused_entry import OTHER_SIDE, close_on_refused_entry, not_sold, said


@pytest.mark.parametrize("position_management, on", [
    (None, True),
    ({}, True),
    ({"close_on_refused_entry": True}, True),
    ({"close_on_refused_entry": False}, False),
    # Only a plain false switches it off: the validator refuses anything else, and a service of before reads on.
    ({"close_on_refused_entry": "no"}, True),
    ({"close_on_refused_entry": 0}, True),
])
def test_a_refused_entry_sells_the_other_side_unless_the_configuration_says_false(position_management, on):
    assert close_on_refused_entry(position_management) is on


def test_the_other_side_of_an_entry_is_the_direction_it_would_have_sold():
    assert OTHER_SIDE == {"long": "short", "short": "long"}


def test_the_journal_says_the_refusal_then_what_was_sold():
    assert said("Signal is refused due to risky market hours.", "long", 1) == (
        "Signal is refused due to risky market hours; closed 1 long position(s) the signal turned against"
    )
    assert said("Paused", "short", 0, "Not sold: p1 (why), handed to the position monitor.") == (
        "Paused; closed 0 short position(s) the signal turned against. Not sold: p1 (why), handed to the position monitor."
    )
    assert not_sold("Paused", "short", "RuntimeError: no answer") == (
        "Paused; the short position the signal turned against could not be sold: RuntimeError: no answer"
    )


def test_the_save_preview_warns_when_a_refused_entry_is_to_leave_the_position_open():
    import json

    from src.admin.config_catalog.checks import warnings

    with open("etc/config_example.json", encoding="utf-8") as config_file:
        config = json.load(config_file)
    path = "trade.config.position_management.close_on_refused_entry"

    assert path not in [found["path"] for found in warnings(config)]
    config["trade"]["config"]["position_management"]["close_on_refused_entry"] = False
    (found,) = [found for found in warnings(config) if found["path"] == path]
    assert "against the strategy" in found["message"]
