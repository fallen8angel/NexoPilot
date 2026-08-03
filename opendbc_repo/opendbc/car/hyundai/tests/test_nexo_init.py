import unittest
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.car.hyundai.interface import CarInterface, nexo_stock_scc_is_silent
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

    def verify(*args, **kwargs):
      calls.append("verify")
      return True

    with patch("opendbc.car.hyundai.interface.disable_ecu", side_effect=disable), \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks", side_effect=enable), \
         patch("opendbc.car.hyundai.interface.nexo_stock_scc_is_silent", side_effect=verify):
      CarInterface.init(self.CP, object(), object())

    self.assertEqual(["disable", "enable", "disable", "verify"], calls)

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

  def test_post_track_silence_failure_retries_and_restores_stock_scc(self):
    calls = []

    def disable(*args, **kwargs):
      calls.append(kwargs["com_cont_req"])
      return True

    with patch("opendbc.car.hyundai.interface.disable_ecu", side_effect=disable), \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks", return_value=True), \
         patch("opendbc.car.hyundai.interface.nexo_stock_scc_is_silent", return_value=False) as verify:
      with self.assertRaisesRegex(RuntimeError, "remained active after radar track activation"):
        CarInterface.init(self.CP, object(), object())

    self.assertEqual([b"\x28\x83\x01"] * 4 + [b"\x28\x80\x01"], calls)
    self.assertEqual(3, verify.call_count)

  def test_silence_check_requires_live_bus_without_stock_scc(self):
    clock = iter((0.0, 0.1, 0.2, 0.3, 0.4))
    unrelated_message = SimpleNamespace(src=0, address=0x100)

    with patch("opendbc.car.hyundai.interface.time.monotonic", side_effect=lambda: next(clock)):
      self.assertTrue(nexo_stock_scc_is_silent(lambda wait_for_one: [[unrelated_message]], 0))

  def test_silence_check_rejects_continuing_stock_scc(self):
    now = 0.0

    def monotonic():
      nonlocal now
      current = now
      now += 0.05
      return current

    stock_scc12 = SimpleNamespace(src=0, address=0x421)
    with patch("opendbc.car.hyundai.interface.time.monotonic", side_effect=monotonic):
      self.assertFalse(nexo_stock_scc_is_silent(lambda wait_for_one: [[stock_scc12]], 0,
                                                quiet_time=0.2, timeout=0.5))

  def test_stock_cruise_does_not_touch_radar(self):
    self.CP.openpilotLongitudinalControl = False
    with patch("opendbc.car.hyundai.interface.disable_ecu") as disable, \
         patch("opendbc.car.hyundai.interface.enable_radar_tracks") as enable:
      CarInterface.init(self.CP, object(), object())

    disable.assert_not_called()
    enable.assert_not_called()


if __name__ == "__main__":
  unittest.main()
