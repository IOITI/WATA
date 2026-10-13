import json
from dataclasses import replace
from datetime import date

from src.price_recorder.bars import BAR_SECONDS
from src.strategy.baseline import RANDOM_ENTRY, Baseline, _replay
from src.strategy.bars import IndexBar
from src.strategy.engine import LONG, SHORT, Decision, LocalClock
from src.strategy.execution import Simulator, TurboModel, WataRules

CLOCK = LocalClock("Europe/Paris")
DAY = date(2026, 10, 12)
NEXT_DAY = date(2026, 10, 13)

with open("etc/config_example.json", encoding="utf-8") as config_file:
    # Stop -15 %, daily target 1.7 %, hours 8 to 22, ten minutes after a loss.
    RULES = WataRules.from_config(json.load(config_file))
# A turbo at 4.00 that moves by 1.00 for 100 points, without spread: 4 points are 1 %.
TURBO = TurboModel(entry_price=4.0, points_per_unit=100.0, spread=0.0)
INDEX = 30000.0
WHOLE_DAY = (0, 86400)


def day_of_bars(clock_time: str, count: int, step: float = 0.0, day: date = DAY) -> list[IndexBar]:
    """``count`` bars from ``clock_time``, the index moving by ``step`` in each."""
    start = CLOCK.epoch(day, clock_time)
    bars = []
    for number in range(count):
        opened, closed = INDEX + step * number, INDEX + step * (number + 1)
        bars.append(IndexBar(
            start + BAR_SECONDS * number, opened, max(opened, closed), min(opened, closed), closed,
            opened, max(opened, closed), min(opened, closed), closed, 1,
        ))
    return bars


def baseline(repetitions: int = 40, rules: WataRules = RULES, window=WHOLE_DAY, turbos=(TURBO,), seed: int = 1) -> Baseline:
    return Baseline(rules, list(turbos), CLOCK, window=window, repetitions=repetitions, seed=seed)


def test_each_repetition_takes_as_many_entries_as_were_asked_at_moments_of_its_own():
    # An hour in which the index gains a point every ten seconds: a long reaches the
    # day's target in a minute or so, a short loses until its stop or the end of the prices.
    bars = day_of_bars("09:00", 360, step=1.0)
    random_entries = baseline()

    random_entries.day(bars, entries=1)
    random_entries.finish()
    found = random_entries.against(0, 1.7)

    assert (found["repetitions"], found["seed"], found["entries"], found["level"]) == (40, 1, 1, 90)
    assert found["totals"] == sorted(found["totals"]) and len(found["totals"]) == 40
    # The coin fell on both sides: the longs all ended at the target, the shorts all lost.
    assert set(found["totals"]) > {1.7} and found["worst"] <= -15.0 and found["best"] == 1.7
    assert all(total == 1.7 or total < 0 for total in found["totals"])
    assert (found["trades"], found["low"] < found["median"] <= found["high"]) == (1.0, True)
    # A strategy that made 1.7 % did better than the shorts, and as well as the longs: those count for half.
    shorts = sum(1 for total in found["totals"] if total < 0)
    assert found["beaten"] == round((shorts + (40 - shorts) / 2) * 100 / 40, 1)
    # Each repetition won its one trade or lost it: the middle of them is one, the other, or between the two.
    assert found["win_rate"] in (0.0, 50.0, 100.0)


def test_the_same_seed_draws_the_same_entries_and_another_seed_others():
    bars = day_of_bars("09:00", 360, step=1.0)

    def totals(seed: int) -> list[float]:
        random_entries = baseline(seed=seed)
        random_entries.day(bars, entries=2)
        random_entries.finish()
        return random_entries.against(0, 0.0)["totals"]

    assert totals(1) == totals(1)
    assert totals(1) != totals(2)


def test_a_result_is_placed_among_the_random_ones():
    # Nothing moves and the turbo has no spread: every random entry ends at nothing.
    random_entries = baseline(repetitions=10)
    random_entries.day(day_of_bars("09:00", 60), entries=1)
    random_entries.finish()

    assert random_entries.against(0, 0.0)["totals"] == [0.0] * 10
    assert [random_entries.against(0, total)["beaten"] for total in (-1.0, 0.0, 1.0)] == [0.0, 50.0, 100.0]
    assert random_entries.against(0, 0.0)["win_rate"] == 0.0


