import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.car.hyundai import interface
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR


class TestNexoLongitudinalInit(unittest.TestCase):
  def setUp(self):
    self.CP = SimpleNamespace(
      openpilotLongitudinalControl=True,
      flags=0,
      carFingerprint=CAR.HYUNDAI_NEXO_1ST_GEN,
    )
    self.can_recv = lambda wait_for_one=False: []
    self.can_send = object()
    self.temporary = tempfile.TemporaryDirectory()
    root = Path(self.temporary.name)
    self.marker = root / "takeover"
    self.restore_log = root / "restore.log"
    self.long_log = root / "long.log"
    self.patchers = (
      patch.object(interface, "NEXO_SCC_TAKEOVER_MARKER", self.marker),
      patch.object(interface, "NEXO_SCC_RESTORE_LOG", self.restore_log),
      patch.object(interface, "NEXO_LONG_INIT_LOG", str(self.long_log)),
    )
    for patcher in self.patchers:
      patcher.start()

  def tearDown(self):
    for patcher in reversed(self.patchers):
      patcher.stop()
    self.temporary.cleanup()

  def test_successful_takeover_leaves_recovery_marker(self):
    calls = []

    def disable(*args, **kwargs):
      calls.append(kwargs["com_cont_req"])
      return True

    with patch.object(interface, "disable_ecu", side_effect=disable), \
         patch.object(interface, "enable_radar_tracks", return_value=True) as radar_enable:
      CarInterface.init(self.CP, self.can_recv, self.can_send)

    self.assertEqual([b"\x28\x83\x01"], calls)
    self.assertEqual(40, radar_enable.call_args.kwargs["retries"])
    self.assertTrue(interface.nexo_stock_scc_restore_pending())
    self.assertIn("longitudinal_takeover_ready", self.marker.read_text())

  def test_radar_failure_restores_stock_before_raising(self):
    calls = []

    def disable(*args, **kwargs):
      calls.append(kwargs["com_cont_req"])
      return True

    with patch.object(interface, "disable_ecu", side_effect=disable), \
         patch.object(interface, "enable_radar_tracks", return_value=False):
      with self.assertRaisesRegex(RuntimeError, "radar track activation"):
        CarInterface.init(self.CP, self.can_recv, self.can_send)

    self.assertEqual(b"\x28\x83\x01", calls[0])
    self.assertEqual(b"\x28\x80\x01", calls[1])
    self.assertFalse(interface.nexo_stock_scc_restore_pending())

  def test_unexpected_init_exception_also_restores(self):
    calls = []

    def disable(*args, **kwargs):
      calls.append(kwargs["com_cont_req"])
      return True

    with patch.object(interface, "disable_ecu", side_effect=disable), \
         patch.object(interface, "enable_radar_tracks", side_effect=ValueError("boom")):
      with self.assertRaisesRegex(ValueError, "boom"):
        CarInterface.init(self.CP, self.can_recv, self.can_send)

    self.assertEqual([b"\x28\x83\x01", b"\x28\x80\x01"], calls)
    self.assertFalse(interface.nexo_stock_scc_restore_pending())

  def test_restore_retries_and_only_then_clears_marker(self):
    self.marker.write_text("pending")
    with patch.object(interface, "disable_ecu", side_effect=[False, True]) as disable:
      restored = interface.restore_nexo_stock_scc_communication(
        self.can_recv, self.can_send, reason="test", retries=3,
      )
    self.assertTrue(restored)
    self.assertEqual(2, disable.call_count)
    self.assertFalse(self.marker.exists())

  def test_failed_restore_keeps_marker_for_next_start(self):
    self.marker.write_text("pending")
    with patch.object(interface, "disable_ecu", return_value=False):
      restored = interface.restore_nexo_stock_scc_communication(
        self.can_recv, self.can_send, reason="test", retries=2,
      )
    self.assertFalse(restored)
    self.assertTrue(self.marker.exists())
    self.assertIn("restore_pending", self.marker.read_text())

  def test_stock_cruise_without_marker_does_not_touch_uds(self):
    self.CP.openpilotLongitudinalControl = False
    with patch.object(interface, "disable_ecu") as disable, \
         patch.object(interface, "enable_radar_tracks") as enable:
      CarInterface.init(self.CP, self.can_recv, self.can_send)
    disable.assert_not_called()
    enable.assert_not_called()

  def test_stock_mode_deinit_repairs_stale_takeover_marker(self):
    self.CP.openpilotLongitudinalControl = False
    self.marker.write_text("pending")
    with patch.object(interface, "disable_ecu", return_value=True) as disable:
      restored = CarInterface.deinit(self.CP, self.can_recv, self.can_send)
    self.assertTrue(restored)
    self.assertEqual(b"\x28\x80\x01", disable.call_args.kwargs["com_cont_req"])
    self.assertFalse(self.marker.exists())


if __name__ == "__main__":
  unittest.main()
