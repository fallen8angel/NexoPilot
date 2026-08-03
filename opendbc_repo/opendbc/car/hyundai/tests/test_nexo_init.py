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
    self.can_recv = lambda wait_for_one=False: []
    self.can_send = object()

  def test_uses_verified_disable_then_radar_sequence(self):
    calls = []

    def disable(*args, **kwargs):
      calls.append("disable")
      return True

    def enable(*args, **kwargs):
      calls.append("enable")
      return True

    with patch("opendbc.car.hyundai.interface.disable_ecu", side_effect=disable), \
         patch("opendbc.car.hyundai.interface._nexo_stock_scc_active", return_value=False) as stock_active, \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks", side_effect=enable) as radar_enable:
      CarInterface.init(self.CP, self.can_recv, self.can_send)

    self.assertEqual(["disable", "enable"], calls)
    stock_active.assert_called_once_with(self.can_recv, 0)
    self.assertEqual(40, radar_enable.call_args.kwargs["retries"])

  def test_missing_disable_ack_falls_back_before_radar_activation(self):
    with patch("opendbc.car.hyundai.interface.disable_ecu", return_value=False) as disable, \
         patch("opendbc.car.hyundai.interface._nexo_stock_scc_active") as stock_active, \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks") as enable:
      with self.assertRaisesRegex(RuntimeError, "could not be disabled"):
        CarInterface.init(self.CP, self.can_recv, self.can_send)

    disable.assert_called_once()
    stock_active.assert_not_called()
    enable.assert_not_called()

  def test_stock_scc_remaining_active_restores_stock_before_fallback(self):
    calls = []

    def disable(*args, **kwargs):
      calls.append(kwargs["com_cont_req"])
      return True

    with patch("opendbc.car.hyundai.interface.disable_ecu", side_effect=disable), \
         patch("opendbc.car.hyundai.interface._nexo_stock_scc_active", return_value=True), \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks") as enable:
      with self.assertRaisesRegex(RuntimeError, "stock SCC remained active"):
        CarInterface.init(self.CP, self.can_recv, self.can_send)

    enable.assert_not_called()
    self.assertEqual(b"\x28\x83\x01", calls[0])
    self.assertEqual(b"\x28\x80\x01", calls[1])

  def test_radar_failure_restores_stock_scc_before_safe_recovery(self):
    calls = []

    def disable(*args, **kwargs):
      calls.append(kwargs["com_cont_req"])
      return True

    with patch("opendbc.car.hyundai.interface.disable_ecu", side_effect=disable), \
         patch("opendbc.car.hyundai.interface._nexo_stock_scc_active", return_value=False), \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks", return_value=False) as enable:
      with self.assertRaisesRegex(RuntimeError, "radar track activation"):
        CarInterface.init(self.CP, self.can_recv, self.can_send)

    self.assertEqual(40, enable.call_args.kwargs["retries"])
    self.assertEqual(b"\x28\x83\x01", calls[0])
    self.assertEqual(b"\x28\x80\x01", calls[1])

  def test_stock_cruise_does_not_touch_radar(self):
    self.CP.openpilotLongitudinalControl = False
    with patch("opendbc.car.hyundai.interface.disable_ecu") as disable, \
         patch("opendbc.car.hyundai.interface._nexo_stock_scc_active") as stock_active, \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks") as enable:
      CarInterface.init(self.CP, self.can_recv, self.can_send)

    disable.assert_not_called()
    stock_active.assert_not_called()
    enable.assert_not_called()


if __name__ == "__main__":
  unittest.main()
