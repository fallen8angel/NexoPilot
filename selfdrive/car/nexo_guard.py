from __future__ import annotations

import time
from collections import Counter, deque
from collections.abc import Iterable


NEXO_STOCK_SCC_ADDRS = frozenset((0x389, 0x420, 0x421, 0x50A))
NEXO_FCA_ADDRS = frozenset((0x38D, 0x483))
NEXO_RADAR_TRACK_ADDRS = frozenset(range(0x500, 0x520))
NEXO_STOCK_SCC_SOURCE = 0
NEXO_RUNTIME_GUARD_GRACE_S = 0.30
NEXO_RUNTIME_GUARD_WINDOW_S = 0.25
NEXO_RUNTIME_GUARD_MIN_FRAMES = 3
NEXO_CAN_HISTORY_S = 5.0
NEXO_CAN_HISTORY_MAX = 4000


class NexoStockSccRuntimeGuard:
  """Fail closed when the factory SCC stream returns after radar takeover.

  Sources 128-191 are accepted Panda TX echoes and sources 192+ are safety
  blocks. Only physical source 0 SCC11/12/13/14 can trip the guard. A rolling
  five-second SCC/FCA/radar history is retained so the reason survives reboot.
  """

  def __init__(self, enabled: bool, *, grace_s: float = NEXO_RUNTIME_GUARD_GRACE_S,
               window_s: float = NEXO_RUNTIME_GUARD_WINDOW_S,
               min_frames: int = NEXO_RUNTIME_GUARD_MIN_FRAMES,
               history_s: float = NEXO_CAN_HISTORY_S) -> None:
    self.enabled = enabled
    self.grace_s = grace_s
    self.window_s = window_s
    self.min_frames = min_frames
    self.history_s = history_s
    self.armed = False
    self.armed_at = 0.0
    self._detections: deque[tuple[float, int]] = deque()
    self._recent_can: deque[tuple[float, int, int, str]] = deque(maxlen=NEXO_CAN_HISTORY_MAX)
    self._last_fault: dict[str, object] = {}

  def arm(self, now: float | None = None) -> None:
    self.armed = self.enabled
    self.armed_at = time.monotonic() if now is None else now
    self._detections.clear()
    self._recent_can.clear()
    self._last_fault = {}

  def disarm(self) -> None:
    """Stop runtime detection while retaining the captured fault history."""
    self.armed = False
    self._detections.clear()

  def _prune(self, timestamp: float) -> None:
    detection_cutoff = timestamp - self.window_s
    while self._detections and self._detections[0][0] < detection_cutoff:
      self._detections.popleft()

    history_cutoff = timestamp - self.history_s
    while self._recent_can and self._recent_can[0][0] < history_cutoff:
      self._recent_can.popleft()

  def observe(self, can_messages: Iterable[object], now: float | None = None) -> bool:
    if not self.armed:
      return False

    timestamp = time.monotonic() if now is None else now
    grace_complete = timestamp - self.armed_at >= self.grace_s

    for msg in can_messages:
      source = int(getattr(msg, "src", -1))
      address = int(getattr(msg, "address", -1))
      if address in NEXO_STOCK_SCC_ADDRS or address in NEXO_FCA_ADDRS or address in NEXO_RADAR_TRACK_ADDRS:
        try:
          payload = bytes(getattr(msg, "dat", b"")).hex(" ")
        except Exception:
          payload = ""
        self._recent_can.append((timestamp, source, address, payload))

      if (grace_complete and getattr(msg, "src", -1) == NEXO_STOCK_SCC_SOURCE and
          address in NEXO_STOCK_SCC_ADDRS):
        self._detections.append((timestamp, address))

    self._prune(timestamp)
    if not grace_complete or len(self._detections) < self.min_frames:
      return False

    counts = Counter(address for _, address in self._detections)
    first_seen = self._detections[0][0]
    self._last_fault = {
      "armed_monotonic": self.armed_at,
      "detected_monotonic": timestamp,
      "first_stock_scc_monotonic": first_seen,
      "first_stock_scc_after_arm_s": round(first_seen - self.armed_at, 6),
      "detection_window_s": self.window_s,
      "minimum_frames": self.min_frames,
      "stock_scc_counts": {f"0x{address:03X}": count for address, count in sorted(counts.items())},
      "recent_can": [
        {
          "age_s": round(timestamp - seen_at, 6),
          "src": source,
          "address": f"0x{address:03X}",
          "data": payload,
        }
        for seen_at, source, address, payload in self._recent_can
      ],
    }
    return True

  def fault_snapshot(self) -> dict[str, object]:
    return dict(self._last_fault)
