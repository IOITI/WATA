"""
Strategies computed by WATA itself, and their backtest.

A strategy only sees ten-second bars of the index, in order, and answers with
the position it wants (flat, long or short). The same code is replayed on
history by the backtester (``python -m src.strategy.backtest``).

Everything here is outside the trading path: no trading service imports it.
"""
