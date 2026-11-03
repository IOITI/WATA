"""
A day as chart_view reads it (src/research/day_view.py): the bars gathered
into rows of a length that fits, the table, what price did after each alert
(the edge's own moves), the day's swings and levels, a run's trades marked in
their rows, and the answer held to its size, what is cut first being said.
"""

import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.backtest.data import AssetSeries
from src.backtest.edge import moves_after
from src.research import day_view

PARIS = ZoneInfo("Europe/Paris")
# 14 Oct 2026, 08:00 in Paris.
START = int(datetime(2026, 10, 14, 6, tzinfo=timezone.utc).timestamp())


def minute_bars(count: int, start: int = START, price=lambda n: 25000.0 + n) -> list:
    return [[(start + 60 * n) * 1000, price(n), price(n) + 2, price(n) - 1, price(n) + 1, 1, (start + 60 * (n + 1)) * 1000]
            for n in range(count)]


def series_of(closes, start: int = START) -> AssetSeries:
    series = AssetSeries()
    for place, close in enumerate(closes):
        series.ts.append(start + 10 * place)
        series.close.append(float(close))
    return series


def test_a_day_is_gathered_into_the_shortest_rows_that_fit_and_a_bar_a_row_when_it_fits():
    opens = [bar[0] for bar in minute_bars(840)]

    # 14 hours of minutes in 180 rows: 3 minutes make 280, 5 minutes 168.
    assert day_view.step_for(opens, 180, 60) == 300
    assert day_view.step_for(opens, 840, 60) is None
    # A row is never a bar's own length: two-minute rows for 400 rows of minute bars would hold 420.
    assert day_view.step_for(opens, 420, 60) == 120
    # Range bars have no length: rows of any length that fits (17.5 hours: 210 rows of 5 minutes, 105 of 10).
    assert day_view.step_for([(START + 7 * n) * 1000 for n in range(9000)], 200, None) == 600
    assert [day_view.step_said(step) for step in (None, 20, 300, 3600)] == ["a bar", "20 s", "5 min", "1 h"]


def test_a_row_holds_its_bars_prices_and_each_plot_at_its_last_value():
    bars = minute_bars(10)
    kernel = [100.0 + n for n in range(10)]
    # A mark drawn on one bar alone, na elsewhere: the row keeps it.
    mark = [None] * 10
    mark[6] = 1.0

    rows = day_view.gathered(bars, {"Kernel": kernel, "Mark": mark, "Colour": ["#ff0000"] * 10}, 300)

    assert [row["n"] for row in rows] == [5, 5] and [(row["first"], row["last"]) for row in rows] == [(0, 4), (5, 9)]
    first = rows[0]
    assert (first["o"], first["h"], first["l"], first["c"]) == (25000.0, 25006.0, 24999.0, 25005.0)
    assert first["plots"] == {"Kernel": 104.0} and rows[1]["plots"] == {"Kernel": 109.0, "Mark": 1.0}
    assert len(day_view.gathered(bars, {}, None)) == 10


def test_the_table_writes_times_in_the_trading_timezone_and_counts_the_alerts_of_a_row():
    rows = day_view.gathered(minute_bars(10), {"Kernel": [100.123456789] * 10}, 300)

    columns, text = day_view.table(rows, ["Kernel"], {0: ["Long", "Long", "Exit"]}, {1: ["#3 long in"]}, PARIS, 1, False)

    assert columns == ["time", "open", "high", "low", "close", "bars", "Kernel", "alerts", "trades"]
    lines = text.splitlines()
    assert lines[1] == "08:00,25000.0,25006.0,24999.0,25005.0,5,100.123,Long×2 Exit,"
    assert lines[2].startswith("08:05,") and lines[2].endswith(",#3 long in")


def test_what_price_did_after_an_alert_is_the_edges_own_move_signed_by_its_direction():
    # Up a point every ten seconds from 25,000: 6 points a minute, 2.4 basis points.
    series = series_of([25000 + n for n in range(2000)])
    moment = START + 600

    up = day_view.moved(series, moment, "long")
    down = day_view.moved(series, moment, "short")
    unsigned = day_view.moved(series, moment, "close-position")

    edge = moves_after(series, moment, "long")
    assert up["after"] == {str(minutes): round(edge[minutes][0], 1) for minutes in (5, 15, 30, 60)}
    assert up["after"]["5"] > 0 and down["after"]["5"] == -up["after"]["5"] and unsigned == up
    assert (up["best"], up["worst"]) == (round(edge[30][1], 1), round(edge[30][2], 1))
    assert day_view.moved(series, START - 3600, "long") is None


def test_each_alerts_title_says_how_often_price_went_its_way():
    series = series_of([25000 + n for n in range(2000)])
    alerts = [{"title": "Long", "action": "long", "at": START + 600 + 60 * n,
               "moves": day_view.moved(series, START + 600 + 60 * n, "long")} for n in range(3)]
    alerts.append({"title": "Short", "action": "short", "at": START + 900, "moves": day_view.moved(series, START + 900, "short")})
    alerts.append({"title": "Bell", "action": None, "at": START + 900, "moves": None})

    said = day_view.alert_moves(series, alerts)

    assert said["Long"]["count"] == 3 and said["Long"]["signed"]
    assert said["Long"]["after_bp"]["15"]["its_way"] == 1.0 and said["Long"]["after_bp"]["15"]["of"] == 3
    assert said["Short"]["after_bp"]["15"]["its_way"] == 0.0 and said["Short"]["after_bp"]["15"]["mean"] < 0
    # Not an entry: price up is positive, said as such.
    assert said["Bell"] == {"count": 1, "action": None, "signed": False, "after_bp": {}, "best_30m_median": None,
                            "worst_30m_median": None}


