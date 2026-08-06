#!/usr/bin/env python3
import unittest

from opendbc.car.hyundai.values import HyundaiSafetyFlags
from opendbc.car.structs import CarParams
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.common import CANPackerSafety, make_msg


class TestHyundaiNexoStandardLong(unittest.TestCase):
  """Targeted parity tests for the safety mode proven by NEXOdriveAI logs."""

  TX_MSGS = [[0x340, 0], [0x4F1, 0], [0x485, 0], [0x420, 0], [0x421, 0],
             [0x50A, 0], [0x389, 0], [0x4A2, 0], [0x38D, 0], [0x483, 0], [0x7D0, 0]]
  SCC_ADDRS = (0x389, 0x420, 0x421, 0x50A)
  PARAM = HyundaiSafetyFlags.FCEV_GAS | HyundaiSafetyFlags.LONG

  def setUp(self):
    self.packer = CANPackerSafety("hyundai_can_generated")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, self.PARAM)
    self.safety.init_tests()

  def _scc12(self, accel: float):
    values = {
      "ACCMode": 1,
      "aReqRaw": accel,
      "aReqValue": accel,
      "AEB_CmdAct": 0,
      "CR_VSM_DecCmd": 0,
    }
    return self.packer.make_can_msg_safety("SCC12", 0, values)

  def test_expected_standard_fcev_long_param(self):
    self.assertEqual(self.safety.get_current_safety_param(), int(self.PARAM))
    self.assertEqual(int(self.PARAM), 260)

  def test_exact_tester_present_is_allowed(self):
    tester_present = make_msg(0, 0x7D0, 8, b"\x02\x3E\x80\x00\x00\x00\x00\x00")
    wrong_subfunction = make_msg(0, 0x7D0, 8, b"\x02\x3E\x00\x00\x00\x00\x00\x00")
    wrong_payload = make_msg(0, 0x7D0, 8, b"\x03\x28\x83\x01\x00\x00\x00\x00")
    self.assertTrue(self.safety.safety_tx_hook(tester_present))
    self.assertFalse(self.safety.safety_tx_hook(wrong_subfunction))
    self.assertFalse(self.safety.safety_tx_hook(wrong_payload))

  def test_scc12_keeps_normal_longitudinal_limits(self):
    self.safety.set_controls_allowed(True)
    self.assertTrue(self.safety.safety_tx_hook(self._scc12(0.5)))
    self.assertFalse(self.safety.safety_tx_hook(self._scc12(3.0)))

  def test_controls_off_blocks_nonzero_acceleration(self):
    self.safety.set_controls_allowed(False)
    self.assertTrue(self.safety.safety_tx_hook(self._scc12(0.0)))
    self.assertFalse(self.safety.safety_tx_hook(self._scc12(0.1)))

  def test_standard_long_statically_blocks_camera_side_scc(self):
    for addr in self.SCC_ADDRS:
      self.assertEqual(self.safety.safety_fwd_hook(2, addr), -1)


if __name__ == "__main__":
  unittest.main()
