import asyncio
import sys
import warnings

import pytest

from src import event_loop


async def _loop_in_use():
    return type(asyncio.get_running_loop()).__module__


def test_a_service_runs_on_uvloop_when_it_is_installed():
    pytest.importorskip("uvloop")
    seen = []

    async def main():
        seen.append(await _loop_in_use())

    event_loop.run(main())

    assert seen and seen[0].startswith("uvloop")


def test_a_service_still_runs_without_uvloop(monkeypatch):
    # What ``import uvloop`` does where it is not installed.
    monkeypatch.setitem(sys.modules, "uvloop", None)
    seen = []

    async def main():
        seen.append(await _loop_in_use())

    event_loop.run(main())

    # The loop any ``asyncio.run`` gets in this process.
    assert seen == [asyncio.run(_loop_in_use())]


def test_running_a_service_uses_nothing_python_is_about_to_remove():
    async def main():
        await asyncio.sleep(0)

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        event_loop.run(main())


def test_the_loop_is_not_made_the_default_of_the_process():
    async def main():
        await asyncio.sleep(0)

    before = asyncio.run(_loop_in_use())
    event_loop.run(main())

    # Another run, started the ordinary way, gets what it got before.
    assert asyncio.run(_loop_in_use()) == before


def test_an_error_in_the_service_reaches_whoever_started_it():
    async def main():
        raise RuntimeError("the service failed")

    with pytest.raises(RuntimeError, match="the service failed"):
        event_loop.run(main())


@pytest.mark.parametrize("service", ["trader", "position_monitor", "price_recorder", "watchlist_manager"])
def test_no_service_installs_uvloop_as_a_policy(service):
    with open(f"src/{service}/__init__.py", encoding="utf-8") as source:
        code = source.read()

    assert "uvloop.install" not in code
    assert "set_event_loop_policy" not in code
