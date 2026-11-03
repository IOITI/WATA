"""
The picture of a day chart_view gives beside its table (the ``view`` study of
src/research/study.py), drawn with Pillow in the study's child: the asset's
candles over the window, the script's plots on them (or in a pane under them
when their values are not prices), its alerts as marks, and a run's trades
from their entry to their exit with their result; the times in the trading
timezone, the prices on the right.

PineTS's own drawings (labels, lines, boxes) are not in it: the runner gives
the plots and the alerts alone. The operator's screen has them, and a question
asked from a run's day sends it.

Pillow is imported here alone, so only the study's child loads it. Nothing
here reads an image: what is drawn comes from numbers.
"""

import io
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from src.research.day_view import finite, panes

WIDTH, HEIGHT = 1280, 720
# The plot area's margins: the title and legend above, the price axis on the right, the times below.
LEFT, RIGHT, TOP, BOTTOM = 12, 86, 50, 30
# A candle's column, in pixels at least: the window is gathered in as many columns as fit, a bar each when they are
# fewer (a zoom), its candles then as wide as their room.
COLUMN = 4
# The share of the plot area the pane under the prices takes, when a plot is no price.
PANE_SHARE = 0.27
MOST_PLOTS = 4
# How many alerts are named by their title on the picture; the rest are marks alone.
NAMED_ALERTS = 12

BACKGROUND = (255, 255, 255)
GRID = (232, 234, 238)
AXIS = (90, 96, 108)
TEXT = (28, 32, 40)
UP = (22, 150, 136)
DOWN = (220, 68, 68)
NEUTRAL = (120, 126, 138)
# Okabe and Ito's colours, which colour-blind readers tell apart: blue, orange, purple, green.
PLOT_COLOURS = ((0, 114, 178), (230, 159, 0), (204, 121, 167), (0, 158, 115))
TRADE_WIN = (0, 128, 96)
TRADE_LOSS = (200, 50, 40)


def _font(size: int):
    return ImageFont.load_default(size=size)


def _nice_step(span: float, count: int) -> float:
    """A round step (1, 2 or 5 times a power of ten) that cuts ``span`` in about ``count`` parts."""
    if span <= 0:
        return 1.0
    raw = span / max(1, count)
    power = 10 ** math.floor(math.log10(raw))
    for multiple in (1, 2, 5, 10):
        if raw <= multiple * power:
            return multiple * power
    return 10 * power


def _columns(bars: list, plots: dict[str, list], begin_ms: int, end_ms: int, count: int) -> list[dict | None]:
    """The bars of [begin, end) in ``count`` columns of time: each column's open, high, low, close and plots."""
    span = max(1, end_ms - begin_ms)
    columns: list[dict | None] = [None] * count
    for index, bar in enumerate(bars):
        if not begin_ms <= bar[0] < end_ms:
            continue
        place = min(count - 1, int((bar[0] - begin_ms) * count / span))
        column = columns[place]
        if column is None:
            column = columns[place] = {"o": bar[1], "h": bar[2], "l": bar[3], "c": bar[4], "plots": {}}
        column["h"], column["l"], column["c"] = max(column["h"], bar[2]), min(column["l"], bar[3]), bar[4]
        for name, values in plots.items():
            value = finite(values[index]) if index < len(values) else None
            if value is not None:
                column["plots"][name] = value
    return columns


