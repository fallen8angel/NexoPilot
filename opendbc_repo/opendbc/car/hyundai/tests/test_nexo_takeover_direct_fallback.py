import unittest
from unittest.mock import patch

from opendbc.car.hyundai import nexo_takeover


class TestNexoTakeoverDirectFallback(unittest.TestCase):
  def test_direct_fallback_sends_only_communication_control(self):
    calls = {}

    class FakeQuery:
      def __init__(self, can_send, can_recv, bus, addresses, requests, responses):
        calls["can_send"] = can_send
        calls["can_recv"] = can_recv
        calls["bus"] = bus
        calls["addresses"] = addresses
        calls["requests"] = requests
        calls["responses"] = responses

      def get_data(self, timeout):
        calls["timeout"] = timeout
        return {}

    can_recv = object()
    can_send = object()
    with patch.object(nexo_takeover, "IsoTpParallelQuery", FakeQuery):
      sent, detail = nexo_takeover._send_communication_control_direct(
        can_recv, can_send, bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
      )

    self.assertTrue(sent)
    self.assertEqual("", detail)
    self.assertEqual(0, calls["bus"])
    self.assertEqual([(0x7D0, None)], calls["addresses"])
    self.assertEqual([b"\x28\x83\x01"], calls["requests"])
    self.assertEqual([b""], calls["responses"])
    self.assertEqual(0, calls["timeout"])

  def test_suppress_once_uses_direct_fallback_when_normal_disable_is_not_acknowledged(self):
    silent = {
      "source0_frames": 100,
      "source0_scc_total": 0,
      "source0_scc_counts": {},
    }
    with patch.object(nexo_takeover, "disable_ecu", return_value=False), \
         patch.object(nexo_takeover, "_send_communication_control_direct", return_value=(True, "")) as direct, \
         patch.object(nexo_takeover, "_observe_source0_scc", return_value=silent):
      record = nexo_takeover._suppress_once(
        lambda wait_for_one=False: [], lambda messages: None,
        bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
        sample_s=0.01, settle_s=0.0, min_source0_frames=20,
        keepalive=lambda: None, label="test", owner="1:2",
      )

    direct.assert_called_once()
    self.assertFalse(record["acknowledged"])
    self.assertTrue(record["directCommunicationControlAttempted"])
    self.assertTrue(record["directCommunicationControlSent"])
    self.assertTrue(record["success"])

  def test_direct_fallback_does_not_bypass_physical_scc_verification(self):
    stock_scc_alive = {
      "source0_frames": 100,
      "source0_scc_total": 10,
      "source0_scc_counts": {"0x420": 5, "0x421": 5},
    }
    with patch.object(nexo_takeover, "disable_ecu", return_value=False), \
         patch.object(nexo_takeover, "_send_communication_control_direct", return_value=(True, "")), \
         patch.object(nexo_takeover, "_observe_source0_scc", return_value=stock_scc_alive):
      record = nexo_takeover._suppress_once(
        lambda wait_for_one=False: [], lambda messages: None,
        bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
        sample_s=0.01, settle_s=0.0, min_source0_frames=20,
        keepalive=lambda: None, label="test", owner="1:2",
      )

    self.assertTrue(record["directCommunicationControlSent"])
    self.assertFalse(record["success"])
    self.assertEqual(10, record["source0_scc_total"])


if __name__ == "__main__":
  unittest.main()
