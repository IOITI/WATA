"""
The picture chart_view gives of a day (src/research/chart_picture.py): a PNG of
its size drawn from numbers, a day of minutes in pixel columns, a zoom a bar a
column with its plots' lines unbroken where a column holds no bar, and a window
without a bar said in words.
"""

import io
import math

from PIL import Image

from src.research import chart_picture

BEGIN = 1_791_957_600_000


def minutes(count: int) -> list:
    return [[BEGIN + 60_000 * n, 25_000 + 20 * math.sin(n / 30), 25_004 + 20 * math.sin(n / 30),
             24_996 + 20 * math.sin(n / 30), 25_001 + 20 * math.sin(n / 30), 1, BEGIN + 60_000 * (n + 1)] for n in range(count)]


def drawn(bars: list, plots: dict | None = None, minutes_shown: int = 840, **more) -> Image.Image:
    png = chart_picture.picture(bars=bars, plots=plots or {}, begin_ms=BEGIN, end_ms=BEGIN + minutes_shown * 60_000,
                                zone="Europe/Paris", title="A day", alerts=more.get("alerts", []),
                                trades=more.get("trades", []), decimals=1)
    return Image.open(io.BytesIO(png))


def test_a_day_is_a_png_of_its_size_with_its_plots_alerts_and_trades():
    bars = minutes(840)
    kernel = [bar[4] - 2 for bar in bars]
    oscillator = [math.sin(n / 10) * 50 for n in range(840)]
    picture = drawn(bars, {"Kernel": kernel, "Oscillator": oscillator},
                    alerts=[{"at": bars[100][6], "title": "Long", "action": "long"},
                            {"at": bars[400][6], "title": "Exit", "action": "close-position"}],
                    trades=[{"id": 1, "direction": "long", "in_ms": bars[101][0], "out_ms": bars[180][0],
                             "asset_in": bars[101][4], "asset_out": bars[180][4], "result_percent": -0.84}])

    assert (picture.format, picture.size) == ("PNG", (chart_picture.WIDTH, chart_picture.HEIGHT))
    # The price pane's background and its plots' colours are in it.
    colours = {colour for _, colour in picture.convert("RGB").getcolors(maxcolors=4096)}
    assert chart_picture.BACKGROUND in colours


def test_a_window_without_a_bar_is_said_in_words():
    picture = drawn([], {})

    assert picture.size == (chart_picture.WIDTH, chart_picture.HEIGHT)


class Draw:
    """Stands for Pillow's drawing: the lines and dots asked, by their points."""

    def __init__(self):
        self.lines, self.dots = [], []

    def line(self, points, fill=None, width=1):
        self.lines.append(list(points))

    def ellipse(self, box, fill=None):
        self.dots.append(box)


def test_a_plot_runs_through_columns_without_a_bar_and_breaks_where_it_has_no_value():
    with_value = {"plots": {"Kernel": 1.0}}
    no_value = {"plots": {}}
    draw = Draw()

    chart_picture._line(draw, [with_value, None, with_value, None, with_value, no_value, with_value],
                        "Kernel", (0, 0, 0), lambda place: place * 10.0, lambda value: value)

    # Three points joined across the columns without a bar; the na alone breaks it, its last point a dot.
    assert draw.lines == [[(0.0, 1.0), (20.0, 1.0), (40.0, 1.0)]] and len(draw.dots) == 1


def test_a_zoom_has_a_column_a_bar_and_a_day_as_many_as_fit():
    bars = minutes(120)
    columns = chart_picture._columns(bars, {}, BEGIN, BEGIN + 120 * 60_000, 120)

    assert all(column is not None for column in columns)
    # A day of 840 minutes in the 295 columns of four pixels that fit: some hold three bars, none is empty.
    day = minutes(840)
    fit = (chart_picture.WIDTH - chart_picture.RIGHT - chart_picture.LEFT) // chart_picture.COLUMN
    gathered = chart_picture._columns(day, {}, BEGIN, BEGIN + 840 * 60_000, fit)
    assert fit == 295 and all(column is not None for column in gathered)
    assert gathered[0]["o"] == day[0][1] and gathered[-1]["c"] == day[-1][4]
