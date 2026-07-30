import unittest

from openpilot.selfdrive.car.card import recover_nexo_stock_cruise


class FakeParams:
  def __init__(self):
    self.values = {}

  def put_bool(self, key, value, block=False):
    self.values[key] = value


class TestNexoStockCruiseRecovery(unittest.TestCase):
  def test_recovers_only_verified_nexo_longitudinal_failures(self):
    params = FakeParams()
    recovered = recover_nexo_stock_cruise(
      params,
      "HYUNDAI_NEXO_1ST_GEN",
      RuntimeError("NEXO stock SCC remained active; longitudinal control not started"),
    )

    self.assertTrue(recovered)
    self.assertFalse(params.values["AlphaLongitudinalEnabled"])
    self.assertFalse(params.values["ExperimentalMode"])

  def test_does_not_change_other_failures_or_cars(self):
    for fingerprint, error in (
      ("HYUNDAI_NEXO_1ST_GEN", RuntimeError("unrelated initialization failure")),
      ("HYUNDAI_IONIQ_5", RuntimeError("NEXO stock SCC remained active; longitudinal control not started")),
    ):
      with self.subTest(fingerprint=fingerprint, error=str(error)):
        params = FakeParams()
        self.assertFalse(recover_nexo_stock_cruise(params, fingerprint, error))
        self.assertEqual({}, params.values)


if __name__ == "__main__":
  unittest.main()
