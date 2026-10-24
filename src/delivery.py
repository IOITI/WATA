"""
A message that must reach another service, tried again a few times before it
is given up: the scheduler's close of the day, which the web server may not
take at the very moment it is sent (restarting, slow to answer).
"""

import time

# The waits between attempts: three attempts in all.
RETRY_WAITS_SECONDS = (2.0, 4.0)


def deliver(send, waits=RETRY_WAITS_SECONDS, sleep=time.sleep) -> Exception | None:
    """Call ``send()`` until it returns, once more after each of ``waits``; the last error, None once delivered."""
    error = None
    for wait in (None, *waits):
        if wait is not None:
            sleep(wait)
        try:
            send()
            return None
        except Exception as e:
            error = e
    return error
