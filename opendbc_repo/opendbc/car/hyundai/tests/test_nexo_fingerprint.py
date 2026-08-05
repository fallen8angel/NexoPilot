#!/usr/bin/env python3
import unittest
from types import SimpleNamespace

from opendbc.car.fingerprints import eliminate_incompatible_cars
from opendbc.car.hyundai.fingerprints import FINGERPRINTS
from opendbc.car.hyundai.values import CAR


class TestNexoFingerprint(unittest.TestCase):
  def test_nexo_registered_for_can_fallback(self):
    self.assertIn(CAR.HYUNDAI_NEXO_1ST_GEN, FINGERPRINTS)
    self.assertEqual(len(FINGERPRINTS[CAR.HYUNDAI_NEXO_1ST_GEN]), 1)

  def test_proven_nexo_scc_and_fcev_messages_present(self):
    fp = FINGERPRINTS[CAR.HYUNDAI_NEXO_1ST_GEN][0]
    expected = {
      0x200: 6,   # FCEV powertrain status
      0x340: 8,   # LKAS11
      0x389: 8,   # SCC14
      0x38D: 8,   # FCA11
      0x420: 8,   # SCC11
      0x421: 8,   # SCC12
      0x483: 8,   # FCA12
      0x4F1: 4,   # CLU11 buttons
      0x50A: 8,   # SCC13
    }
    for addr, length in expected.items():
      self.assertEqual(fp.get(addr), length, hex(addr))

  def test_known_nexo_message_keeps_candidate(self):
    msg = SimpleNamespace(address=0x421, dat=b"\x00" * 8)
    self.assertEqual(
      eliminate_incompatible_cars(msg, [CAR.HYUNDAI_NEXO_1ST_GEN]),
      [CAR.HYUNDAI_NEXO_1ST_GEN],
    )

  def test_unknown_message_rejects_candidate(self):
    msg = SimpleNamespace(address=0x123, dat=b"\x00" * 8)
    self.assertEqual(eliminate_incompatible_cars(msg, [CAR.HYUNDAI_NEXO_1ST_GEN]), [])


if __name__ == "__main__":
  unittest.main()