def test_random_entries_are_only_taken_where_the_strategy_and_the_trader_would_enter():
    # Quotes from seven in the morning, the trader from eight, the strategy until half past eight.
    bars = day_of_bars("07:00", 720)
    random_entries = baseline(repetitions=30, rules=replace(RULES, day_profit_target=1000.0), window=(0, 8 * 3600 + 1800))
    entered: list[int] = []
    for repetition in random_entries._repetitions:
        decide = repetition.simulators[0].decide
        repetition.simulators[0].decide = lambda decision, decide=decide: (entered.append(decision.at), decide(decision))

    random_entries.day(bars, entries=3)

    eight, half_past = CLOCK.epoch(DAY, "08:00"), CLOCK.epoch(DAY, "08:30")
    assert len(entered) == 90
    # Known at the end of a bar that began in those thirty minutes.
    assert min(entered) >= eight + BAR_SECONDS and max(entered) <= half_past


def test_without_a_moment_to_enter_at_or_an_entry_asked_there_is_nothing_to_compare_with():
    before_hours = baseline()
    before_hours.day(day_of_bars("06:00", 60), entries=2)
    before_hours.finish()
    assert before_hours.entries == 0 and before_hours.against(0, 1.7) is None

    nothing_asked = baseline()
    nothing_asked.day(day_of_bars("09:00", 60), entries=0)
    nothing_asked.day([], entries=3)
    nothing_asked.finish()
    assert nothing_asked.against(0, 1.7) is None


def test_no_more_entries_are_drawn_than_there_are_moments():
    random_entries = baseline(repetitions=5)

    # Three bars: the last one has no next bar to act at.
    random_entries.day(day_of_bars("09:00", 3), entries=10)

    assert random_entries.entries == 2


def test_a_position_left_open_when_the_prices_stopped_is_closed_on_the_next_day_whatever_is_asked_then():
    random_entries = baseline(repetitions=5, rules=replace(RULES, day_profit_target=1000.0))
    random_entries.day(day_of_bars("09:00", 30), entries=1)
    assert all(repetition.simulators[0].busy for repetition in random_entries._repetitions)

    random_entries.day(day_of_bars("09:00", 30, day=NEXT_DAY), entries=0)
    random_entries.finish()

    assert not any(repetition.simulators[0].busy for repetition in random_entries._repetitions)
    found = random_entries.against(0, 0.0)
    assert (found["trades"], found["totals"]) == (1.0, [0.0] * 5)


def test_each_turbo_assumed_is_given_the_same_entries():
    bars = day_of_bars("09:00", 360, step=1.0)
    more_leverage = TurboModel(entry_price=4.0, points_per_unit=50.0, spread=0.0)
    random_entries = baseline(turbos=(TURBO, more_leverage))

    random_entries.day(bars, entries=1)
    random_entries.finish()
    first, second = random_entries.against(0, 0.0), random_entries.against(1, 0.0)

    # The same coin: as many shorts on one as on the other, each lost at its own stop.
    assert sum(1 for total in first["totals"] if total < 0) == sum(1 for total in second["totals"] if total < 0)
    assert first["totals"] != second["totals"]


def test_passing_over_the_bars_in_which_nothing_is_open_changes_nothing():
    bars = day_of_bars("09:00", 360, step=1.0)
    picks = [(10, SHORT), (100, LONG), (101, LONG), (300, SHORT)]
    rules = replace(RULES, cooldown_after_loss_minutes=0)

    skipping = Simulator(rules, TURBO, CLOCK)
    _replay(skipping, bars, picks)
    skipping.finish()

    every_bar = Simulator(rules, TURBO, CLOCK)
    decided = dict(picks)
    for place, bar in enumerate(bars):
        every_bar.on_bar(bar)
        if place in decided:
            every_bar.decide(Decision(bar.start + BAR_SECONDS, decided[place], RANDOM_ENTRY))
    every_bar.finish()

    assert skipping.trades == every_bar.trades and len(skipping.trades) >= 2
    assert skipping.refusals == every_bar.refusals
    assert [day.percent for day in skipping.days] == [day.percent for day in every_bar.days]
