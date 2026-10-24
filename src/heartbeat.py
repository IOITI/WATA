"""
What the trader and the position monitor say of themselves every fifteen
seconds (``service_heartbeat``), for the admin's health: alive, and what they
see. The figures are read from memory and written in the background: a slow or
failing database only makes them old, and never stands in a trade's way.
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 15
HEARTBEAT_DB_TIMEOUT_SECONDS = 5.0
# Past this without a beat, a service is taken for stopped (the admin's health).
HEARTBEAT_STALE_SECONDS = 90


async def beat_periodically(manager, service: str, version: str, figures, interval_seconds=HEARTBEAT_INTERVAL_SECONDS):
    """Write ``figures()`` every ``interval_seconds`` until cancelled; never raises."""
    started_at = datetime.now(timezone.utc)
    while True:
        try:
            await asyncio.wait_for(manager.beat(service, started_at, version, figures()), HEARTBEAT_DB_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Could not write the heartbeat of %s: %r", service, e)
        await asyncio.sleep(interval_seconds)


def moment(epoch: float | None) -> str | None:
    """An epoch time as the figures keep it."""
    return None if epoch is None else datetime.fromtimestamp(epoch, timezone.utc).isoformat()
