"""
Data: the assets WATA has prices of, what was recorded day by day, bars, the
signals it received, its real trades and what its orders cost. Read only,
through the admin's named queries; the lock box's days are refused.
"""

from datetime import date

from starlette.concurrency import run_in_threadpool

from src.admin import asset_bars, backtest_requests
from src.admin.data import capital_asset, saxo_asset
from src.admin.mcp_server.budget import Refused, research_tool
from src.admin.mcp_server.shape import DEFAULT_PAGE, compacted, csv_text, iso_moment, page, period, refuse_lockbox
from src.admin.prices import MAX_BUCKETS

# The days a question on the signals or the trades may cover.
MAX_DATA_DAYS = 92
# Bars as CSV, for a file on the Mac: a week of ten-second bars (about 60,000 rows, 6 MB) at a call,
# under the 16 MiB Claude Code reads of an answer.
MAX_CSV_DAYS = 7
MAX_CSV_BARS = 70_000
BARS_EXPORT_CHARS = 8_000_000
# The Saxo instruments looked at at most to list them (indexes and turbos).
MAX_SAXO_ASSETS = 5000
SIGNAL_KINDS = ("received", "engine")
FORMATS = ("json", "csv")


def _asset_line(asset: dict) -> dict:
    """An asset in a few fields: what a tool is told of it, and its ``ref`` to name it with."""
    line = {
        "ref": asset["ref"], "name": asset.get("description") or asset.get("symbol"), "role": asset.get("role"),
        "indice": asset.get("indice"), "first": iso_moment(asset.get("first_t")), "last": iso_moment(asset.get("last_t")),
        # What R<ticks> is counted in: 0.1 on Capital.com's US100 (R60 is 6 points), 0.001 on Dukascopy's.
        "tick": asset.get("tick"),
    }
    if asset.get("role") == "turbo":
        line.update(direction=asset.get("direction"), barrier=asset.get("barrier"), issuer=asset.get("issuer"))
    if asset.get("days") is not None:
        line["days"] = asset["days"]
    return line


def _format(value: str) -> str:
    if value not in FORMATS:
        raise Refused("format is json or csv")
    return value


