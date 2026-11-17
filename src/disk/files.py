"""Where the samples of the disk are kept, and reading them back: the collector writes, the admin and the scheduler read."""

import json

from src.disk.trend import moment

SAMPLES = "samples.jsonl"
LATEST = "latest.json"


def read_samples(path: str) -> list[dict]:
    """The samples kept, oldest first; a line cut short by a stop is skipped."""
    found = []
    try:
        with open(path, encoding="utf-8") as lines:
            for line in lines:
                try:
                    sample = json.loads(line)
                except ValueError:
                    continue
                if isinstance(sample, dict) and moment(sample.get("at")):
                    found.append(sample)
    except OSError:
        pass
    found.sort(key=lambda sample: moment(sample["at"]))
    return found


def read_latest(path: str) -> dict | None:
    """The newest sample with its trend; None when there is none or it cannot be read."""
    try:
        with open(path, encoding="utf-8") as file:
            latest = json.load(file)
    except (OSError, ValueError):
        return None
    return latest if isinstance(latest, dict) else None
