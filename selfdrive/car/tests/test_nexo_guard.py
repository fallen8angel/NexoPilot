import unittest
from types import SimpleNamespace

from selfdrive.car.nexo_guard import NexoStockSccRuntimeGuard


def frame(address: int, src: int = 0, dat: bytes = b"\x00" * 8):
  return SimpleNamespace(address=address, src=src, dat=dat)


class TestNexoStockSccRuntimeGuard(unittest.TestCase):
  def test_not_armed_is_inert(self):
    guard = NexoStockSccRuntimeGuard(True, grace_s=0.0)
    self.assertFalse(guard.observe([frame(0x420)] * 4, now=1.0))

  def test_ignores_startup_buffer_during_grace(self):
    guard = NexoStockSccRuntimeGuard(True, grace_s=0.3)
    guard.arm(now=1.0)
    self.assertFalse(guard.observe([frame(0x420)] * 4, now=1.2))
    self.assertFalse(guard.observe([], now=1.31))

  def test_detects_sustained_vehicle_side_scc_and_keeps_history(self):
    guard = NexoStockSccRuntimeGuard(True, grace_s=0.0, window_s=0.25, min_frames=3)
    guard.arm(now=1.0)
    self.assertFalse(guard.observe([frame(0x38D), frame(0x420)], now=1.00))
    self.assertFalse(guard.observe([frame(0x421)], now=1.05))
    self.assertTrue(guard.observe([frame(0x389), frame(0x500)], now=1.10))
    snapshot = guard.fault_snapshot()
    self.assertEqual({"0x389": 1, "0x420": 1, "0x421": 1}, snapshot["stock_scc_counts"])
    self.assertGreaterEqual(len(snapshot["recent_can"]), 5)

  def test_factory_fca_does_not_trip_scc_guard(self):
    guard = NexoStockSccRuntimeGuard(True, grace_s=0.0, min_frames=1)
    guard.arm(now=1.0)
    self.assertFalse(guard.observe([frame(0x38D), frame(0x483)], now=1.1))

  def test_ignores_outgoing_and_blocked_sources(self):
    guard = NexoStockSccRuntimeGuard(True, grace_s=0.0, min_frames=1)
    guard.arm(now=1.0)
    self.assertFalse(guard.observe([frame(0x420, 128), frame(0x421, 192)], now=1.1))

  def test_disabled_for_stock_cruise(self):
    guard = NexoStockSccRuntimeGuard(False, grace_s=0.0, min_frames=1)
    guard.arm(now=1.0)
    self.assertFalse(guard.observe([frame(0x420)], now=1.1))


if __name__ == "__main__":
  unittest.main()