def register(server, research) -> None:
    calls = research.calls

    @research_tool(server, calls, name="data_assets", title="Assets with prices", read_only=True)
    async def data_assets(provider: str = "", offset: int = 0, limit: int = DEFAULT_PAGE) -> dict:
        """The assets WATA has prices of, each named by its ``ref`` (provider/key) for the other tools: Capital.com's
        indexes (the live feed the TA engine reads), Dukascopy's index history (long backtests), and the Saxo index
        and turbos the price recorder kept. provider="" lists the indexes and counts the turbos; provider="saxo"
        lists the turbos too, the last quoted first, a page at a time."""
        if provider not in ("", "saxo", "capital", "dukascopy"):
            raise Refused('provider is "", saxo, capital or dukascopy')
        items, turbos = [], 0
        if provider in ("", "capital"):
            items += [_asset_line(capital_asset(row)) for row in await research.queries.capital_assets()]
        if provider in ("", "dukascopy") and research.history is not None:
            history = research.history
            found = await run_in_threadpool(lambda: [history.asset(label) for label in history.labels()])
            items += [_asset_line(asset) for asset in found if asset is not None]
        if provider in ("", "saxo"):
            for row in await research.queries.price_assets(MAX_SAXO_ASSETS):
                if row.get("role") == "turbo" and provider == "":
                    turbos += 1
                    continue
                items.append(_asset_line(saxo_asset(row)))
        answer = page(items, offset, limit)
        if provider == "":
            answer["saxo_turbos"] = turbos
        return compacted(answer)

    @research_tool(server, calls, name="data_coverage", title="What was recorded, day by day", read_only=True)
    async def data_coverage(start_day: date, end_day: date, indice: str = "us100", asset: str = "",
                            source: str = "pine") -> dict:
        """Day by day, what a backtest of the period would have: the Saxo turbos the recorder kept (long and short
        counts, first and last bar), the minutes it listened, the bars of ``asset`` (a ref of data_assets) a script
        would run on, and with source="journal" the entry signals WATA received. Read it before asking for a run:
        a day with no recorded turbo cannot be traded."""
        if source not in ("pine", "journal"):
            raise Refused("source is pine or journal")
        found = await backtest_requests.coverage(
            research.queries, research.history, research.timezone, research.service().max_period_days,
            start_day, end_day, indice=indice, asset=asset or None, source=source,
        )
        return compacted(found)

    @research_tool(server, calls, name="data_bars", title="Bars of an asset", read_only=True, export=True,
                   export_chars=BARS_EXPORT_CHARS)
    async def data_bars(asset: str, start_day: date, end_day: date | None = None, interval_seconds: int = 60,
                        format: str = "json") -> dict | str:
        """Bars of ``asset`` (a ref of data_assets) over whole days of the trading timezone, of any length from 10 s
        to a day (interval_seconds): t (bucket start, epoch s), bid and ask open/high/low/close, updates (quote
        changes, what stands for volume). As JSON, 12,000 bars at most. format="csv" gives a table Claude Code saves
        to a file on the Mac, for analysis there: up to 7 days a call, a week of ten-second bars (70,000 at most)."""
        _format(format)
        first, last = period(start_day, end_day, MAX_CSV_DAYS if format == "csv" else MAX_DATA_DAYS)
        refuse_lockbox(research.journal, first, last)
        provider, _, key = asset.partition("/")
        found = await asset_bars.bars(
            research.queries, research.history, research.timezone, provider, key, first, last,
            interval_seconds, today=research.today(), limit=MAX_CSV_BARS if format == "csv" else MAX_BUCKETS,
        )
        if format == "csv":
            return csv_text(found["columns"], found["bars"])
        return compacted({
            "asset": _asset_line(found["instrument"]), "from": found["from"], "to": found["to"],
            "interval_seconds": found["interval"], "columns": found["columns"], "bars": found["bars"],
            "count": len(found["bars"]),
        })

    @research_tool(server, calls, name="data_signals", title="Signals WATA received or raised", read_only=True,
                   export=True)
    async def data_signals(start_day: date, end_day: date | None = None, kind: str = "received", rule_id: str = "",
                           offset: int = 0, limit: int = DEFAULT_PAGE, format: str = "json") -> dict | str:
        """kind="received": every signal the trader dealt with (signal_journal: TradingView's and WATA's own), with
        its outcome and reason, oldest first. kind="engine": what the TA engine's rules raised (ta_signal), live or
        shadow, newest first, with what the trader made of the live ones (rule_id narrows it to a rule)."""
        _format(format)
        if kind not in SIGNAL_KINDS:
            raise Refused(f"kind is one of {', '.join(SIGNAL_KINDS)}")
        first, last = period(start_day, end_day, MAX_DATA_DAYS)
        refuse_lockbox(research.journal, first, last)
        if kind == "received":
            rows = await research.queries.journal_between(first, last)
        else:
            rows = await research.queries.ta_signals(start=first, end=last, rule_id=rule_id or None, limit=5000)
        rows = compacted(rows)
        if format == "csv":
            columns = sorted({key for row in rows for key in row if not isinstance(row[key], dict)})
            return csv_text(columns, ([row.get(column) for column in columns] for row in rows))
        return page(rows, offset, limit)

    @research_tool(server, calls, name="data_trades", title="WATA's real trades", read_only=True, export=True)
    async def data_trades(start_day: date, end_day: date | None = None, offset: int = 0, limit: int = DEFAULT_PAGE,
                          format: str = "json") -> dict | str:
        """The positions WATA really opened and closed in a period (by their opening day; end_day is start_day when
        not given), oldest first: times, turbo, prices, result in %, best and worst results reached (the worst
        recorded since v0.48.0, 0 for a position that never fell under its open), close reason. No account detail.
        With a count of wins and the reasons positions were closed for."""
        _format(format)
        first, last = period(start_day, end_day, MAX_DATA_DAYS)
        refuse_lockbox(research.journal, first, last)
        rows = compacted(await research.queries.closed_positions(first, last))
        if format == "csv":
            columns = list(rows[0]) if rows else ["position_id"]
            return csv_text(columns, ([row.get(column) for column in columns] for row in rows))
        reasons: dict[str, int] = {}
        for row in rows:
            reasons[row.get("position_close_reason") or "unknown"] = reasons.get(row.get("position_close_reason") or "unknown", 0) + 1
        results = [row["position_total_performance_percent"] for row in rows
                   if row.get("position_total_performance_percent") is not None]
        return {
            "summary": compacted({
                "trades": len(rows), "wins": sum(1 for value in results if value > 0),
                "sum_percent": sum(results), "close_reasons": reasons,
            }),
            **page(rows, offset, limit),
        }

    @research_tool(server, calls, name="data_execution", title="What WATA's orders took and cost", read_only=True)
    async def data_execution(start_day: date | None = None, end_day: date | None = None) -> dict:
        """Measured on WATA's own orders (the last three months by default): delays from a signal to the trader's
        decision and from an order to Saxo's acceptance, the price paid against the recorded quote (slippage), the
        refusals by hour, and the values a backtest would take from them (signal_delay_seconds, order_seconds,
        slippage), suggested only from 10 measures."""
        return compacted(await backtest_requests.execution(research.queries, research.timezone, start_day, end_day))
