"""Tables and figures as text, for a terminal and for whoever reads it next."""


def cell(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def percent(value, signed: bool = True) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%" if signed else f"{value:.1f}%"


def money(value) -> str:
    return "-" if value is None else f"€{value:,.2f}"


def table(columns: list[str], rows: list[list], align_right: set[int] | None = None) -> str:
    texts = [[cell(value) for value in row] for row in rows]
    widths = [max([len(column), *(len(row[index]) for row in texts)]) for index, column in enumerate(columns)]
    right = align_right or set()

    def line(values: list[str]) -> str:
        return "  ".join(value.rjust(widths[index]) if index in right else value.ljust(widths[index]) for index, value in enumerate(values)).rstrip()

    return "\n".join([line(columns), *(line(row) for row in texts)])
