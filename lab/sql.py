"""
SQL over the research files themselves, with DuckDB: for questions over many
days of bars that need no database (the spread of the turbos by hour, the
moves of the index after a time of day...).

Each table of the research days is a view of the same name, every day's file
of it at once (the two tables of instruments: the newest day's). The days of
Dukascopy's history are ``dukascopy_bars``, with the instrument's ``label``.
"""

import json
from pathlib import Path

from lab import paths
from src.admin.research_export import TABLES


def connect(root: Path | None = None):
    import duckdb

    root = root or paths.price_history()
    days = root / "research" / "days"
    connection = duckdb.connect()
    for table in TABLES:
        files = sorted(days.glob(f"*/{table.name}.csv.gz"))
        if not files:
            continue
        source = f"'{files[-1]}'" if not table.moment else f"'{days}/*/{table.name}.csv.gz'"
        connection.execute(f"CREATE VIEW {table.name} AS SELECT * FROM read_csv({source}, header = true, union_by_name = true)")
    history = root / "dukascopy" / "bars"
    if any(history.glob("*/*.csv.gz")):
        connection.execute(
            f"CREATE VIEW dukascopy_bars AS SELECT * EXCLUDE (filename), "
            f"split_part(filename, '/', -2) AS label FROM read_csv('{history}/*/*.csv.gz', header = true, filename = true)"
        )
    return connection


def query(sql: str, *, limit: int = 50, root: Path | None = None) -> dict:
    connection = connect(root)
    try:
        cursor = connection.execute(sql)
        columns = [column[0] for column in cursor.description or []]
        rows = cursor.fetchmany(limit + 1)
    finally:
        connection.close()
    return {"columns": columns, "rows": [list(row) for row in rows[:limit]], "more": len(rows) > limit}


def as_text(result: dict) -> str:
    """A table of text: what the terminal shows."""
    columns, rows = result["columns"], [[_cell(value) for value in row] for row in result["rows"]]
    widths = [max([len(column), *(len(row[index]) for row in rows)]) for index, column in enumerate(columns)]
    lines = ["  ".join(column.ljust(width) for column, width in zip(columns, widths))]
    lines += ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)) for row in rows]
    if result["more"]:
        lines.append("… more rows (--limit to see them)")
    return "\n".join(lines)


def as_json(result: dict) -> str:
    return json.dumps(result, default=str)


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)
