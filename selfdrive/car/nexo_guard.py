from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterable


NEXO_STOCK_SCC_ADDRS = frozenset((0x389, 0x420, 0x421, 0x50A))
NEXO_STOCK_SCC_SOURCE = 0
NEXO_RUNTIME_GUARD_GRACE_S = 0.30
NEXO_RUNTIME_GUARD_WINDOW_S = 0.25
NEXO_RUNTIME_GUARD_MIN_FRAMES = 3


class NexoStockSccRuntimeGuard:
  """Fail closed when the factory SCC stream returns after radar takeover.

  Outgoing Panda acknowledgements use sources >=128, so source 0 is the actual
  vehicle-side stock SCC stream observed in NEXO logs. The guard is armed only
  after CarInterface.init() succeeds and is disabled entirely for stock cruise.
  """

  def __init__(self, enabled: bool, *, grace_s: float = NEXO_RUNTIME_GUARD_GRACE_S,
               window_s: float = NEXO_RUNTIME_GUARD_WINDOW_S,
               min_frames: int = NEXO_RUNTIME_GUARD_MIN_FRAMES) -> None:
    self.enabled = enabled
    self.grace_s = grace_s
    self.window_s = window_s
    self.min_frames = min_frames
    self.armed = False
    self.armed_at = 0.0
    self._timestamps: deque[float] = deque()

  def arm(self, now: float | None = None) -> None:
    self.armed = self.enabled
    self.armed_at = time.monotonic() if now is None else now
    self._timestamps.clear()

  def observe(self, can_messages: Iterable[object], now: float | None = None) -> bool:
    if not self.armed:
      return False

    timestamp = time.monotonic() if now is None else now
    if timestamp - self.armed_at < self.grace_s:
      self._timestamps.clear()
      return False

    for msg in can_messages:
      if getattr(msg, "src", -1) == NEXO_STOCK_SCC_SOURCE and          getattr(msg, "address", -1) in NEXO_STOCK_SCC_ADDRS:
        self._timestamps.append(timestamp)

    cutoff = timestamp - self.window_s
    while self._timestamps and self._timestamps[0] < cutoff:
      self._timestamps.popleft()

    return len(self._timestamps) >= self.min_frames
