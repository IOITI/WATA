from src.trade.close_intents import (
    ALERT_GAPS,
    LATER_ALERT_GAP,
    MAX_RETRY_SECONDS,
    SLOW_AFTER_SECONDS,
    SLOW_RETRY_SECONDS,
    VERY_SLOW_AFTER_SECONDS,
    VERY_SLOW_RETRY_SECONDS,
    WORKING_SELL_SECONDS,
    CloseIntents,
    alert_gap,
    retry_after,
)


def test_a_new_intent_is_due_at_once_and_keeps_its_first_reason():
    intents = CloseIntents()

    intent = intents.want("p1", "Performance (Stoploss (-7%) hit at -8%)", now=100.0, action="long", urgent=True)
    again = intents.want("p1", "Explicit Close (End of day)", now=105.0)

    assert again is intent
    assert (intent.reason, intent.action, intent.urgent, intent.since) == (
        "Performance (Stoploss (-7%) hit at -8%)", "long", True, 100.0,
    )
    assert [due.position_id for due in intents.due(100.0, {"p1": {}})] == ["p1"]


def test_only_the_positions_still_listed_are_due():
    intents = CloseIntents()
    intents.want("p1", "a", now=0.0)
    intents.want("p2", "b", now=1.0)

    assert [intent.position_id for intent in intents.due(5.0, {"p2"})] == ["p2"]


def test_the_wait_doubles_after_each_failure_up_to_fifteen_seconds_then_slows_with_age():
    assert [retry_after(attempts, age_seconds=0) for attempts in range(1, 7)] == [1, 2, 4, 8, 15, 15]
    assert retry_after(3, age_seconds=SLOW_AFTER_SECONDS) == SLOW_RETRY_SECONDS
    assert retry_after(30, age_seconds=VERY_SLOW_AFTER_SECONDS) == VERY_SLOW_RETRY_SECONDS


def test_a_price_that_moves_again_brings_the_next_try_close():
    # A position left over a weekend: tried every five minutes, then quoted again at Monday's open.
    intents = CloseIntents()
    intents.want("p1", "Explicit Close (End of day)", now=0.0)
    intents.quoted("p1", 8.0, now=0.0)
    intents.failed("p1", "market closed", now=VERY_SLOW_AFTER_SECONDS)
    assert intents.get("p1").next_at == VERY_SLOW_AFTER_SECONDS + VERY_SLOW_RETRY_SECONDS

    intents.quoted("p1", 8.0, now=VERY_SLOW_AFTER_SECONDS + 1)  # the same price: nothing changes
    assert intents.get("p1").next_at == VERY_SLOW_AFTER_SECONDS + VERY_SLOW_RETRY_SECONDS
    intents.quoted("p1", 7.2, now=VERY_SLOW_AFTER_SECONDS + 2)
    assert intents.get("p1").next_at == VERY_SLOW_AFTER_SECONDS + 2 + MAX_RETRY_SECONDS


def test_a_failure_spaces_the_next_attempt():
    intents = CloseIntents()
    intents.want("p1", "a", now=0.0)

    intents.failed("p1", "no quote", now=0.0)
    assert intents.due(0.5, {"p1"}) == []
    assert [intent.position_id for intent in intents.due(1.0, {"p1"})] == ["p1"]

    intents.failed("p1", "no quote", now=1.0)
    assert intents.due(2.5, {"p1"}) == []
    assert intents.get("p1").next_at == 3.0
    assert (intents.get("p1").attempts, intents.get("p1").last_error) == (2, "no quote")


def test_failures_are_said_at_once_then_less_and_less_often():
    intents = CloseIntents()
    intents.want("p1", "a", now=0.0)
    said_at = [moment for moment in range(0, 4 * 3600, 15) if intents.failed("p1", "no quote", now=float(moment))]

    # The first failure, 5, 10 and 15 minutes later, half an hour after that, then every hour.
    assert said_at[:5] == [0, 300, 600, 900, 2700]
    assert said_at[5] - said_at[4] == LATER_ALERT_GAP
    assert alert_gap(len(ALERT_GAPS) + 3) == LATER_ALERT_GAP


def test_a_sale_saxo_accepted_is_given_time_to_be_filled():
    intents = CloseIntents()
    intents.want("p1", "a", now=0.0)

    intents.accepted("p1", now=2.0)

    assert intents.due(2.0 + WORKING_SELL_SECONDS - 0.1, {"p1"}) == []
    assert intents.get("p1").sell_accepted_at == 2.0
    assert len(intents.due(2.0 + WORKING_SELL_SECONDS, {"p1"})) == 1


def test_an_intent_ends_when_its_position_is_no_longer_listed():
    intents = CloseIntents()
    intents.want("p1", "a", now=0.0)
    intents.want("p2", "b", now=0.0)

    ended = intents.gone({"p2": {}})

    assert [intent.position_id for intent in ended] == ["p1"]
    assert "p1" not in intents and "p2" in intents and len(intents) == 1


def test_position_ids_are_kept_as_text():
    intents = CloseIntents()
    intents.want(7656296579, "a", now=0.0)

    assert "7656296579" in intents and 7656296579 in intents
    assert [intent.position_id for intent in intents.due(0.0, [7656296579])] == ["7656296579"]


def test_nothing_is_said_of_an_intent_that_does_not_exist():
    assert CloseIntents().failed("p9", "no quote", now=0.0) is False