def picture(*, bars: list, plots: dict[str, list], begin_ms: int, end_ms: int, zone: str, title: str,
            alerts: list[dict], trades: list[dict], decimals: int | None) -> bytes:
    """
    The day as a PNG of WIDTH × HEIGHT. ``bars``: the script's bars ([open ms,
    open, high, low, close, volume, close ms]); ``plots``: up to MOST_PLOTS of
    its plots, a value a bar; ``alerts``: each one's bar close (``at`` in ms),
    title and action; ``trades``: a run's, with their times (ms) and the
    asset's prices in and out, and their result in percent.
    """
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    small, normal, large = _font(12), _font(13), _font(15)
    tz = ZoneInfo(zone)
    plots = dict(list(plots.items())[:MOST_PLOTS])
    colours = {name: PLOT_COLOURS[place] for place, name in enumerate(plots)}

    area_left, area_right, area_top, area_bottom = LEFT, WIDTH - RIGHT, TOP, HEIGHT - BOTTOM
    shown = sum(1 for bar in bars if begin_ms <= bar[0] < end_ms)
    count = max(1, min((area_right - area_left) // COLUMN, shown))
    width = (area_right - area_left) / count
    columns = _columns(bars, plots, begin_ms, end_ms, count)
    present = [column for column in columns if column is not None]
    draw.text((LEFT, 8), title, fill=TEXT, font=large)
    if not present:
        draw.text((LEFT, area_top + 20), "No bar in this window", fill=AXIS, font=normal)
        return _png(image)

    low = min(column["l"] for column in present)
    high = max(column["h"] for column in present)
    over, under = panes(plots, low, high)
    for name in over:
        values = [column["plots"][name] for column in present if name in column["plots"]]
        if values:
            low, high = min(low, min(values)), max(high, max(values))
    pad = (high - low) * 0.06 or max(abs(high) * 0.001, 1e-9)
    low, high = low - pad, high + pad
    price_bottom = area_bottom if not under else int(area_bottom - (area_bottom - area_top) * PANE_SHARE)
    pane_top = price_bottom + 12

    def x_of(moment_ms: float) -> float:
        return area_left + (moment_ms - begin_ms) / max(1, end_ms - begin_ms) * (area_right - area_left)

    def centre_of(place: int) -> float:
        return area_left + (place + 0.5) * width

    def column_of(moment_ms: float) -> int:
        return min(count - 1, max(0, int((moment_ms - begin_ms) * count / max(1, end_ms - begin_ms))))

    def y_of(value: float) -> float:
        return area_top + (high - value) / (high - low) * (price_bottom - area_top)

    # The grid and the price axis.
    step = _nice_step(high - low, 7)
    tick = math.ceil(low / step) * step
    while tick <= high:
        y = y_of(tick)
        draw.line([(area_left, y), (area_right, y)], fill=GRID)
        draw.text((area_right + 6, y - 7), _price(tick, decimals, step), fill=AXIS, font=small)
        tick += step
    _time_axis(draw, begin_ms, end_ms, tz, x_of, area_top, area_bottom, small)
    draw.rectangle([area_left, area_top, area_right, price_bottom], outline=AXIS)

    # The candles.
    body = max(0.5, width * 0.35)
    for place, column in enumerate(columns):
        if column is None:
            continue
        centre = centre_of(place)
        colour = UP if column["c"] >= column["o"] else DOWN
        draw.line([(centre, y_of(column["h"])), (centre, y_of(column["l"]))], fill=colour)
        top, bottom = sorted((y_of(column["o"]), y_of(column["c"])))
        draw.rectangle([centre - body, top, centre + body, max(bottom, top + 1)], fill=colour)

    # The plots over the prices, then in their pane.
    for name in over:
        _line(draw, columns, name, colours[name], centre_of, y_of)
    if under:
        values = [column["plots"][name] for column in present for name in under if name in column["plots"]]
        pane_low, pane_high = min(values), max(values)
        if pane_high == pane_low:
            pane_low, pane_high = pane_low - 1, pane_high + 1

        def pane_y(value: float) -> float:
            return pane_top + (pane_high - value) / (pane_high - pane_low) * (area_bottom - pane_top)

        draw.rectangle([area_left, pane_top, area_right, area_bottom], outline=AXIS)
        for value in (pane_low, pane_high):
            draw.text((area_right + 6, pane_y(value) - 7), f"{value:.4g}", fill=AXIS, font=small)
        if pane_low < 0 < pane_high:
            draw.line([(area_left, pane_y(0)), (area_right, pane_y(0))], fill=GRID)
        for name in under:
            _line(draw, columns, name, colours[name], centre_of, pane_y)

    # The alerts: an entry under or over its bar, a close at its price, anything else at the top.
    by_close = {bar[6]: bar for bar in bars}
    for number, alert in enumerate(sorted(alerts, key=lambda found: found["at"])):
        bar = by_close.get(alert["at"])
        if bar is None or not begin_ms <= bar[0] < end_ms:
            continue
        x = centre_of(column_of(bar[0]))
        action = alert.get("action")
        if action == "long":
            y = y_of(bar[3]) + 6
            draw.polygon([(x, y), (x - 5, y + 9), (x + 5, y + 9)], fill=UP)
            label_y = y + 10
        elif action == "short":
            y = y_of(bar[2]) - 6
            draw.polygon([(x, y), (x - 5, y - 9), (x + 5, y - 9)], fill=DOWN)
            label_y = y - 24
        elif action == "close-position":
            y = y_of(bar[4])
            draw.line([(x - 4, y - 4), (x + 4, y + 4)], fill=TEXT, width=2)
            draw.line([(x - 4, y + 4), (x + 4, y - 4)], fill=TEXT, width=2)
            label_y = y + 6
        else:
            y = area_top + 4
            draw.ellipse([x - 3, y, x + 3, y + 6], fill=NEUTRAL)
            label_y = y + 8
        if number < NAMED_ALERTS and alert.get("title"):
            _label(draw, (x + 4, label_y), str(alert["title"])[:14], AXIS, small)

    # A run's trades: from the asset's price at entry to its price at exit, the result beside.
    for trade in trades:
        start, finish = trade.get("in_ms"), trade.get("out_ms")
        price_in, price_out = finite(trade.get("asset_in")), finite(trade.get("asset_out"))
        if start is None or price_in is None:
            continue
        result = finite(trade.get("result_percent"))
        colour = TRADE_WIN if (result or 0) >= 0 else TRADE_LOSS
        x_in, y_in = x_of(start), y_of(price_in)
        draw.ellipse([x_in - 4, y_in - 4, x_in + 4, y_in + 4], outline=colour, width=2)
        _label(draw, (x_in - 8, y_in - 20 if trade.get("direction") == "long" else y_in + 6), f"#{trade.get('id')}",
               colour, small)
        if finish is not None and price_out is not None:
            x_out, y_out = x_of(finish), y_of(price_out)
            _dashed(draw, (x_in, y_in), (x_out, y_out), colour)
            draw.rectangle([x_out - 3, y_out - 3, x_out + 3, y_out + 3], fill=colour)
            if result is not None:
                _label(draw, (x_out + 5, y_out - 7), f"{result:+.2f}%", colour, small)

    _legend(draw, plots, colours, under, normal)
    return _png(image)


def _line(draw, columns: list, name: str, colour, x_of_place, y_of) -> None:
    """A plot through its columns, broken where it has no value: a column without a bar (a zoom's) breaks nothing."""
    points: list[tuple[float, float]] = []
    for place, column in enumerate(columns):
        if column is None:
            continue
        value = column["plots"].get(name)
        if value is None:
            if len(points) > 1:
                draw.line(points, fill=colour, width=2)
            elif len(points) == 1:
                x, y = points[0]
                draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=colour)
            points = []
            continue
        points.append((x_of_place(place), y_of(value)))
    if len(points) > 1:
        draw.line(points, fill=colour, width=2)
    elif len(points) == 1:
        x, y = points[0]
        draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=colour)


