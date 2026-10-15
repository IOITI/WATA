import json
import random
from datetime import date, datetime, timezone

import pytest

from src.price_recorder.bars import Bar
from src.strategy import calibration
from src.strategy.calibration import FAIR, FEW, TOO_FEW, Observation, calibrate, describe, fit, observe, sample_size
from src.strategy.engine import LONG, SHORT
from src.strategy.history.dukascopy import day_path, write_day_file

# Monday 12 October 2026, 08:00:00 UTC.
START = int(datetime(2026, 10, 12, 8, tzinfo=timezone.utc).timestamp())


def moment(seconds: int) -> str:
    return datetime.fromtimestamp(START + seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trade(direction: str, opened: int, closed: int, bought: float, sold: float, position_id: str = "p") -> dict:
    return {
        "position_id": position_id, "direction": direction, "opened_at": moment(opened), "closed_at": moment(closed),
        "open_price": bought, "close_price": sold,
    }


def of_a_turbo(moves: list[float], points_per_unit: float = 100.0, spread: float = 0.04, bought: float = 5.0) -> list[Observation]:
    """What trades on a turbo of that leverage and spread would show, for each move of the index."""
    return [Observation(f"p{number}", bought, bought + move / points_per_unit - spread, move) for number, move in enumerate(moves)]


MOVES = [8.0, -60.0, 7.5, 9.0, -58.0, 8.2, 30.0, -20.0, 7.9, -61.0, 12.0, 8.4]


# ── what a trade says of the turbo ──


def test_a_trade_says_what_the_index_did_in_its_direction_while_the_turbo_was_held():
    index = {START: 30000.0, START + 600: 30012.0}.get

    observations, without_prices, unreadable = observe(
        [trade(LONG, 0, 600, 4.40, 4.48, "p1"), trade(SHORT, 0, 600, 4.40, 4.25, "p2")], index,
    )

    assert (without_prices, unreadable) == (0, 0)
    assert [(seen.reference, seen.move, round(seen.change, 2)) for seen in observations] == [("p1", 12.0, 0.08), ("p2", -12.0, -0.15)]


def test_a_trade_without_index_prices_or_that_cannot_be_read_is_counted_apart():
    index = {START: 30000.0, START + 600: 30012.0}.get
    trades = [
        trade(LONG, 0, 600, 4.40, 4.48),
        trade(LONG, 0, 1200, 4.40, 4.48),               # no price when it was closed
        trade("flat", 0, 600, 4.40, 4.48),
        trade(LONG, 600, 0, 4.40, 4.48),                # closed before it was opened
        trade(LONG, 0, 600, 0, 4.48),
        {**trade(LONG, 0, 600, 4.40, 4.48), "close_price": None},
        {"direction": LONG},
    ]

    observations, without_prices, unreadable = observe(trades, index)

    assert (len(observations), without_prices, unreadable) == (1, 1, 5)


# ── the turbo that fits them ──


def test_trades_on_one_turbo_give_back_its_leverage_and_its_spread():
    found = fit(of_a_turbo(MOVES))

    assert (found["fitted"], found["reason"], found["used"], found["set_aside"]) == (True, None, 12, 0)
    assert (found["points_per_unit"], found["spread"], found["entry_price"]) == (100.0, 0.04, 5.0)
    # Nothing is left unexplained, and the range is the value.
    assert (found["points_per_unit_low"], found["points_per_unit_high"]) == (100.0, 100.0)
    assert (found["median_error"], found["ninth_error"], found["level"]) == (0.0, 0.0, 90)
    assert found["sample"] == TOO_FEW


def test_the_range_of_the_leverage_holds_what_noisy_trades_were_made_with():
    noise = random.Random(4)
    moves = [noise.uniform(-60, 30) for _ in range(120)]
    noisy = [
        Observation(seen.reference, seen.open_price, seen.close_price + noise.gauss(0, 0.03), seen.move)
        for seen in of_a_turbo(moves, points_per_unit=104.0)
    ]

    found = fit(noisy)

    assert found["points_per_unit_low"] < 104.0 < found["points_per_unit_high"]
    assert found["points_per_unit_high"] - found["points_per_unit_low"] < 10
    assert 0.02 < found["spread"] < 0.06
    # Three hundredths of a turbo at five: about half a point of percent.
    assert 0.2 < found["median_error"] < 0.8 < found["ninth_error"] < 2
    assert found["sample"] == FAIR


def test_a_trade_unlike_the_others_is_set_aside_once():
    odd = Observation("odd", 5.0, 9.0, 8.0)

    found = fit(of_a_turbo(MOVES) + [odd])

    assert (found["used"], found["set_aside"], found["points_per_unit"], found["spread"]) == (12, 1, 100.0, 0.04)


def test_what_wata_paid_for_its_turbos_is_read_from_the_trades():
    prices = [4.1, 4.2, 4.4, 4.4, 4.5, 4.6, 5.0, 6.0, 9.0, 12.0, 4.3]
    observations = [Observation("p", price, price + move / 100 - 0.04, move) for price, move in zip(prices, MOVES)]

    found = fit(observations)

    assert (found["entry_price"], found["entry_price_low"], found["entry_price_high"]) == (4.5, 4.2, 9.0)


@pytest.mark.parametrize("observations, reason", [
    (of_a_turbo(MOVES[:4]), "4 trade(s) with prices: 5 at least are needed"),
    (of_a_turbo([8.0] * 6), "The index moved by the same amount in every trade"),
    (of_a_turbo(MOVES, points_per_unit=-100.0), "The turbos did not follow the index"),
])
def test_trades_that_describe_no_turbo_say_why(observations, reason):
    found = fit(observations)

    assert found["fitted"] is False and reason in found["reason"]
    assert "points_per_unit" not in found
    # What was paid is known all the same.
    assert found["entry_price"] == 5.0


def test_trades_too_scattered_to_bound_the_leverage_leave_it_unbounded():
    noise = random.Random(1)
    scattered = [Observation("p", 5.0, 5.0 + move / 100 + noise.gauss(0, 2.0), move) for move in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]]

    found = fit(scattered)

    if found["fitted"]:
        assert found["points_per_unit_high"] is None
    else:
        assert "did not follow the index" in found["reason"]


