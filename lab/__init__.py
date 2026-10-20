"""
WATA's research lab: what runs on a research machine, never on the server.

The server writes each finished day as files (src/admin/research_export.py);
``lab/sync.sh`` pulls them, ``python -m lab db load`` puts them in a
PostgreSQL of the lab's own, and the backtests and the admin pages run here on
the same tables, through the same code, as they would there.

    python -m lab --help

This package is outside ``src/``: ``package.sh`` never ships it.
"""
