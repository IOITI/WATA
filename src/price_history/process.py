"""What the commands the admin starts as child processes share: giving way to everything else."""

import os

LOWEST_PRIORITY = 19
# What the kernel kills first when the container runs out of memory: a child, never the admin
# (the operator's console, the pause of new entries included). 1000 is the most a process may ask.
OOM_SCORE_ADJ = 1000
OOM_SCORE_ADJ_PATH = "/proc/self/oom_score_adj"


def lower_priority(nice: int = LOWEST_PRIORITY) -> None:
    """Give way to everything else on the machine: neither a backtest nor a download is ever urgent."""
    try:
        os.nice(nice)
    except (AttributeError, OSError):
        pass
    give_up_memory_first()


def give_up_memory_first(path: str = OOM_SCORE_ADJ_PATH) -> None:
    """Be the first the kernel stops when memory runs out (Linux; nothing elsewhere)."""
    try:
        with open(path, "w") as score_file:
            score_file.write(str(OOM_SCORE_ADJ))
    except OSError:
        pass


CGROUP_ROOT = "/sys/fs/cgroup"


def exit_said(code: int | None) -> str:
    """What a child's end means: the signal that stopped it, or its code."""
    if code is None:
        return "it had not ended"
    if code == -9:
        return "stopped by the kernel (SIGKILL), most likely for memory: the admin's children are the first it stops"
    if code == -6:
        return "aborted (SIGABRT): Node does so when its heap is full"
    if code == -15:
        return "stopped (SIGTERM): cancelled, or the admin stopping"
    if code < 0:
        return f"stopped by signal {-code}"
    return f"ended with code {code}"


def peak_memory_mb(pid: int | None, proc: str = "/proc") -> float | None:
    """The most memory a process has held (VmHWM), in MB; None where it cannot be read (not Linux)."""
    if pid is None:
        return None
    try:
        with open(os.path.join(proc, str(pid), "status"), encoding="utf-8") as status:
            for line in status:
                if line.startswith("VmHWM:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        return None
    return None


def oom_kills(root: str = CGROUP_ROOT) -> int | None:
    """How many processes the kernel's OOM killer has stopped in this container (cgroup v2); None where unknown."""
    try:
        with open(os.path.join(root, "memory.events"), encoding="utf-8") as events:
            for line in events:
                name, _, value = line.strip().partition(" ")
                if name == "oom_kill":
                    return int(value)
    except (OSError, ValueError):
        return None
    return None
