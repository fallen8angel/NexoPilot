import unittest

from opendbc.car.hyundai.values import HyundaiSafetyFlags
from opendbc.car.structs import CarParams
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py


class TestHyundaiNexoNeutralSccOwnership(unittest.TestCase):
  SCC_ADDRS = (0x389, 0x420, 0x421, 0x50A)
  TIMEOUT_US = 400_000

  def setUp(self):
    self.packer = CANPackerSafety("hyundai_can_generated")
    self.safety = libsafety_py.libsafety
    param = HyundaiSafetyFlags.FCEV_GAS | HyundaiSafetyFlags.LONG | HyundaiSafetyFlags.NEXO_DYNAMIC_SCC
    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, param)
    self.safety.init_tests()
    self.safety.set_timer(1_000_000)

  def _scc12(self, accel: float, acc_mode: int = 0):
    values = {
      "ACCMode": acc_mode,
      "aReqRaw": accel,
      "aReqValue": accel,
      "AEB_CmdAct": 0,
      "CR_VSM_DecCmd": 0,
    }
    return self.packer.make_can_msg_safety("SCC12", 0, values)

  def test_neutral_scc12_claims_ownership_with_controls_off(self):
    self.safety.set_controls_allowed(False)
    self.assertTrue(self.safety.safety_tx_hook(self._scc12(0.0, 0)))
    for address in self.SCC_ADDRS:
      self.assertEqual(self.safety.safety_fwd_hook(2, address), -1)

  def test_nonzero_scc12_remains_blocked_with_controls_off(self):
    self.safety.set_controls_allowed(False)
    self.assertFalse(self.safety.safety_tx_hook(self._scc12(0.1, 1)))
    for address in self.SCC_ADDRS:
      self.assertEqual(self.safety.safety_fwd_hook(2, address), 0)

  def test_neutral_ownership_times_out(self):
    self.safety.set_controls_allowed(False)
    self.assertTrue(self.safety.safety_tx_hook(self._scc12(0.0, 0)))
    self.safety.set_timer(1_000_000 + self.TIMEOUT_US)
    for address in self.SCC_ADDRS:
      self.assertEqual(self.safety.safety_fwd_hook(2, address), 0)


if __name__ == "__main__":
  unittest.main()
