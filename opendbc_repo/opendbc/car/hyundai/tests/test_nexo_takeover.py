import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.car.hyundai import nexo_takeover


class StepClock:
  def __init__(self, step: float = 0.1):
    self.value = 0.0
    self.step = step

  def __call__(self) -> float:
    self.value += self.step
    return self.value


class TestNexoTakeoverVerification(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.state_path = Path(self.temporary.name) / "state.json"
    self.sent = []
    self.can_send = lambda messages: self.sent.extend(messages)

  def tearDown(self):
    self.temporary.cleanup()

  @staticmethod
  def _receiver(messages):
    def recv(wait_for_one=False):
      del wait_for_one
      return [[SimpleNamespace(address=address, src=source, dat=b"\x00" * 8)
               for address, source in messages]]
    return recv

  @staticmethod
  def _silent_observation(frames: int = 100):
    return {
      "source0_frames": frames,
      "source0_scc_total": 0,
      "source0_scc_counts": {},
    }

  def test_verified_when_bus_is_alive_and_source0_scc_is_silent(self):
    recv = self._receiver([(0x200, 0), (0x251, 0), (0x386, 0)])
    with patch.object(nexo_takeover, "NEXO_TAKEOVER_VERIFY_LOG", self.state_path), \
         patch.object(nexo_takeover, "claim_owner", return_value="123:456"), \
         patch.object(nexo_takeover, "_wait_for_exclusive_card_process", return_value=(True, [], 0.0)), \
         patch.object(nexo_takeover, "disable_ecu", return_value=True):
      result = nexo_takeover.ensure_nexo_stock_scc_silent(
        recv, self.can_send, bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
        trace=lambda _: None, attempts=1, sample_s=0.01, settle_s=0.0, min_source0_frames=1,
        exclusive_wait_s=0.0, stability_observation_s=0.0,
      )
    self.assertTrue(result)
    state = self.state_path.read_text(encoding="utf-8")
    self.assertIn('"success": true', state)
    self.assertIn('"owner": "123:456"', state)
    self.assertIn('"exclusiveCardProcess": true', state)

  def test_fails_closed_when_source0_scc_remains(self):
    recv = self._receiver([(0x200, 0), (0x420, 0), (0x421, 0)])
    with patch.object(nexo_takeover, "NEXO_TAKEOVER_VERIFY_LOG", self.state_path), \
         patch.object(nexo_takeover, "claim_owner", return_value="123:456"), \
         patch.object(nexo_takeover, "_wait_for_exclusive_card_process", return_value=(True, [], 0.0)), \
         patch.object(nexo_takeover, "disable_ecu", return_value=True):
      result = nexo_takeover.ensure_nexo_stock_scc_silent(
        recv, self.can_send, bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
        trace=lambda _: None, attempts=1, sample_s=0.01, settle_s=0.0, min_source0_frames=1,
        exclusive_wait_s=0.0, stability_observation_s=0.0,
      )
    self.assertFalse(result)
    self.assertIn('"state": "failed"', self.state_path.read_text(encoding="utf-8"))

  def test_fails_closed_when_source0_fca11_remains(self):
    recv = self._receiver([(0x200, 0), (0x38D, 0), (0x251, 0)])
    with patch.object(nexo_takeover, "NEXO_TAKEOVER_VERIFY_LOG", self.state_path), \
         patch.object(nexo_takeover, "claim_owner", return_value="123:456"), \
         patch.object(nexo_takeover, "_wait_for_exclusive_card_process", return_value=(True, [], 0.0)), \
         patch.object(nexo_takeover, "disable_ecu", return_value=True):
      result = nexo_takeover.ensure_nexo_stock_scc_silent(
        recv, self.can_send, bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
        trace=lambda _: None, attempts=1, sample_s=0.01, settle_s=0.0, min_source0_frames=1,
        exclusive_wait_s=0.0, stability_observation_s=0.0,
      )
    self.assertFalse(result)
    state = self.state_path.read_text(encoding="utf-8")
    self.assertIn('"state": "failed"', state)
    self.assertIn("0x38D", state)

  def test_fails_closed_when_takeover_owner_cannot_be_claimed(self):
    recv = self._receiver([(0x200, 0), (0x251, 0), (0x386, 0)])
    with patch.object(nexo_takeover, "NEXO_TAKEOVER_VERIFY_LOG", self.state_path), \
         patch.object(nexo_takeover, "claim_owner", return_value=""), \
         patch.object(nexo_takeover, "disable_ecu") as disable:
      result = nexo_takeover.ensure_nexo_stock_scc_silent(
        recv, self.can_send, bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
        trace=lambda _: None, attempts=1, sample_s=0.01, settle_s=0.0, min_source0_frames=1,
        exclusive_wait_s=0.0, stability_observation_s=0.0,
      )
    self.assertFalse(result)
    disable.assert_not_called()
    self.assertIn("takeover owner claim failed", self.state_path.read_text(encoding="utf-8"))

  def test_fails_before_suppression_when_old_card_process_remains(self):
    recv = self._receiver([(0x200, 0)])
    with patch.object(nexo_takeover, "NEXO_TAKEOVER_VERIFY_LOG", self.state_path), \
         patch.object(nexo_takeover, "claim_owner", return_value="123:456"), \
         patch.object(nexo_takeover, "_wait_for_exclusive_card_process",
                      return_value=(False, ["77:88"], 8.0)), \
         patch.object(nexo_takeover, "disable_ecu") as disable:
      result = nexo_takeover.ensure_nexo_stock_scc_silent(
        recv, self.can_send, bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
        trace=lambda _: None, exclusive_wait_s=0.0, stability_observation_s=0.0,
      )
    self.assertFalse(result)
    disable.assert_not_called()
    state = self.state_path.read_text(encoding="utf-8")
    self.assertIn("other card processes remained", state)
    self.assertIn("77:88", state)

  def test_reasserts_when_scc_returns_during_stability_window(self):
    recv = self._receiver([])
    relapse = {
      "source0_frames": 100,
      "source0_scc_total": 4,
      "source0_scc_counts": {"0x420": 2, "0x421": 2},
    }
    observations = [
      self._silent_observation(),
      relapse,
      self._silent_observation(),
    ]
    clock = StepClock()
    with patch.object(nexo_takeover, "NEXO_TAKEOVER_VERIFY_LOG", self.state_path), \
         patch.object(nexo_takeover, "claim_owner", return_value="123:456"), \
         patch.object(nexo_takeover, "_wait_for_exclusive_card_process", return_value=(True, [], 0.0)), \
         patch.object(nexo_takeover, "disable_ecu", return_value=True) as disable, \
         patch.object(nexo_takeover, "_observe_source0_scc", side_effect=observations), \
         patch.object(nexo_takeover.time, "monotonic", side_effect=clock), \
         patch.object(nexo_takeover.time, "sleep", return_value=None):
      result = nexo_takeover.ensure_nexo_stock_scc_silent(
        recv, self.can_send, bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
        trace=lambda _: None, attempts=1, sample_s=0.01, settle_s=0.0, min_source0_frames=1,
        exclusive_wait_s=0.0, stability_observation_s=0.5, stability_quiet_s=0.0,
        stability_sample_s=0.1, stability_timeout_s=2.0, stability_reassertions=1,
      )
    self.assertTrue(result)
    self.assertEqual(2, disable.call_count)
    state = self.state_path.read_text(encoding="utf-8")
    self.assertIn('"reassertions": 1', state)
    self.assertIn('"state": "verified"', state)

  def test_process_scan_finds_old_card_process(self):
    proc_root = Path(self.temporary.name) / "proc"
    old = proc_root / "77"
    unrelated = proc_root / "88"
    old.mkdir(parents=True)
    unrelated.mkdir(parents=True)
    old.joinpath("cmdline").write_bytes(b"python3\x00-m\x00selfdrive.car.card\x00")
    unrelated.joinpath("cmdline").write_bytes(b"python3\x00other.module\x00")
    old.joinpath("stat").write_text("77 (selfdrive.car.card) S " + " ".join(str(i) for i in range(4, 25)),
                                    encoding="utf-8")
    unrelated.joinpath("stat").write_text("88 (other) S " + " ".join(str(i) for i in range(4, 25)),
                                          encoding="utf-8")
    with patch.object(nexo_takeover.os, "getpid", return_value=99):
      tokens = nexo_takeover._other_card_process_tokens(proc_root)
    self.assertEqual(["77:22"], tokens)


if __name__ == "__main__":
  unittest.main()
