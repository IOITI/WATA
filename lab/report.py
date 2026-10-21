"""
An experiment as a page to read: what was tried and why, each run's figures
side by side, their days one applied on the other, and what was concluded.
Written as the content of an Artifact page (no document skeleton: the
Artifact tool wraps it), self-contained, readable in both themes and at a
phone's width.

    python -m lab exp report <id> [--out file.html]
"""

import html
import math
from pathlib import Path

from lab import experiments, paths, runs

LINE_TOKENS = ("--line-1", "--line-2", "--line-3", "--line-4", "--line-5", "--line-6")

STYLE = """
<style>
/* A lab notebook: one column of record, figures in a ruled table, curves drawn to scale. */
:root {
  --paper: #f5f7f9;
  --sheet: #ffffff;
  --ink: #18212d;
  --muted: #5b6676;
  --rule: #d9dfe7;
  --accent: #0d6a86;
  --gain: #1d7a4b;
  --loss: #b02a22;
  --line-1: #2563eb;
  --line-2: #d9590b;
  --line-3: #0f8f7f;
  --line-4: #c2399b;
  --line-5: #6b7d1f;
  --line-6: #7151c9;
  --display: "IBM Plex Sans Condensed", "Arial Narrow", "Helvetica Neue", Arial, sans-serif;
  --body: "IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #0f141b; --sheet: #151c25; --ink: #e5eaf0; --muted: #9aa6b5; --rule: #2a3542; --accent: #4fb3d1;
    --gain: #4cc38a; --loss: #f07167; --line-1: #6c9cff; --line-2: #f08a4b; --line-3: #3cc4b0; --line-4: #e46ac3;
    --line-5: #a8bd48; --line-6: #a58cf5; color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --paper: #0f141b; --sheet: #151c25; --ink: #e5eaf0; --muted: #9aa6b5; --rule: #2a3542; --accent: #4fb3d1;
  --gain: #4cc38a; --loss: #f07167; --line-1: #6c9cff; --line-2: #f08a4b; --line-3: #3cc4b0; --line-4: #e46ac3;
  --line-5: #a8bd48; --line-6: #a58cf5; color-scheme: dark;
}
body {
  background: var(--paper); color: var(--ink); font: 15px/1.55 var(--body);
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  padding-block: 28px 48px;
}
main { max-width: 980px; margin: 0 auto; display: flex; flex-direction: column; gap: 28px; }
header { display: flex; flex-direction: column; gap: 10px; }
.eyebrow { font: 600 12px/1.2 var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
h1 { font: 600 clamp(26px, 4.6vw, 38px)/1.1 var(--display); margin: 0; text-wrap: balance; letter-spacing: -.01em; }
h2 { font: 600 19px/1.25 var(--display); margin: 0; letter-spacing: .01em; }
p { margin: 0; max-width: 68ch; }
.meta { display: flex; flex-wrap: wrap; gap: 8px 18px; color: var(--muted); font-size: 13px; }
.verdict { display: inline-flex; align-items: center; gap: 6px; font: 600 12px/1 var(--mono); text-transform: uppercase;
  letter-spacing: .06em; padding: 5px 9px; border-radius: 999px; border: 1px solid var(--rule); background: var(--sheet); }
.verdict.kept { color: var(--gain); border-color: var(--gain); }
.verdict.rejected { color: var(--loss); border-color: var(--loss); }
.verdict.inconclusive, .verdict.open { color: var(--muted); }
section { display: flex; flex-direction: column; gap: 12px; }
.hypothesis { font-size: 17px; line-height: 1.5; border-left: 3px solid var(--accent); padding-left: 14px; }
.scroll { overflow-x: auto; min-width: 0; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; background: var(--sheet); }
th, td { padding: 9px 12px; text-align: right; border-bottom: 1px solid var(--rule); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; white-space: normal; min-width: 180px; }
thead th { font: 600 11.5px/1.2 var(--mono); letter-spacing: .05em; text-transform: uppercase; color: var(--muted); }
td { font-variant-numeric: tabular-nums; }
td.gain { color: var(--gain); } td.loss { color: var(--loss); }
.swatch { display: inline-block; width: 14px; height: 3px; border-radius: 2px; vertical-align: middle; margin-right: 8px; }
.run-name { font-weight: 600; }
.run-id { display: block; font: 12px/1.4 var(--mono); color: var(--muted); }
figure { margin: 0; background: var(--sheet); border: 1px solid var(--rule); border-radius: 6px; padding: 12px; overflow-x: auto; }
/* Readable labels on a phone: the chart keeps its width there and scrolls in its frame. */
figure svg { width: 100%; min-width: 520px; max-width: 760px; height: auto; display: block; }
figcaption { color: var(--muted); font-size: 13px; padding-top: 8px; }
.axis { font: 12px var(--mono); fill: var(--muted); }
.grid { stroke: var(--rule); stroke-width: 1; }
.zero { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 3 3; }
.notes { white-space: pre-wrap; }
.empty { color: var(--muted); }
</style>
"""


