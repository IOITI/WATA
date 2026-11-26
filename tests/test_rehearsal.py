"""
A signal's way rehearsed between signals (src/trade/rehearsal.py): the steps
run in turn and quietly, what one raises passed over, stopped once a signal is
in hand, and measured.
"""

import asyncio
import logging

from src.trade import rehearsal
from src.trade.rehearsal import Rehearsal, quiet_while_rehearsing, rehearse


class Kept(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _listening():
    handler = Kept()
    root = logging.getLogger()
    root.addHandler(handler)
    return handler, root


def test_each_step_is_run_in_turn_whether_it_awaits_or_not_and_whatever_one_raises():
    ran = []

    async def read():
        await asyncio.sleep(0)
        ran.append("read")

    def refuse():
        ran.append("refuse")
        raise ValueError("Breaking trading rule: the day's target is reached")

    took = asyncio.run(rehearse([("a check", lambda: ran.append("check")), ("a refusal", refuse), ("a read", read)]))

    assert ran == ["check", "refuse", "read"]
    assert took.steps == 3 and took.seconds >= 0 and took.slowest[0] in ("a check", "a refusal", "a read")


def test_what_a_step_logs_is_left_out_and_what_is_logged_elsewhere_meanwhile_is_not():
    handler, root = _listening()
    level = root.level
    root.setLevel(logging.INFO)
    try:
        quiet_while_rehearsing()
        quiet_while_rehearsing()
        assert sum(isinstance(known, rehearsal._Quiet) for known in handler.filters) == 1

        async def scenario():
            waiting = asyncio.Event()

            async def other_task():
                # A signal handled meanwhile, in its own task: its lines are kept.
                await waiting.wait()
                logging.info("Dispatching action=long")

            other = asyncio.ensure_future(other_task())

            async def step():
                logging.info("Breaking trading rule : said every thirty seconds")
                logging.getLogger("src.trade.async_services").info("Position scale: tod=1.00 × conf=1.00 = 1.00")
                logging.getLogger("src.trade.watchlist_client").info("Ignoring stale watchlist entry")
                waiting.set()
                await asyncio.sleep(0.01)
                logging.error("Signal is outside of market hours.")
                # Not the rehearsed code's: a fault of the database or of a connection is still said.
                logging.getLogger("asyncio").error("Fatal error on transport")
                logging.getLogger("src.database.postgres").error("The database refused a read")

            await rehearse([("a check", step)])
            await other
            logging.info("after the rehearsal")

        asyncio.run(scenario())
    finally:
        root.removeHandler(handler)
        root.setLevel(level)

    assert handler.lines == [
        "Dispatching action=long", "Fatal error on transport", "The database refused a read", "after the rehearsal",
    ]


def test_a_rehearsal_stops_between_two_steps_once_a_signal_is_in_hand():
    ran = []
    in_hand = {"busy": False}

    def first():
        ran.append("first")
        in_hand["busy"] = True

    took = asyncio.run(rehearse([("first", first), ("second", lambda: ran.append("second"))], lambda: in_hand["busy"]))

    assert ran == ["first"] and took.steps == 1


def test_a_step_cancelled_is_not_passed_over():
    async def cancelled():
        raise asyncio.CancelledError

    async def scenario():
        try:
            await rehearse([("a read", cancelled)])
        except asyncio.CancelledError:
            return "cancelled"

    assert asyncio.run(scenario()) == "cancelled"
    # And the quiet goes with it: a line logged afterwards is kept.
    assert rehearsal._rehearsing.get() is False


def test_a_slow_rehearsal_says_what_it_took_and_which_step_was_slowest():
    quick = Rehearsal(steps=16, seconds=0.012, cpu_seconds=0.004, major_faults=0, slowest=("the pause", 0.003))
    slow = Rehearsal(steps=16, seconds=0.132, cpu_seconds=0.011, major_faults=3, slowest=("the long turbo of us100", 0.095))

    assert not quick.slow and slow.slow
    assert slow.said() == (
        "132 ms for 16 step(s) (processor 11 ms, 3 page(s) read from the disk; slowest: the long turbo of us100, 95 ms)"
    )


def test_a_rehearsal_measures_the_time_its_steps_waited():
    async def wait():
        await asyncio.sleep(0.06)

    took = asyncio.run(rehearse([("a wait", wait)]))

    assert took.slow and took.seconds >= 0.06 and took.slowest == ("a wait", took.slowest[1])
    # Waiting is not work: the processor's time is far under the clock's.
    assert took.cpu_seconds < took.seconds
