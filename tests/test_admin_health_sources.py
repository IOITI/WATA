"""The web server's health entry says what the sources of the signals leave without a signal."""

from src.admin.health import OK, WARNING, web_server_entry


def test_an_index_set_to_the_engine_with_no_live_rule_is_a_warning():
    [entry] = web_server_entry(2, {"sources": {"us100": "engine"}, "ignored": {}}, live_indices=[])
    assert entry["status"] == WARNING
    assert entry["detail"] == "us100 takes the TA engine's signals and no rule is live on it: nothing trades it"


def test_a_live_rule_on_an_index_that_takes_tradingviews_is_a_warning():
    [entry] = web_server_entry(2, {"sources": {}, "ignored": {}}, live_indices=["us100"])
    assert entry["status"] == WARNING and "the engine sends it none" in entry["detail"]


def test_the_alerts_ignored_are_counted_and_a_web_server_of_before_says_nothing_more():
    [counted] = web_server_entry(1, {"sources": {"us100": "engine"}, "ignored": {"us100": {"count": 4}}}, ["us100"])
    [before] = web_server_entry(1, None, ["us100"])

    assert counted["status"] == OK
    assert counted["detail"] == "1 signal in store; TradingView's alerts ignored since its start: us100 4"
    assert before == {"id": "web_server", "name": "Web server", "status": OK, "detail": "1 signal in store"}