def _escape(value) -> str:
    return html.escape("" if value is None else str(value))


def _percent(value) -> str:
    return "–" if value is None else f"{value:+.2f}%"


def _signed_class(value) -> str:
    if value is None or value == 0:
        return ""
    return "gain" if value > 0 else "loss"


def _elsewhere(experiment: dict) -> str:
    """How many of an experiment's trials were made outside its runs, when some were."""
    count = sum(entry["trials"] for entry in experiment.get("elsewhere") or [])
    return f" ({count} of them elsewhere)" if count else ""


def _nice_step(span: float, ticks: int = 5) -> float:
    raw = span / max(ticks, 1)
    magnitude = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for multiple in (1, 2, 2.5, 5, 10):
        if raw <= multiple * magnitude:
            return multiple * magnitude
    return 10 * magnitude


def curves_svg(curves: list[tuple[int, list[tuple[str, float]]]]) -> str:
    """The runs' cumulative results by day, on one scale; each in the colour of its row of the table."""
    days = sorted({day for _, points in curves for day, _ in points})
    values = [value for _, points in curves for _, value in points] + [0.0]
    if len(days) < 2:
        return '<p class="empty">Too few days to draw a curve.</p>'
    low, high = min(values), max(values)
    step = _nice_step(high - low or 1.0)
    low, high = math.floor(low / step) * step, math.ceil(high / step) * step
    if high == low:
        high = low + step
    width, height, left, right, top, bottom = 600, 280, 56, 14, 14, 34
    plot_w, plot_h = width - left - right, height - top - bottom

    def x(index: int) -> float:
        return left + plot_w * index / (len(days) - 1)

    def y(value: float) -> float:
        return top + plot_h * (high - value) / (high - low)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Cumulative result of each run, day by day">']
    tick = low
    while tick <= high + step / 2:
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y(tick):.1f}" y2="{y(tick):.1f}"/>')
        parts.append(f'<text class="axis" x="{left - 8}" y="{y(tick) + 4:.1f}" text-anchor="end">{tick:+g}%</text>')
        tick += step
    if low < 0 < high:
        parts.append(f'<line class="zero" x1="{left}" x2="{width - right}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>')
    labelled = sorted({0, len(days) - 1, *range(0, len(days), max(1, len(days) // 6))})
    for index in labelled:
        anchor = "start" if index == 0 else "end" if index == len(days) - 1 else "middle"
        parts.append(f'<text class="axis" x="{x(index):.1f}" y="{height - 10}" text-anchor="{anchor}">{days[index][5:]}</text>')
    place = {day: index for index, day in enumerate(days)}
    for number, points in curves:
        token = LINE_TOKENS[number % len(LINE_TOKENS)]
        path = " ".join(f"{'M' if order == 0 else 'L'}{x(place[day]):.1f},{y(value):.1f}" for order, (day, value) in enumerate(points))
        parts.append(f'<path d="{path}" fill="none" stroke="var({token})" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>')
        if points:
            day, value = points[-1]
            parts.append(f'<circle cx="{x(place[day]):.1f}" cy="{y(value):.1f}" r="3.5" fill="var({token})"/>')
    parts.append("</svg>")
    return "".join(parts)


def render(experiment: dict, found_runs: list[dict]) -> str:
    verdict = experiment["verdict"] or "open"
    title = f"{experiment['title']}"
    rows, curves = [], []
    for number, run in enumerate(found_runs):
        data = run.get("summary") or {}
        figures, money, random = data.get("figures") or {}, data.get("money") or {}, data.get("against_random") or {}
        token = LINE_TOKENS[number % len(LINE_TOKENS)]
        changed = ", ".join(f"{name} {value}" for name, value in ((run.get("settings") or {}).get("changed") or {}).items())
        cells = [
            f'<td><span class="swatch" style="background: var({token})"></span><span class="run-name">{_escape(run["name"])}</span>'
            f'<span class="run-id">{_escape(run["id"])} · {_escape(run["from"])} to {_escape(run["to"])}'
            f'{" · " + _escape(changed) if changed else ""}</span></td>',
            f'<td>{_escape(figures.get("trades", "–"))}</td>',
            f'<td>{"–" if figures.get("win_rate") is None else f"{figures["win_rate"]:.0f}%"}</td>',
            f'<td class="{_signed_class(figures.get("expectancy"))}">{_percent(figures.get("expectancy"))}</td>',
            f'<td class="{_signed_class(figures.get("total_percent"))}">{_percent(figures.get("total_percent"))}</td>',
            f'<td class="{_signed_class(figures.get("worst_drawdown"))}">{_percent(figures.get("worst_drawdown"))}</td>',
            f'<td class="{_signed_class(money.get("profit_loss"))}">{"–" if money.get("profit_loss") is None else f"€{money["profit_loss"]:,.0f}"}</td>',
            f'<td>{"–" if random.get("beaten") is None else f"{random["beaten"]:.0f}%"}</td>',
        ]
        rows.append("<tr>" + "".join(cells) + "</tr>")
        curve = [(point["day"], point["cumulative"]) for point in data.get("curve") or []]
        if curve:
            curves.append((number, curve))
    table = (
        '<div class="scroll"><table><thead><tr><th>Run</th><th>Trades</th><th>Won</th><th>Per trade</th>'
        '<th>All days</th><th>Worst drawdown</th><th>In money</th><th>Beats random</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        if rows else '<p class="empty">No run of this experiment is left in the lab.</p>'
    )
    chart = curves_svg(curves) if curves else '<p class="empty">No run has days to draw yet.</p>'
    conclusion = (
        f'<p class="notes">{_escape(experiment["notes"])}</p>' if experiment["notes"]
        else '<p class="empty">Not concluded yet.</p>'
    )
    family_trials = experiments.family_trials(experiment["family"])
    return (
        f"<title>{_escape(title)}</title>\n"
        '<meta name="claude:viewport-fit-sides" content="cover">\n'
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600'
        '&family=IBM+Plex+Sans+Condensed:wght@600&family=IBM+Plex+Sans:wght@400;600&display=swap">\n'
        f"{STYLE}\n<main>\n"
        "<header>\n"
        f'  <div class="eyebrow">WATA research lab · experiment {_escape(experiment["id"])}</div>\n'
        f"  <h1>{_escape(experiment['title'])}</h1>\n"
        f'  <div class="meta"><span class="verdict {_escape(verdict)}">{_escape(verdict)}</span>'
        f"<span>Family {_escape(experiment['family'])}</span>"
        f"<span>{experiment['trials']} configurations tried here{_elsewhere(experiment)}, {family_trials} in the family</span>"
        f"<span>Opened {_escape(experiment['opened_at'][:10])}</span></div>\n"
        "</header>\n"
        f'<section><h2>Hypothesis</h2><p class="hypothesis">{_escape(experiment["hypothesis"])}</p></section>\n'
        f"<section><h2>Runs</h2>{table}</section>\n"
        "<section><h2>All days, one applied on the other</h2>"
        f"<figure>{chart}<figcaption>Each run's result in percent, as WATA counts a day: "
        "each trade as the whole stake, one day applied on the one before.</figcaption></figure></section>\n"
        f"<section><h2>Conclusion</h2>{conclusion}</section>\n"
        "</main>\n"
    )


def write(experiment_id: str, out: Path | None = None) -> Path:
    experiment = experiments.get(experiment_id)
    reader = runs.reader()
    found = []
    for entry in experiment["runs"]:
        try:
            run = reader.get(entry["run"])
        except Exception:
            continue
        if run.get("kind") == "run":
            found.append(run)
    target = out or paths.lab_root() / "reports" / f"experiment-{experiment_id}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(experiment, found), encoding="utf-8")
    return target
