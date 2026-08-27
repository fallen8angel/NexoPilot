import unittest
from unittest.mock import patch

from opendbc.car.hyundai import nexo_takeover


class TestNexoTakeoverPhysicalVerification(unittest.TestCase):
  def setUp(self):
    self.can_recv = lambda wait_for_one=False: []
    self.can_send = lambda messages: None
    self.keepalive = lambda: None

  def _run(self, observation):
    with patch.object(nexo_takeover, "disable_ecu", return_value=False), \
         patch.object(nexo_takeover, "_observe_source0_scc", return_value=observation):
      return nexo_takeover._suppress_once(
        self.can_recv,
        self.can_send,
        bus=0,
        addr=0x7D0,
        communication_control=b"\x28\x83\x01",
        sample_s=0.01,
        settle_s=0.0,
        min_source0_frames=20,
        keepalive=self.keepalive,
        label="test",
        owner="123:456",
      )

  def test_suppressed_uds_response_accepts_proven_physical_silence(self):
    result = self._run({
      "source0_frames": 100,
      "source0_scc_total": 0,
      "source0_scc_counts": {},
    })
    self.assertFalse(result["acknowledged"])
    self.assertTrue(result["enough_bus_data"])
    self.assertTrue(result["success"])

  def test_fails_closed_when_bus_activity_is_insufficient(self):
    result = self._run({
      "source0_frames": 0,
      "source0_scc_total": 0,
      "source0_scc_counts": {},
    })
    self.assertFalse(result["success"])

  def test_fails_closed_when_stock_scc_is_present(self):
    result = self._run({
      "source0_frames": 100,
      "source0_scc_total": 2,
      "source0_scc_counts": {"0x420": 1, "0x421": 1},
    })
    self.assertFalse(result["success"])


if __name__ == "__main__":
  unittest.main()