def test_a_day_s_swings_are_its_turns_the_threshold_raised_until_they_are_few_enough():
    # A wave of 80 points around 25,000, a turn every 47 minutes or so.
    series = series_of([25000 + 40 * math.sin(n / 90) for n in range(5040)])

    found = day_view.swings(series, START, START + 50_400, PARIS, 1)

    assert 10 <= len(found) <= day_view.SWINGS_MOST
    assert [entry["kind"] for entry in found[:4]] == ["high", "low", "high", "low"]
    assert found[0]["price"] == 25040.0 and found[1]["price"] == 24960.0 and found[1]["move_percent"] == -0.319
    # A noisy day of many small turns: the threshold is raised, the swings are never more than SWINGS_MOST.
    noisy = series_of([25000 + 60 * math.sin(n / 9) for n in range(5040)])
    assert len(day_view.swings(noisy, START, START + 50_400, PARIS, 1)) <= day_view.SWINGS_MOST
    assert day_view.swings(series_of([25000] * 100), START, START + 1000, PARIS, 1) == []


def test_the_day_s_prices_and_levels_are_read_from_its_ten_second_closes():
    closes = [25000 + n for n in range(400)] + [25400 - n for n in range(400)]
    series = series_of(closes, START - 4000)

    prices = day_view.day_prices(series, START, START + 4000, PARIS, 1)
    levels = day_view.levels(series, START - 4000, START, START + 1000, PARIS, 1)

    assert (prices["open"], prices["high"], prices["high_at"]) == (25400.0, 25400.0, "08:00")
    assert prices["low"] == 25001.0 and prices["move_percent"] == -1.571
    assert levels["before_high"] == 25399 and levels["before_low"] == 25000
    assert levels["us_open_at"] == "08:16" and levels["first_hour_high"] >= levels["first_hour_low"]


def test_a_run_s_trades_are_marked_in_their_rows_with_their_result():
    rows = day_view.gathered(minute_bars(30), {}, 300)
    trades = [{"id": 3, "direction": "short", "performance_percent": -0.84, "max_performance_percent": 0.2,
               "min_performance_percent": -1.1, "signal_id": 7,
               "entry": {"filled_at": "2026-10-14T06:07:30Z", "asset_price": 25007.5},
               "exit": {"filled_at": "2026-10-14T06:21:00Z", "asset_price": 25021.0, "reason": "Stop loss hit"}}]

    marks, lines = day_view.trade_marks(trades, rows)

    assert marks == {1: ["#3 short in"], 4: ["#3 out -0.84%"]}
    assert lines == [{"id": 3, "direction": "short", "in": "2026-10-14T06:07:30Z", "out": "2026-10-14T06:21:00Z",
                      "asset_in": 25007.5, "asset_out": 25021.0, "result_percent": -0.84, "best_percent": 0.2,
                      "worst_percent": -1.1, "exit_reason": "Stop loss hit", "signal_id": 7}]
    # A fill before the hours shown, or after them, is in no row.
    early = [{**trades[0], "entry": {"filled_at": "2026-10-14T05:00:00Z"}, "exit": {"filled_at": "2026-10-14T09:00:00Z"}}]
    assert day_view.trade_marks(early, rows)[0] == {}


def test_plots_whose_values_are_no_prices_go_in_a_pane_of_their_own():
    over, under = day_view.panes({"Kernel": [25010.0, 25020.0], "RSI": [40.0, 60.0], "Empty": [None, None]}, 25000.0, 25050.0)

    assert (over, under) == (["Kernel"], ["RSI"])


def test_an_answer_too_long_is_cut_in_order_the_listed_alerts_first():
    asked = []

    def build(listed, plots, coarser, swings):
        asked.append((listed, plots, coarser, swings))
        size = (500 if listed is None else listed * 10) + (400 if plots is None else 100) + 1000 // (coarser + 1)
        return {"size": size, "cut": (listed, plots, coarser, swings)}

    def size(answer):
        return answer["size"]

    assert day_view.fitted(build, 5000, size)["cut"] == (None, None, 0, None)
    assert day_view.fitted(build, 1700, size)["cut"] == (20, None, 0, None)
    assert day_view.fitted(build, 1500, size)["cut"] == (0, None, 0, None)
    asked.clear()
    assert day_view.fitted(build, 1000, size)["cut"] == (0, 2, 1, None)
    assert asked == [(None, None, 0, None), (20, None, 0, None), (0, None, 0, None), (0, 2, 0, None), (0, 2, 1, None)]
    # Nothing fits: the last attempt, everything cut, is the answer.
    assert day_view.fitted(build, 10, size)["cut"] == (0, 2, len(day_view.STEPS_SECONDS), 8)
