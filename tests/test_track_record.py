"""The research's track record (src/admin/track_record.py): how each family's and each maker's conclusions held later."""

from src.admin.track_record import AGENT, family_record, forward_kind, maker_of, record


def experiment(experiment_id, family, verdict, *, via="agent:abc", trials=10, bar=None):
    return {"id": experiment_id, "family": family, "verdict": verdict, "made_via": via, "trials": trials,
            "checks": {"bar": bar} if bar else None}


def test_the_makers_are_the_agent_and_each_token():
    assert maker_of({"made_via": "agent:abc123"}) == AGENT
    assert maker_of({"made_via": "claude-mac"}) == "claude-mac"
    assert maker_of({}) == "unknown"


def test_how_a_forward_check_stands_in_a_word():
    assert [forward_kind(check) for check in (
        None, {"verdict": "as_expected"}, {"verdict": "better_than_expected"}, {"verdict": "watch"},
        {"verdict": "broken"}, {"verdict": None}, {"verdict": "too_early"}, {"verdict": "no_model"},
    )] == [None, "held", "held", "watch", "broken", "too_early", "too_early", "nothing_to_judge"]


def test_each_family_and_maker_counts_its_verdicts_and_how_its_kept_ones_held():
    experiments = [
        experiment("a", "kernel", "kept", bar="fail", trials=95),
        experiment("b", "kernel", "rejected", trials=17),
        experiment("c", "kernel", "kept", bar="pass", trials=10),
        experiment("d", "day-design", "inconclusive", via="claude-mac", trials=251),
        experiment("e", "day-design", None, via="claude-mac", trials=3),
    ]
    checks = {"a": {"verdict": "broken", "state": "ended"}, "c": {"verdict": "too_early", "state": "active"}}

    found = record(experiments, checks)

    families = {entry["family"]: entry for entry in found["families"]}
    # The most tried first.
    assert [entry["family"] for entry in found["families"]] == ["day-design", "kernel"]
    kernel = families["kernel"]
    assert (kernel["experiments"], kernel["kept"], kernel["rejected"], kernel["trials"]) == (3, 2, 1, 122)
    assert (kernel["kept_met_bar"], kernel["kept_against_bar"]) == (1, 1)
    assert kernel["forward"] == {"active": 1, "held": 0, "watch": 0, "broken": 1, "too_early": 1, "nothing_to_judge": 0}
    # A share only from five judged checks.
    assert kernel["held_share"] is None
    assert families["day-design"]["open"] == 1 and families["day-design"]["inconclusive"] == 1
    makers = {entry["maker"]: entry for entry in found["makers"]}
    assert makers[AGENT]["experiments"] == 3 and makers["claude-mac"]["experiments"] == 2
    assert found["total"]["experiments"] == 5


def test_a_share_held_is_given_from_five_judged_checks():
    experiments = [experiment(str(n), "f", "kept") for n in range(6)]
    checks = {"0": {"verdict": "as_expected"}, "1": {"verdict": "as_expected"}, "2": {"verdict": "better_than_expected"},
              "3": {"verdict": "watch"}, "4": {"verdict": "broken"}, "5": {"verdict": "too_early"}}

    assert family_record(experiments, checks, "f")["held_share"] == 60
    assert family_record(experiments, checks, "nothing")["experiments"] == 0