@pytest.mark.parametrize("trades, size", [(29, TOO_FEW), (30, FEW), (99, FEW), (100, FAIR)])
def test_a_line_through_a_few_trades_is_a_first_idea(trades, size):
    assert sample_size(trades) == size


def test_what_was_measured_is_said_in_words():
    index = {START + 600 * number: 30000.0 + 10 * number for number in range(13)}.get
    trades = [trade(LONG, 600 * number, 600 * (number + 2), 5.0, 5.0 + 20 / 100 - 0.04) for number in range(6)]
    trades += [trade(SHORT, 0, 600 * number, 5.0, 5.0 - 10 * number / 100 - 0.04) for number in range(1, 5)]

    found = calibrate(trades + [trade(LONG, 0, 99999, 5.0, 5.2)], index)
    said = describe(found)

    assert (found["trades"], found["used"], found["without_prices"], found["points_per_unit"], found["spread"]) == (11, 10, 1, 100.0, 0.04)
    assert "11 closed trade(s): 10 used, 1 without index prices at their open or close, 0 unreadable, 0 set aside" in said
    assert "1.00 of turbo for 100 index points (90 %: 100 to 100), spread 0.04." in said
    assert "Under 30 trades: a first idea" in said

    nothing = describe(calibrate(trades[:2], index))
    assert "No turbo measured: 2 trade(s) with prices: 5 at least are needed." in nothing


# ── the command ──


def _prices(root) -> None:
    """Two hours in which the index gains a point every ten seconds."""
    bars = [
        Bar("index", START + 10 * number, 30000.0 + number, 30001.0 + number, 30000.0 + number, 30001.0 + number,
            30000.0 + number, 30001.0 + number, 30000.0 + number, 30001.0 + number, 3, 0)
        for number in range(720)
    ]
    write_day_file(day_path(str(root), "us100", date(2026, 10, 12)), bars, symbol="TEST", label="us100")


def test_the_command_measures_the_turbo_on_the_trades_of_a_file_with_the_history_fetched(tmp_path, capsys):
    _prices(tmp_path / "prices")
    # Held for 100, 200... seconds: the index gains 10, 20... points, a long as much, a short loses it.
    trades = [trade(LONG, 0, 100 * number, 5.0, round(5.0 + 10 * number / 100 - 0.04, 4)) for number in range(1, 5)]
    trades += [trade(SHORT, 1000, 1000 + 100 * number, 5.0, round(5.0 - 10 * number / 100 - 0.04, 4)) for number in range(1, 5)]
    (tmp_path / "trades.json").write_text(json.dumps(trades))
    out = tmp_path / "out" / "calibration.json"

    code = calibration.main(["--trades", str(tmp_path / "trades.json"), "--root", str(tmp_path / "prices"), "--out", str(out)])

    assert code == 0
    found = json.loads(out.read_text())
    assert (found["fitted"], found["trades"], found["used"], found["points_per_unit"], found["spread"]) == (True, 8, 8, 100.0, 0.04)
    assert "1.00 of turbo for 100 index points" in capsys.readouterr().out
    assert not (tmp_path / "out" / "calibration.json.part").exists()


def test_the_command_says_so_when_the_trades_cannot_be_read(tmp_path, capsys):
    (tmp_path / "trades.json").write_text(json.dumps({"trades": []}))

    assert calibration.main(["--trades", str(tmp_path / "trades.json")]) == 2
    assert calibration.main(["--trades", str(tmp_path / "absent.json")]) == 2
    assert "The trades cannot be read" in capsys.readouterr().err
