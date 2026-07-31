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

    self.assertEqual(["disable", "enable", "disable"], calls)

  def test_stock_scc_disable_failure_stops_long_init(self):
    with patch("opendbc.car.hyundai.interface.disable_ecu", return_value=False), \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks", return_value=False):
      with self.assertRaisesRegex(RuntimeError, "stock SCC communication"):
        CarInterface.init(self.CP, object(), object())

  def test_radar_failure_restores_stock_scc_before_stopping_long_init(self):
    calls = []

    def disable(*args, **kwargs):
      calls.append(kwargs["com_cont_req"])
      return True

    with patch("opendbc.car.hyundai.interface.disable_ecu", side_effect=disable), \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks", return_value=False) as enable:
      with self.assertRaisesRegex(RuntimeError, "radar track activation"):
        CarInterface.init(self.CP, object(), object())

    self.assertEqual(3, enable.call_args.kwargs["retries"])
    self.assertEqual(b"\x28\x83\x01", calls[0])
    self.assertEqual(b"\x28\x80\x01", calls[1])

  def test_post_track_disable_failure_restores_stock_scc(self):
    calls = []

    def disable(*args, **kwargs):
      calls.append(kwargs["com_cont_req"])
      return len(calls) != 2

    with patch("opendbc.car.hyundai.interface.disable_ecu", side_effect=disable), \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks", return_value=True):
      with self.assertRaisesRegex(RuntimeError, "resumed after radar track activation"):
        CarInterface.init(self.CP, object(), object())

    self.assertEqual([b"\x28\x83\x01", b"\x28\x83\x01", b"\x28\x80\x01"], calls)

  def test_stock_cruise_does_not_touch_radar(self):
    self.CP.openpilotLongitudinalControl = False
    with patch("opendbc.car.hyundai.interface.disable_ecu") as disable, \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks") as enable:
      CarInterface.init(self.CP, object(), object())

    disable.assert_not_called()
    enable.assert_not_called()


if __name__ == "__main__":
  unittest.main()
