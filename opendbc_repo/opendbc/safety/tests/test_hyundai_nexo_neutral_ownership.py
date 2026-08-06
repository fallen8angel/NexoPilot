#!/usr/bin/env python3
import unittest

from opendbc.car.hyundai.values import HyundaiSafetyFlags
from opendbc.car.structs import CarParams
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.common import CANPackerSafety


class TestHyundaiNexoNeutralOwnership(unittest.TestCase):
  # Metadata used by the cross-mode TX isolation test. This historical dynamic
  # SCC mode is no longer selected by the production NEXO interface.
  TX_MSGS = [[0x340, 0], [0x4F1, 0], [0x485, 0], [0x420, 0], [0x421, 0],
             [0x50A, 0], [0x389, 0], [0x4A2, 0], [0x38D, 0], [0x483, 0], [0x7D0, 0]]
  SCC_ADDRS = (0x389, 0x420, 0x421, 0x50A)
  FCA_ADDRS = (0x38D, 0x483)
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

  def test_controls_off_allows_only_neutral_scc12_and_arms_ownership(self):
    self.safety.set_controls_allowed(False)
    self.assertTrue(self.safety.safety_tx_hook(self._scc12(0.0, acc_mode=0)))
    for addr in self.SCC_ADDRS:
      self.assertEqual(self.safety.safety_fwd_hook(2, addr), -1)

  def test_controls_off_rejects_active_mode_even_with_zero_accel(self):
    self.safety.set_controls_allowed(False)
    self.assertFalse(self.safety.safety_tx_hook(self._scc12(0.0, acc_mode=1)))
    for addr in self.SCC_ADDRS:
      self.assertEqual(self.safety.safety_fwd_hook(2, addr), 0)

  def test_controls_off_rejects_nonzero_accel(self):
    self.safety.set_controls_allowed(False)
    self.assertFalse(self.safety.safety_tx_hook(self._scc12(0.1, acc_mode=0)))
    for addr in self.SCC_ADDRS:
      self.assertEqual(self.safety.safety_fwd_hook(2, addr), 0)

  def test_controls_on_keeps_normal_longitudinal_limits(self):
    self.safety.set_controls_allowed(True)
    self.assertTrue(self.safety.safety_tx_hook(self._scc12(0.5, acc_mode=1)))
    self.assertFalse(self.safety.safety_tx_hook(self._scc12(3.0, acc_mode=1)))

  def test_timeout_restores_factory_scc_forwarding(self):
    self.safety.set_controls_allowed(False)
    self.assertTrue(self.safety.safety_tx_hook(self._scc12(0.0, acc_mode=0)))
    self.safety.set_timer(1_000_000 + self.TIMEOUT_US)
    for addr in self.SCC_ADDRS:
      self.assertEqual(self.safety.safety_fwd_hook(2, addr), 0)

  def test_factory_fca_is_never_claimed(self):
    self.safety.set_controls_allowed(False)
    self.assertTrue(self.safety.safety_tx_hook(self._scc12(0.0, acc_mode=0)))
    for addr in self.FCA_ADDRS:
      self.assertEqual(self.safety.safety_fwd_hook(2, addr), 0)


if __name__ == "__main__":
  unittest.main()
