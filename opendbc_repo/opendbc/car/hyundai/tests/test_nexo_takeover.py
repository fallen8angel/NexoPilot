import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.car.hyundai import nexo_takeover


class TestNexoTakeoverVerification(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.state_path = Path(self.temporary.name) / "state.json"
    self.can_send = object()

  def tearDown(self):
    self.temporary.cleanup()

  @staticmethod
  def _receiver(messages):
    def recv(wait_for_one=False):
      del wait_for_one
      return [[SimpleNamespace(address=address, src=source, dat=b"\x00" * 8)
               for address, source in messages]]
    return recv

  def test_verified_when_bus_is_alive_and_source0_scc_is_silent(self):
    recv = self._receiver([(0x200, 0), (0x251, 0), (0x386, 0)])
    with patch.object(nexo_takeover, "NEXO_TAKEOVER_VERIFY_LOG", self.state_path), \
         patch.object(nexo_takeover, "claim_owner", return_value="123:456"), \
         patch.object(nexo_takeover, "disable_ecu", return_value=True):
      result = nexo_takeover.ensure_nexo_stock_scc_silent(
        recv, self.can_send, bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
        trace=lambda _: None, attempts=1, sample_s=0.01, settle_s=0.0, min_source0_frames=1,
      )
    self.assertTrue(result)
    self.assertIn('"success": true', self.state_path.read_text(encoding="utf-8"))
    self.assertIn('"owner": "123:456"', self.state_path.read_text(encoding="utf-8"))

  def test_fails_closed_when_source0_scc_remains(self):
    recv = self._receiver([(0x200, 0), (0x420, 0), (0x421, 0)])
    with patch.object(nexo_takeover, "NEXO_TAKEOVER_VERIFY_LOG", self.state_path), \
         patch.object(nexo_takeover, "claim_owner", return_value="123:456"), \
         patch.object(nexo_takeover, "disable_ecu", return_value=True):
      result = nexo_takeover.ensure_nexo_stock_scc_silent(
        recv, self.can_send, bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
        trace=lambda _: None, attempts=1, sample_s=0.01, settle_s=0.0, min_source0_frames=1,
      )
    self.assertFalse(result)
    self.assertIn('"state": "failed"', self.state_path.read_text(encoding="utf-8"))

  def test_fails_closed_when_takeover_owner_cannot_be_claimed(self):
    recv = self._receiver([(0x200, 0), (0x251, 0), (0x386, 0)])
    with patch.object(nexo_takeover, "NEXO_TAKEOVER_VERIFY_LOG", self.state_path), \
         patch.object(nexo_takeover, "claim_owner", return_value=""), \
         patch.object(nexo_takeover, "disable_ecu") as disable:
      result = nexo_takeover.ensure_nexo_stock_scc_silent(
        recv, self.can_send, bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
        trace=lambda _: None, attempts=1, sample_s=0.01, settle_s=0.0, min_source0_frames=1,
      )
    self.assertFalse(result)
    disable.assert_not_called()
    self.assertIn("takeover owner claim failed", self.state_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
  unittest.main()