def _label(draw, at: tuple[float, float], text: str, colour, font) -> None:
    """A few words over the chart, on a white ground so that the candles under them do not hide them."""
    left, top, right, bottom = draw.textbbox(at, text, font=font)
    draw.rectangle([left - 2, top - 1, right + 2, bottom + 1], fill=BACKGROUND)
    draw.text(at, text, fill=colour, font=font)


def _dashed(draw, start: tuple[float, float], end: tuple[float, float], colour, dash: float = 6) -> None:
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    if length < 1:
        return
    steps = int(length // dash)
    for step in range(0, steps, 2):
        a, b = step / steps, min(1, (step + 1) / steps)
        draw.line([(start[0] + (end[0] - start[0]) * a, start[1] + (end[1] - start[1]) * a),
                   (start[0] + (end[0] - start[0]) * b, start[1] + (end[1] - start[1]) * b)], fill=colour, width=1)


def _time_axis(draw, begin_ms: int, end_ms: int, tz: ZoneInfo, x_of, top: float, bottom: float, font) -> None:
    """A tick every round stretch of time (an hour for a day, less for a zoom), written in the trading timezone."""
    span_minutes = (end_ms - begin_ms) / 60_000
    every = next((minutes for minutes in (5, 10, 15, 30, 60, 120, 180) if span_minutes / minutes <= 14), 240)
    local = datetime.fromtimestamp(begin_ms / 1000, timezone.utc).astimezone(tz)
    moment = local.replace(minute=0, second=0, microsecond=0)
    while moment.timestamp() * 1000 < end_ms:
        at = moment.timestamp() * 1000
        if at >= begin_ms and (moment.hour * 60 + moment.minute) % every == 0:
            x = x_of(at)
            draw.line([(x, top), (x, bottom)], fill=GRID)
            draw.text((x - 15, bottom + 6), moment.strftime("%H:%M"), fill=AXIS, font=font)
        moment = (moment + timedelta(minutes=5)).astimezone(tz)


def _legend(draw, plots: dict, colours: dict, under: list[str], font) -> None:
    """The plots' names in their colours, and how the marks read, under the title (drawn: the font has no arrows)."""
    x, y = LEFT, 30
    for name in plots:
        colour = colours[name]
        draw.line([(x, y + 8), (x + 16, y + 8)], fill=colour, width=3)
        said = f"{name[:28]}{' (pane)' if name in under else ''}"
        draw.text((x + 20, y), said, fill=TEXT, font=font)
        x += 28 + int(draw.textlength(said, font=font))
    marks = (("long", lambda left: draw.polygon([(left + 5, y + 2), (left, y + 12), (left + 10, y + 12)], fill=UP)),
             ("short", lambda left: draw.polygon([(left + 5, y + 12), (left, y + 2), (left + 10, y + 2)], fill=DOWN)),
             ("close", lambda left: (draw.line([(left, y + 3), (left + 8, y + 11)], fill=TEXT, width=2),
                                     draw.line([(left, y + 11), (left + 8, y + 3)], fill=TEXT, width=2))),
             ("a trade, in to out", lambda left: (draw.ellipse([left, y + 3, left + 8, y + 11], outline=TRADE_WIN, width=2),
                                                  draw.rectangle([left + 14, y + 4, left + 20, y + 10], fill=TRADE_WIN))))
    widths = [(24 if words.startswith("a trade") else 14) + int(draw.textlength(words, font=font)) + 14 for words, _ in marks]
    left = max(x + 12, WIDTH - RIGHT - sum(widths))
    for (words, mark), width in zip(marks, widths):
        mark(left)
        shift = 24 if words.startswith("a trade") else 14
        draw.text((left + shift, y), words, fill=AXIS, font=font)
        left += width


def _price(value: float, decimals: int | None, step: float) -> str:
    shown = max(0, min(decimals if decimals is not None else 6, -math.floor(math.log10(step)) if step < 1 else 0))
    return f"{value:,.{shown}f}"


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.quantize(colors=64, method=Image.Quantize.MEDIANCUT).save(out, format="PNG", optimize=True)
    return out.getvalue()
