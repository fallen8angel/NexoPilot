import unittest
from types import SimpleNamespace

from opendbc.car.hyundai import hyundaican
from opendbc.car.hyundai.values import CAR


class TestNexoXPlusLongitudinalCapture(unittest.TestCase):
  class RecordingPacker:
    def __init__(self):
      self.messages = []

    def make_can_msg(self, name, bus, values):
      self.messages.append((name, values.copy()))
      return 0, bytes(8), bus

    def last(self, name):
      return next(values for msg_name, values in reversed(self.messages) if msg_name == name)

  def setUp(self):
    self.CP = SimpleNamespace(carFingerprint=CAR.HYUNDAI_NEXO_1ST_GEN, flags=0)
    self.hud = SimpleNamespace(
      lanesVisible=True,
      leadDistanceBars=3,
      leadVisible=True,
      leadDistance=42.5,
      leadRelSpeed=-1.5,
    )

  def create_commands(self, enabled, *, use_fca=False, accel=0.2,
                      stopping=False, long_override=False):
    packer = self.RecordingPacker()
    hyundaican.create_acc_commands(
      packer, enabled, accel, 3.0, 1, self.hud, 80, stopping, long_override,
      use_fca, self.CP, cruise_available=True, vehicle_cruise_enabled=False,
    )
    return packer

  def test_direct_longitudinal_command_set_matches_xplus_capture(self):
    packer = self.create_commands(enabled=True, use_fca=True)
    names = [name for name, _ in packer.messages]
    self.assertEqual(names.count("SCC11"), 1)
    # SCC12/FCA11 are packed once for checksum calculation and once for final TX.
    self.assertEqual(names.count("SCC12"), 2)
    self.assertEqual(names.count("SCC14"), 1)
    self.assertEqual(names.count("FCA11"), 2)

  def test_stopping_uses_stop_request_and_keeps_decel_value(self):
    packer = self.create_commands(enabled=True, accel=-0.507, stopping=True)
    scc12 = packer.last("SCC12")
    self.assertEqual(scc12["ACCMode"], 1)
    self.assertEqual(scc12["StopReq"], 1)
    self.assertEqual(scc12["aReqRaw"], 0.0)
    self.assertEqual(scc12["aReqValue"], -0.507)

  def test_stopping_bits_clear_when_longitudinal_is_inactive(self):
    packer = self.create_commands(enabled=False, accel=-0.507, stopping=True)
    scc12 = packer.last("SCC12")
    self.assertEqual(scc12["ACCMode"], 0)
    self.assertEqual(scc12["StopReq"], 0)
    self.assertEqual(scc12["aReqRaw"], 0.0)
    self.assertEqual(scc12["aReqValue"], 0.0)

  def test_driver_override_keeps_longitudinal_stream_active(self):
    packer = self.create_commands(enabled=True, accel=0.4, long_override=True)
    self.assertEqual(packer.last("SCC12")["ACCMode"], 2)
    self.assertEqual(packer.last("SCC14")["ACCMode"], 2)

  def test_fca_status_heartbeat_matches_current_xplus(self):
    command_packer = self.create_commands(enabled=True, use_fca=True)
    self.assertEqual(command_packer.last("FCA11")["FCA_Status"], 0)

    option_packer = self.RecordingPacker()
    hyundaican.create_acc_opt(option_packer, self.CP)
    self.assertEqual(option_packer.last("FCA12")["FCA_USM"], 1)
    self.assertEqual(option_packer.last("FCA12")["FCA_DrvSetState"], 2)


if __name__ == "__main__":
  unittest.main()
