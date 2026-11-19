"""The challenger board (src/admin/challengers.py): each forward check against the live configuration, day by day."""

from src.admin.challengers import AHEAD, BEHIND, EVEN, TOO_FEW, board, board_line, paired, sign_test


def test_the_sign_test_is_exact_and_two_sided():
    assert sign_test(5, 0) == 0.0625 and sign_test(0, 5) == 0.0625
    assert sign_test(3, 2) == 1.0 and sign_test(0, 0) is None


def test_only_the_days_both_traded_are_paired():
    pairs = paired([["2026-11-17", 1.0, 2], ["2026-11-18", -2.0, 1], ["2026-11-19", 0.5, 1]], [["2026-11-17", 0.2], ["2026-11-19", 0.4]])

    assert pairs == [("2026-11-17", 1.0, 0.2), ("2026-11-19", 0.5, 0.4)]


def test_a_challenger_ahead_on_most_days_is_said_ahead():
    days = [f"2026-11-{day:02d}" for day in range(2, 14)]
    challenger = [[day, 2.0 + (index % 3) * 0.1, 2] for index, day in enumerate(days)]
    champion = [[day, -0.5, 1] for day in days]

    line = board_line(challenger, champion, seed=1)

    assert line["verdict"] == AHEAD and line["better_days"] == 12 and line["sign_p"] < 0.01
    assert line["interval"][0] > 0 and line["challenger_percent"] > line["champion_percent"]
    assert board_line(champion, challenger, seed=1)["verdict"] == BEHIND


def test_a_mixed_or_short_record_says_little():
    days = [f"2026-11-{day:02d}" for day in range(2, 12)]
    mixed = board_line([[day, (1 if index % 2 else -1) * 3.0, 1] for index, day in enumerate(days)],
                       [[day, 0.0, 1] for day in days], seed=1)
    short = board_line([["2026-11-17", 5.0, 1]], [["2026-11-17", 0.0]], seed=1)

    assert mixed["verdict"] == EVEN and short["verdict"] == TOO_FEW and short["interval"] is None


def test_the_board_lists_the_checks_with_days_against_the_champion_each_kept():
    checks = [{"id": "f1234abcd", "title": "Open block", "state": "active", "days": [["2026-11-17", 1.0, 1]]},
              {"id": "f00000000", "title": "Nothing yet", "state": "active", "days": []},
              {"id": "f5555aaaa", "title": "Kept its own", "state": "ended", "days": [["2026-10-01", 2.0, 1]],
               "champion": [["2026-10-01", 1.5]]}]

    lines = board(checks, [["2026-11-17", 0.5]])

    assert [line["check"] for line in lines] == ["f1234abcd", "f5555aaaa"]
    assert lines[0]["days"] == 1 and lines[1]["days"] == 1 and lines[1]["champion_percent"] == 1.5
