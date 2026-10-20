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
