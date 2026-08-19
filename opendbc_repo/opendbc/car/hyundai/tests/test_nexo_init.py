import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.car import nexo_session_owner
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
    self.owner = root / "owner"
    self.owner_lock = root / "owner.lock"
    self.patchers = (
      patch.object(interface, "NEXO_SCC_TAKEOVER_MARKER", self.marker),
      patch.object(interface, "NEXO_SCC_RESTORE_LOG", self.restore_log),
      patch.object(interface, "NEXO_LONG_INIT_LOG", str(self.long_log)),
      patch.object(nexo_session_owner, "NEXO_SCC_OWNER", self.owner),
      patch.object(nexo_session_owner, "NEXO_SCC_OWNER_LOCK", self.owner_lock),
    )
    for patcher in self.patchers:
      patcher.start()

  def tearDown(self):
    for patcher in reversed(self.patchers):
      patcher.stop()
    self.temporary.cleanup()

  def test_successful_takeover_programs_radar_before_final_scc_suppression(self):
    order = []

    def radar(*args, **kwargs):
      order.append("radar")
      return True

    def verify(*args, **kwargs):
      order.append("suppress")
      return True

    with patch.object(interface, "disable_ecu") as disable, \
         patch.object(interface, "enable_radar_tracks", side_effect=radar) as radar_enable, \
         patch.object(interface, "ensure_nexo_stock_scc_silent", side_effect=verify) as verify_silence:
      CarInterface.init(self.CP, self.can_recv, self.can_send)

    self.assertEqual(["radar", "suppress"], order)
    disable.assert_not_called()
    self.assertEqual(40, radar_enable.call_args.kwargs["retries"])
    verify_silence.assert_called_once()
    self.assertTrue(interface.nexo_stock_scc_restore_pending())
    self.assertIn("longitudinal_takeover_ready", self.marker.read_text())
    log = self.long_log.read_text()
    self.assertLess(log.index("STEP 1 run radar-track"), log.index("STEP 2 final SCC suppression"))
    self.assertIn("physical src0 silence is the success criterion", log)

  def test_source0_scc_verification_failure_restores_stock_before_raising(self):
    calls = []

    def disable(*args, **kwargs):
      calls.append(kwargs["com_cont_req"])
      return True

    with patch.object(interface, "disable_ecu", side_effect=disable), \
         patch.object(interface, "enable_radar_tracks", return_value=True), \
         patch.object(interface, "ensure_nexo_stock_scc_silent", return_value=False):
      with self.assertRaisesRegex(RuntimeError, "stock SCC remained active"):
        CarInterface.init(self.CP, self.can_recv, self.can_send)

    self.assertEqual([b"\x28\x80\x01"], calls)
    self.assertFalse(interface.nexo_stock_scc_restore_pending())

  def test_radar_failure_never_mutes_stock_scc(self):
    with patch.object(interface, "disable_ecu") as disable, \
         patch.object(interface, "enable_radar_tracks", return_value=False), \
         patch.object(interface, "ensure_nexo_stock_scc_silent") as verify:
      with self.assertRaisesRegex(RuntimeError, "radar track activation"):
        CarInterface.init(self.CP, self.can_recv, self.can_send)

    disable.assert_not_called()
    verify.assert_not_called()
    self.assertFalse(interface.nexo_stock_scc_restore_pending())

  def test_radar_exception_never_mutes_stock_scc(self):
    with patch.object(interface, "disable_ecu") as disable, \
         patch.object(interface, "enable_radar_tracks", side_effect=ValueError("boom")), \
         patch.object(interface, "ensure_nexo_stock_scc_silent") as verify:
      with self.assertRaisesRegex(ValueError, "boom"):
        CarInterface.init(self.CP, self.can_recv, self.can_send)

    disable.assert_not_called()
    verify.assert_not_called()
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

  def test_older_process_cannot_restore_or_clear_newer_takeover(self):
    self.marker.write_text("newer longitudinal_takeover_ready")
    self.owner.write_text("999:1\n")

    with patch.object(nexo_session_owner, "_owner_alive", return_value=True), \
         patch.object(interface, "disable_ecu") as disable:
      restored = interface.restore_nexo_stock_scc_communication(
        self.can_recv, self.can_send, reason="old card exit", retries=1,
      )

    self.assertTrue(restored)
    disable.assert_not_called()
    self.assertTrue(self.marker.exists())
    self.assertEqual("999:1", self.owner.read_text().strip())
    self.assertIn("RESTORE SKIP", self.restore_log.read_text())

  def test_owner_restore_clears_owner_and_duplicate_deinit_is_inert(self):
    self.marker.write_text("owned longitudinal_takeover_ready")
    self.owner.write_text(nexo_session_owner.current_owner_token() + "\n")

    with patch.object(interface, "disable_ecu", return_value=True) as disable:
      self.assertTrue(CarInterface.deinit(self.CP, self.can_recv, self.can_send))
      self.assertTrue(CarInterface.deinit(self.CP, self.can_recv, self.can_send))

    self.assertEqual(1, disable.call_count)
    self.assertFalse(self.marker.exists())
    self.assertFalse(self.owner.exists())

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
