"""Claude's Pine studies as child processes of the admin (src/admin/studies.py), with a stand-in command."""

import asyncio
import json
import os

import pytest

from src.admin.errors import NotPossible
from src.admin.studies import StudyService, cgroup_memory


class Process:
    def __init__(self, ends=True):
        self.returncode = None
        self.killed = False
        self._ended = asyncio.Event()
        if ends:
            self.end(0)

    def end(self, code):
        self.returncode = code
        self._ended.set()

    async def wait(self):
        await self._ended.wait()
        return self.returncode

    def kill(self):
        self.killed = True
        self.end(-9)


class Launcher:
    """Stands for the study's command: answers what it is told, or never ends."""

    def __init__(self, answer=None, ends=True, delay=0.0):
        self.answer = answer if answer is not None else {"ok": True}
        self.ends = ends
        self.delay = delay
        self.commands = []
        self.processes = []

    async def __call__(self, arguments, log_path):
        self.commands.append(arguments)
        with open(log_path, "a") as log:
            log.write("studying\n")
        if self.delay:
            await asyncio.sleep(self.delay)
        out = arguments[arguments.index("--out") + 1]
        if self.answer is not False:
            with open(out, "w") as out_file:
                json.dump(self.answer, out_file)
        process = Process(ends=self.ends)
        self.processes.append(process)
        return process


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def service(tmp_path, launcher, **options) -> StudyService:
    return StudyService(
        root=str(tmp_path / "_studies"), ta_root=str(tmp_path / "ta"), prices_root=str(tmp_path / "prices"),
        signal_cache=str(tmp_path / "_signal_cache"), memory_mb=384, timeout_seconds=options.pop("timeout_seconds", 90),
        minutes_per_hour=options.pop("minutes_per_hour", 20), launcher=launcher,
        memory=options.pop("memory", lambda: None), **options,
    )


def test_a_study_is_the_command_with_its_limits_and_leaves_nothing_behind(tmp_path):
    launcher = Launcher({"ok": True, "plots": ["Mid"]})
    studies = service(tmp_path, launcher)

    found = asyncio.run(studies.study("run", {"asset": "capital/US100"}))

    assert found == {"ok": True, "plots": ["Mid"]}
    command = launcher.commands[0]
    assert command[:2] == ["src.research.study", "run"]
    assert command[command.index("--memory-mb") + 1] == "384" and command[command.index("--timeout") + 1] == "90"
    assert command[command.index("--signal-cache") + 1].endswith("_signal_cache")
    assert command[command.index("--data") + 1] == "database"
    assert os.listdir(tmp_path / "_studies") == []


def test_a_study_reads_recorded_day_files_when_told_to(tmp_path):
    launcher = Launcher()
    asyncio.run(service(tmp_path, launcher, data=f"files:{tmp_path}").study("run", {}))

    command = launcher.commands[0]
    assert command[command.index("--data") + 1] == f"files:{tmp_path}"


def test_one_study_at_a_time_the_next_waits_a_little_then_is_refused(tmp_path):
    launcher = Launcher(delay=0.3)
    studies = service(tmp_path, launcher, wait_seconds=0.05)

    async def two():
        return await asyncio.gather(studies.study("check", {}), studies.study("check", {}), return_exceptions=True)

    first, second = asyncio.run(two())

    assert first == {"ok": True}
    assert isinstance(second, NotPossible) and "Another study" in str(second)


def test_a_study_that_does_not_end_is_stopped(tmp_path):
    launcher = Launcher(ends=False)
    studies = service(tmp_path, launcher, timeout_seconds=0)
    import src.admin.studies as module

    original = module.GRACE_SECONDS
    module.GRACE_SECONDS = 0.05
    try:
        with pytest.raises(NotPossible, match="did not finish"):
            asyncio.run(studies.study("run", {}))
    finally:
        module.GRACE_SECONDS = original
    assert launcher.processes[0].killed


def test_a_study_without_an_answer_says_what_its_log_said(tmp_path):
    studies = service(tmp_path, Launcher(answer=False))

    with pytest.raises(NotPossible, match="studying"):
        asyncio.run(studies.study("check", {}))


def test_no_study_starts_when_the_container_is_short_of_memory(tmp_path):
    megabyte = 1024 ** 2
    studies = service(tmp_path, Launcher(), memory=lambda: (900 * megabyte, 1024 * megabyte))

    with pytest.raises(NotPossible, match="MB of memory free"):
        asyncio.run(studies.study("check", {}))


def test_the_hours_minutes_of_studies_are_kept(tmp_path):
    clock = Clock()
    launcher = Launcher()

    async def slow(arguments, log_path):
        clock.now += 61
        return await Launcher()(arguments, log_path)

    studies = service(tmp_path, slow, minutes_per_hour=1, clock=clock)
    asyncio.run(studies.study("check", {}))

    with pytest.raises(NotPossible, match="last hour"):
        asyncio.run(studies.study("check", {}))
    clock.now += 3600
    assert asyncio.run(studies.study("check", {})) == {"ok": True}


def test_a_study_whose_call_is_given_up_is_stopped(tmp_path):
    launcher = Launcher(ends=False)
    studies = service(tmp_path, launcher)

    async def given_up():
        task = asyncio.create_task(studies.study("run", {}))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(given_up())

    assert launcher.processes[0].killed


@pytest.mark.parametrize("files,expected", [
    ({"memory.max": "1073741824\n", "memory.current": "536870912\n"}, (536870912, 1073741824)),
    ({"memory.max": "max\n", "memory.current": "1"}, None),
    ({}, None),
])
def test_the_containers_memory_is_read_where_it_has_a_limit(tmp_path, files, expected):
    for name, content in files.items():
        (tmp_path / name).write_text(content)

    assert cgroup_memory(str(tmp_path)) == expected


def test_a_study_that_dies_says_how_and_the_memory_then(tmp_path):
    class Killed(Launcher):
        async def __call__(self, arguments, log_path):
            await super().__call__(arguments, log_path)
            process = Process(ends=False)
            process.end(-9)
            return process

    megabytes = 1024 ** 2
    memory = iter([(500 * megabytes, 1536 * megabytes), (1450 * megabytes, 1536 * megabytes)])
    kills = iter([3, 4])
    studies = service(tmp_path, Killed(answer=False), memory=lambda: next(memory), kills=lambda: next(kills))

    with pytest.raises(NotPossible) as refused:
        asyncio.run(studies.study("signals", {"asset": "capital/US100"}))

    said = str(refused.value)
    assert said.startswith("The study gave no answer: it was stopped by the kernel (SIGKILL), most likely for memory")
    assert "the admin used 1,450 of its 1,536 MB then" in said and "the kernel's OOM killer stopped 1 process(es)" in said
    assert said.endswith("(studying)")
