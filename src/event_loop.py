"""How a service runs its main coroutine."""

import asyncio


def run(main) -> None:
    """
    Run ``main`` (a coroutine) to its end, on uvloop when it is installed.

    uvloop is the loop of this run alone. Making it the default of the whole
    process (``uvloop.install()``) goes through asyncio's policies, which
    Python 3.14 deprecates and 3.16 removes.
    """
    try:
        import uvloop
    except ImportError:
        asyncio.run(main)
        return
    uvloop.run(main)
