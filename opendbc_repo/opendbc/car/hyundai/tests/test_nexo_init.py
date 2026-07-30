import unittest
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR


class TestNexoLongitudinalInit(unittest.TestCase):
  def setUp(self):
    self.CP = SimpleNamespace(
      openpilotLongitudinalControl=True,
      flags=0,
      carFingerprint=CAR.HYUNDAI_NEXO_1ST_GEN,
    )

  def test_disables_stock_scc_before_enabling_tracks(self):
    calls = []

    def disable(*args, **kwargs):
      calls.append("disable")
      return True

    def enable(*args, **kwargs):
      calls.append("enable")
      return True

    with patch("opendbc.car.hyundai.interface.disable_ecu", side_effect=disable), \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks", side_effect=enable):
      CarInterface.init(self.CP, object(), object())

    self.assertEqual(["disable", "enable"], calls)

  def test_diagnostic_failure_does_not_stop_card(self):
    with patch("opendbc.car.hyundai.interface.disable_ecu", return_value=False), \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks", return_value=False):
      CarInterface.init(self.CP, object(), object())

  def test_stock_cruise_does_not_touch_radar(self):
    self.CP.openpilotLongitudinalControl = False
    with patch("opendbc.car.hyundai.interface.disable_ecu") as disable, \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks") as enable:
      CarInterface.init(self.CP, object(), object())

    disable.assert_not_called()
    enable.assert_not_called()


if __name__ == "__main__":
  unittest.main()
