import unittest

from opendbc.car.hyundai.nexo_acc_fault import NexoAccFaultQualifier


class TestNexoAccFaultQualifier(unittest.TestCase):
  def test_startup_transient_is_not_promoted_to_fault(self):
    qualifier = NexoAccFaultQualifier(startup_grace_s=2.0)
    self.assertFalse(qualifier.update(True, 10.0).qualified_fault)
    self.assertFalse(qualifier.update(True, 11.99).qualified_fault)
    healthy = qualifier.update(False, 12.0)
    self.assertFalse(healthy.qualified_fault)
    self.assertTrue(healthy.healthy_seen)

  def test_persistent_startup_fault_is_reported(self):
    qualifier = NexoAccFaultQualifier(startup_grace_s=2.0)
    self.assertFalse(qualifier.update(True, 20.0).qualified_fault)
    decision = qualifier.update(True, 22.0)
    self.assertTrue(decision.qualified_fault)
    self.assertEqual("startup_fault_persisted", decision.reason)

  def test_fault_after_healthy_sample_is_immediate(self):
    qualifier = NexoAccFaultQualifier(startup_grace_s=2.0)
    self.assertFalse(qualifier.update(False, 30.0).qualified_fault)
    decision = qualifier.update(True, 30.01)
    self.assertTrue(decision.qualified_fault)
    self.assertEqual("fault_after_healthy", decision.reason)


if __name__ == "__main__":
  unittest.main()
