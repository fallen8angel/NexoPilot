import unittest

from opendbc.car.hyundai.nexo_acc_fault import NexoAccFaultQualifier


class TestNexoAccFaultQualifier(unittest.TestCase):
  def test_startup_transient_is_not_promoted_to_fault(self):
    qualifier = NexoAccFaultQualifier(startup_grace_s=2.0)
    self.assertFalse(qualifier.update(True, 10.0, takeover_active=False).qualified_fault)
    self.assertFalse(qualifier.update(True, 11.99, takeover_active=False).qualified_fault)
    healthy = qualifier.update(False, 12.0, takeover_active=False)
    self.assertFalse(healthy.qualified_fault)
    self.assertTrue(healthy.healthy_seen)

  def test_persistent_startup_fault_is_reported(self):
    qualifier = NexoAccFaultQualifier(startup_grace_s=2.0)
    self.assertFalse(qualifier.update(True, 20.0, takeover_active=False).qualified_fault)
    decision = qualifier.update(True, 22.0, takeover_active=False)
    self.assertTrue(decision.qualified_fault)
    self.assertEqual("startup_fault_persisted", decision.reason)

  def test_fault_after_healthy_sample_is_immediate(self):
    qualifier = NexoAccFaultQualifier(startup_grace_s=2.0)
    self.assertFalse(qualifier.update(False, 30.0, takeover_active=False).qualified_fault)
    decision = qualifier.update(True, 30.01, takeover_active=False)
    self.assertTrue(decision.qualified_fault)
    self.assertEqual("fault_after_healthy", decision.reason)

  def test_takeover_makes_accenable_untrusted_without_hiding_future_faults(self):
    qualifier = NexoAccFaultQualifier(startup_grace_s=2.0)
    self.assertFalse(qualifier.update(False, 40.0, takeover_active=False).qualified_fault)

    takeover = qualifier.update(True, 40.01, takeover_active=True)
    self.assertFalse(takeover.qualified_fault)
    self.assertEqual("signal_untrusted_takeover", takeover.reason)
    self.assertTrue(takeover.healthy_seen)

    after_takeover = qualifier.update(True, 40.02, takeover_active=False)
    self.assertTrue(after_takeover.qualified_fault)
    self.assertEqual("fault_after_healthy", after_takeover.reason)


if __name__ == "__main__":
  unittest.main()
