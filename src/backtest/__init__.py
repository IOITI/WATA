"""
Backtests on what WATA recorded: the signals of a Pine script of the
Technical Analysis pages, or those WATA really received, traded as the
trader and the position monitor trade them, on the Saxo turbos the price
recorder kept, at their recorded bid and ask.

- ``rules``: the trader's checks on an entry, its sizing and the position
  monitor's exits, as pure functions of a clock that is not the present
  (tests/test_backtest_parity.py holds them to the live code).
- ``selection``: the turbo the trader would have bought, chosen by the live
  helpers themselves among the recorded ones.
- ``simulator``: a trading day, signal after signal, ten seconds at a time.
- ``data``: the recorded days, from PostgreSQL or from files.
- ``signals``: what a run trades on: a Pine script replayed as the TA engine
  runs it, the signal journal, the end-of-day close.
- ``run``: the command the admin starts, one run at a time, as a child process.

Outside the trading path: no trading service imports it.
"""
