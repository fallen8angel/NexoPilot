import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.car import nexo_session_owner
from opendbc.car.hyundai import interface
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR


class TestNexoRestoreGuard(unittest.TestCase):
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

  def test_deinit_skips_restore_uds_when_stock_scc_is_physically_active(self):
    self.marker.write_text("restore_pending\n")
    self.owner.write_text(nexo_session_owner.current_owner_token() + "\n")

    with patch.object(interface, "_nexo_stock_scc_already_active", return_value=True) as precheck, \
         patch.object(interface, "disable_ecu") as disable:
      restored = CarInterface.deinit(self.CP, self.can_recv, self.can_send)

    self.assertTrue(restored)
    precheck.assert_called_once_with(self.can_recv)
    disable.assert_not_called()
    self.assertFalse(self.marker.exists())
    self.assertFalse(self.owner.exists())
    self.assertIn("physical source0 stock SCC already active", self.restore_log.read_text())

  def test_deinit_keeps_normal_restore_when_stock_scc_activity_is_not_proven(self):
    self.marker.write_text("longitudinal_takeover_ready\n")
    self.owner.write_text(nexo_session_owner.current_owner_token() + "\n")

    with patch.object(interface, "_nexo_stock_scc_already_active", return_value=False), \
         patch.object(interface, "disable_ecu", return_value=True) as disable:
      restored = CarInterface.deinit(self.CP, self.can_recv, self.can_send)

    self.assertTrue(restored)
    self.assertEqual(1, disable.call_count)
    self.assertEqual(b"\x28\x80\x01", disable.call_args.kwargs["com_cont_req"])
    self.assertFalse(self.marker.exists())
    self.assertFalse(self.owner.exists())

  def test_active_newer_owner_is_checked_before_physical_precheck(self):
    self.marker.write_text("longitudinal_takeover_ready\n")
    self.owner.write_text("999:1\n")

    with patch.object(nexo_session_owner, "_owner_alive", return_value=True), \
         patch.object(interface, "_nexo_stock_scc_already_active") as precheck, \
         patch.object(interface, "disable_ecu") as disable:
      restored = interface.restore_nexo_stock_scc_communication(
        self.can_recv, self.can_send, reason="old card exit", retries=1,
      )

    self.assertTrue(restored)
    precheck.assert_not_called()
    disable.assert_not_called()
    self.assertTrue(self.marker.exists())
    self.assertEqual("999:1", self.owner.read_text().strip())


if __name__ == "__main__":
  unittest.main()
