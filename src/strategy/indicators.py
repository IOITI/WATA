"""
What the strategies measure on prices: a kernel regression and the average
price of the session with the spread of prices around it.

Pure logic: no clock, no I/O.
"""

import math
from collections import deque


def wave(distance: float) -> float:
    """The 'Wave' kernel of KernelFunctions.pine, for a distance already divided by the bandwidth."""
    reach = abs(distance)
    return (1 - reach) * math.cos(math.pi * distance) if reach <= 1 else 0.0


KERNELS = {"wave": wave}


def kernel_weights(bandwidth: int, style: str = "wave") -> list[float]:
    """
    The weight of each of the last ``bandwidth`` values, the newest first, as
    KernelFunctions.pine works them out: the kernel taken at the square of how
    far back the value is, over the square of the bandwidth.
    """
    if bandwidth < 1:
        raise ValueError("A kernel needs a bandwidth of 1 at least")
    try:
        kernel = KERNELS[style.lower()]
    except KeyError:
        raise ValueError(f"Unknown kernel '{style}' (known: {', '.join(sorted(KERNELS))})") from None
    return [kernel(back * back / (bandwidth * bandwidth)) for back in range(bandwidth)]


class KernelRegression:
    """Nadaraya-Watson estimate of the last value, from the last ``bandwidth`` values."""

    def __init__(self, bandwidth: int, style: str = "wave"):
        self._weights = kernel_weights(bandwidth, style)
        self._total = sum(self._weights)
        if self._total == 0:
            raise ValueError(f"The weights of kernel '{style}' at bandwidth {bandwidth} add up to nothing")
        self._values: deque[float] = deque(maxlen=bandwidth)

    def push(self, value: float) -> float | None:
        """Add a value; the estimate, or None while there are fewer values than the bandwidth."""
        self._values.appendleft(value)
        if len(self._values) < len(self._weights):
            return None
        return sum(value * weight for value, weight in zip(self._values, self._weights)) / self._total


class SessionVwap:
    """
    The weighted average of the prices since it was last reset, and their
    weighted standard deviation around it (what ``ta.vwap`` gives in Pine).
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._weight = 0.0
        self._sum = 0.0
        self._squares = 0.0
        # Prices are counted from the first one: squares of 30,000 would cost precision.
        self._origin: float | None = None

    def add(self, price: float, weight: float) -> None:
        if weight <= 0:
            return
        if self._origin is None:
            self._origin = price
        offset = price - self._origin
        self._weight += weight
        self._sum += offset * weight
        self._squares += offset * offset * weight

    @property
    def value(self) -> float | None:
        if self._weight == 0:
            return None
        return self._origin + self._sum / self._weight

    @property
    def deviation(self) -> float | None:
        if self._weight == 0:
            return None
        mean = self._sum / self._weight
        return math.sqrt(max(self._squares / self._weight - mean * mean, 0.0))
