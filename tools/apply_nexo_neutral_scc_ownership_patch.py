#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(relative: str, old: str, new: str) -> None:
  path = ROOT / relative
  text = path.read_text(encoding="utf-8")
  if new in text:
    return
  if old not in text:
    raise RuntimeError(f"patch anchor not found: {relative}\n{old[:200]}")
  path.write_text(text.replace(old, new, 1), encoding="utf-8")


def write(relative: str, content: str) -> None:
  path = ROOT / relative
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(content, encoding="utf-8")


TAKEOVER_HELPER = '''from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from opendbc.car.disable_ecu import disable_ecu


NEXO_STOCK_SCC_ADDRS = frozenset((0x389, 0x420, 0x421, 0x50A))
NEXO_TAKEOVER_VERIFY_LOG = Path("/data/nexo_scc_takeover_verification.json")


def _boot_id() -> str:
  try:
    return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8", errors="replace").strip()
  except OSError:
    return ""


def _write_state(payload: dict[str, object]) -> None:
  try:
    output = {
      "wall_time": time.time(),
      "monotonic": time.monotonic(),
      "boot_id": _boot_id(),
      **payload,
    }
    temporary = NEXO_TAKEOVER_VERIFY_LOG.with_suffix(".tmp")
    temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(NEXO_TAKEOVER_VERIFY_LOG)
  except OSError:
    pass


def _observe_source0_scc(can_recv, duration_s: float) -> dict[str, object]:
  deadline = time.monotonic() + duration_s
  source0_frames = 0
  source0_scc: Counter[int] = Counter()

  while time.monotonic() < deadline:
    batches = can_recv(wait_for_one=True)
    for batch in batches:
      for message in batch:
        source = int(getattr(message, "src", -1))
        address = int(getattr(message, "address", -1))
        if source != 0:
          continue
        source0_frames += 1
        if address in NEXO_STOCK_SCC_ADDRS:
          source0_scc[address] += 1

  return {
    "source0_frames": source0_frames,
    "source0_scc_total": sum(source0_scc.values()),
    "source0_scc_counts": {f"0x{address:03X}": count for address, count in sorted(source0_scc.items())},
  }


def ensure_nexo_stock_scc_silent(can_recv, can_send, *, bus: int, addr: int,
                                  communication_control: bytes, trace: Callable[[str], None],
                                  attempts: int = 3, sample_s: float = 0.35,
                                  settle_s: float = 0.05, min_source0_frames: int = 20) -> bool:
  """Re-assert communication control after radar programming and prove SCC silence.

  NEXOdriveAI was observed with no physical source-0 SCC11/12/13/14 after
  takeover. Radar programming changes the diagnostic session on the same 0x7D0
  ECU, so communication control is re-issued after the DID write and verified
  against live CAN before Panda is switched into Hyundai longitudinal safety.
  """
  attempt_records: list[dict[str, object]] = []

  for attempt in range(1, attempts + 1):
    try:
      can_recv(wait_for_one=False)  # discard frames queued before this attempt
    except Exception:
      pass

    started = time.monotonic()
    try:
      acknowledged = bool(disable_ecu(can_recv, can_send, bus=bus, addr=addr,
                                      com_cont_req=communication_control))
      detail = ""
    except Exception as error:
      acknowledged = False
      detail = f"{type(error).__name__}: {error}"

    try:
      can_recv(wait_for_one=False)  # discard traffic captured before the ACK
    except Exception:
      pass
    if settle_s > 0:
      time.sleep(settle_s)

    observation = _observe_source0_scc(can_recv, sample_s)
    enough_bus_data = int(observation["source0_frames"]) >= min_source0_frames
    silent = int(observation["source0_scc_total"]) == 0
    success = acknowledged and enough_bus_data and silent
    record = {
      "attempt": attempt,
      "acknowledged": acknowledged,
      "elapsed_ms": round((time.monotonic() - started) * 1000.0, 1),
      "detail": detail,
      "enough_bus_data": enough_bus_data,
      "success": success,
      **observation,
    }
    attempt_records.append(record)
    trace(
      f"STEP 3 attempt={attempt}/{attempts} re-suppress acknowledged={acknowledged} "
      f"source0_frames={observation['source0_frames']} source0_scc={observation['source0_scc_total']} "
      f"counts={observation['source0_scc_counts']} success={success}"
    )
    _write_state({"state": "verified" if success else "checking", "success": success,
                  "attempts": attempt_records})
    if success:
      return True

  _write_state({"state": "failed", "success": False, "attempts": attempt_records})
  return False
'''

AI_PARITY_DIAGNOSTICS = '''from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from cereal import messaging


NEXO_STOCK_SCC_ADDRS = frozenset((0x389, 0x420, 0x421, 0x50A))
NEXO_TAKEOVER_VERIFY_LOG = Path("/data/nexo_scc_takeover_verification.json")
TESTER_PRESENT = bytes((0x02, 0x3E, 0x80, 0, 0, 0, 0, 0))


def _boot_id() -> str:
  try:
    return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8", errors="replace").strip()
  except OSError:
    return ""


def _state() -> dict[str, object]:
  try:
    return json.loads(NEXO_TAKEOVER_VERIFY_LOG.read_text(encoding="utf-8", errors="replace"))
  except (OSError, json.JSONDecodeError):
    return {}


def _sample_live(duration_s: float = 1.15) -> dict[str, object]:
  can_sock = messaging.sub_sock("can", conflate=False, timeout=20)
  sendcan_sock = messaging.sub_sock("sendcan", conflate=False, timeout=20)
  requested = 0
  accepted = 0
  blocked = 0
  source0_scc: Counter[int] = Counter()
  deadline = time.monotonic() + duration_s

  while time.monotonic() < deadline:
    for event in messaging.drain_sock(sendcan_sock):
      for frame in getattr(event, "sendcan", ()):
        if int(frame.address) == 0x7D0 and int(frame.src) == 0 and bytes(frame.dat) == TESTER_PRESENT:
          requested += 1

    for event in messaging.drain_sock(can_sock):
      for frame in event.can:
        source = int(frame.src)
        address = int(frame.address)
        payload = bytes(frame.dat)
        if address == 0x7D0 and payload == TESTER_PRESENT:
          if source == 128:
            accepted += 1
          elif source == 192:
            blocked += 1
        if source == 0 and address in NEXO_STOCK_SCC_ADDRS:
          source0_scc[address] += 1
    time.sleep(0.005)

  return {
    "duration_s": duration_s,
    "tester_requested": requested,
    "tester_accepted": accepted,
    "tester_blocked": blocked,
    "source0_scc_total": sum(source0_scc.values()),
    "source0_scc_counts": {f"0x{address:03X}": count for address, count in sorted(source0_scc.items())},
  }


def prepend_ai_parity_report(core, report: str) -> str:
  state = _state()
  current_boot = _boot_id()
  state_current = bool(current_boot and state.get("boot_id") == current_boot)
  verified = bool(state_current and state.get("success"))
  live = _sample_live()
  source0_scc = int(live["source0_scc_total"])
  tester_requested = int(live["tester_requested"])
  tester_accepted = int(live["tester_accepted"])
  tester_blocked = int(live["tester_blocked"])

  if source0_scc:
    verdict = "[주행 금지] AI 정상 기준과 달리 물리 source0 순정 SCC가 다시 관측됐습니다."
  elif not verified:
    verdict = "[주행 금지] 현재 부팅에서 순정 SCC 중지 검증 성공 기록이 없습니다."
  elif tester_blocked:
    verdict = "[주행 금지] 0x7D0 Tester Present가 Panda에서 차단됐습니다."
  elif tester_accepted:
    verdict = "[정상 후보] 순정 SCC 중지 검증과 Tester Present 통과가 확인됐습니다."
  else:
    verdict = "[주의] 순정 SCC는 보이지 않지만 1.15초 창에서 Tester Present 통과를 잡지 못했습니다."

  attempts = state.get("attempts", []) if isinstance(state.get("attempts", []), list) else []
  last_attempt = attempts[-1] if attempts else {}
  lines = [
    "============================================================",
    "AI 실차 기준·NEXO SCC 인계 확인",
    "============================================================",
    f"판정: {verdict}",
    f"현재 부팅 검증 기록={state_current} | 검증 성공={verified} | state={state.get('state', '기록 없음')}",
    f"마지막 검증: attempt={last_attempt.get('attempt', '없음')} ack={last_attempt.get('acknowledged', '확인 불가')} "
    f"source0_frames={last_attempt.get('source0_frames', '확인 불가')} source0_scc={last_attempt.get('source0_scc_total', '확인 불가')}",
    f"1.15초 실시간: source0 SCC={source0_scc}회 {live['source0_scc_counts']}",
    f"Tester Present(0x7D0): 요청={tester_requested} 통과={tester_accepted} 차단={tester_blocked}",
    "※ AI 실차 정상 기록에서는 takeover 이후 source0 SCC11/12/13/14가 사라지고 Panda 송신만 관측됐습니다.",
    "※ 이 검사는 읽기 전용입니다. 기존 8초 수집 뒤 1.15초 동안 CAN·sendcan을 추가로 구독합니다.",
    "※ 계기판 SCC·FCA·ADAS 경고등이 실제로 켜져 있으면 판정과 무관하게 주행하지 마세요.",
    "",
  ]
  return "\n".join(lines) + report
'''

TAKEOVER_TEST = '''import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.car.hyundai import nexo_takeover


class TestNexoTakeoverVerification(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.state_path = Path(self.temporary.name) / "state.json"
    self.can_send = object()

  def tearDown(self):
    self.temporary.cleanup()

  @staticmethod
  def _receiver(messages):
    def recv(wait_for_one=False):
      return [[SimpleNamespace(address=address, src=source, dat=b"\x00" * 8)
               for address, source in messages]]
    return recv

  def test_verified_when_bus_is_alive_and_source0_scc_is_silent(self):
    recv = self._receiver([(0x200, 0), (0x251, 0), (0x386, 0)])
    with patch.object(nexo_takeover, "NEXO_TAKEOVER_VERIFY_LOG", self.state_path), \
         patch.object(nexo_takeover, "disable_ecu", return_value=True):
      result = nexo_takeover.ensure_nexo_stock_scc_silent(
        recv, self.can_send, bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
        trace=lambda _: None, attempts=1, sample_s=0.01, settle_s=0.0, min_source0_frames=1,
      )
    self.assertTrue(result)
    self.assertIn('"success": true', self.state_path.read_text(encoding="utf-8"))

  def test_fails_closed_when_source0_scc_remains(self):
    recv = self._receiver([(0x200, 0), (0x420, 0), (0x421, 0)])
    with patch.object(nexo_takeover, "NEXO_TAKEOVER_VERIFY_LOG", self.state_path), \
         patch.object(nexo_takeover, "disable_ecu", return_value=True):
      result = nexo_takeover.ensure_nexo_stock_scc_silent(
        recv, self.can_send, bus=0, addr=0x7D0, communication_control=b"\x28\x83\x01",
        trace=lambda _: None, attempts=1, sample_s=0.01, settle_s=0.0, min_source0_frames=1,
      )
    self.assertFalse(result)
    self.assertIn('"state": "failed"', self.state_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
  unittest.main()
'''

NEUTRAL_SAFETY_TEST = '''import unittest

from opendbc.car.hyundai.values import HyundaiSafetyFlags
from opendbc.car.structs import CarParams
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py


class TestHyundaiNexoNeutralSccOwnership(unittest.TestCase):
  SCC_ADDRS = (0x389, 0x420, 0x421, 0x50A)
  TIMEOUT_US = 400_000

  def setUp(self):
    self.packer = CANPackerSafety("hyundai_can_generated")
    self.safety = libsafety_py.libsafety
    param = HyundaiSafetyFlags.FCEV_GAS | HyundaiSafetyFlags.LONG | HyundaiSafetyFlags.NEXO_DYNAMIC_SCC
    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, param)
    self.safety.init_tests()
    self.safety.set_timer(1_000_000)

  def _scc12(self, accel: float, acc_mode: int = 0):
    values = {
      "ACCMode": acc_mode,
      "aReqRaw": accel,
      "aReqValue": accel,
      "AEB_CmdAct": 0,
      "CR_VSM_DecCmd": 0,
    }
    return self.packer.make_can_msg_safety("SCC12", 0, values)

  def test_neutral_scc12_claims_ownership_with_controls_off(self):
    self.safety.set_controls_allowed(False)
    self.assertTrue(self.safety.safety_tx_hook(self._scc12(0.0, 0)))
    for address in self.SCC_ADDRS:
      self.assertEqual(self.safety.safety_fwd_hook(2, address), -1)

  def test_nonzero_scc12_remains_blocked_with_controls_off(self):
    self.safety.set_controls_allowed(False)
    self.assertFalse(self.safety.safety_tx_hook(self._scc12(0.1, 1)))
    for address in self.SCC_ADDRS:
      self.assertEqual(self.safety.safety_fwd_hook(2, address), 0)

  def test_neutral_ownership_times_out(self):
    self.safety.set_controls_allowed(False)
    self.assertTrue(self.safety.safety_tx_hook(self._scc12(0.0, 0)))
    self.safety.set_timer(1_000_000 + self.TIMEOUT_US)
    for address in self.SCC_ADDRS:
      self.assertEqual(self.safety.safety_fwd_hook(2, address), 0)


if __name__ == "__main__":
  unittest.main()
'''

STATIC_CHECK = '''#!/usr/bin/env python3
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


interface = text("opendbc_repo/opendbc/car/hyundai/interface.py")
takeover = text("opendbc_repo/opendbc/car/hyundai/nexo_takeover.py")
web = text("system/nexo_web/web.py")
diagnostics = text("system/nexo_web/nexo_ai_parity_diagnostics.py")
workflow = text(".github/workflows/nexo-validation.yml")

assert "ensure_nexo_stock_scc_silent" in interface
assert 'RuntimeError("NEXO stock SCC remained active")' in interface
assert "attempts: int = 3" in takeover
assert "source0_scc_total" in takeover
assert "disable_ecu" in takeover
assert "prepend_ai_parity_report" in web
assert 'NexoPilotWeb/7.7' in web
assert "StableThreadingHTTPServer" in web
assert "allow_reuse_address = True" in web
assert "daemon_threads = True" in web
assert "request_queue_size = 32" in web
assert "messaging.sub_sock(\"can\"" in diagnostics
assert "messaging.sub_sock(\"sendcan\"" in diagnostics
assert "PubMaster" not in diagnostics
assert "pub_sock" not in diagnostics
assert "TestHyundaiNexoNeutralSccOwnership" in text("opendbc_repo/opendbc/safety/tests/test_hyundai_nexo_neutral.py")
assert "TestNexoTakeoverVerification" in text("opendbc_repo/opendbc/car/hyundai/tests/test_nexo_takeover.py")
assert "Check NEXO verified SCC takeover and AI parity diagnostics" in workflow
assert "Test NEXO post-radar SCC silence verification" in workflow
assert "Test NEXO neutral SCC ownership" in workflow

for path in (
  "opendbc_repo/opendbc/car/hyundai/nexo_takeover.py",
  "system/nexo_web/nexo_ai_parity_diagnostics.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_nexo_takeover.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_nexo_neutral.py",
):
  ast.parse(text(path), filename=path)

print("NEXO verified SCC takeover and AI parity diagnostics: OK")
'''

write("opendbc_repo/opendbc/car/hyundai/nexo_takeover.py", TAKEOVER_HELPER)
write("system/nexo_web/nexo_ai_parity_diagnostics.py", AI_PARITY_DIAGNOSTICS)
write("opendbc_repo/opendbc/car/hyundai/tests/test_nexo_takeover.py", TAKEOVER_TEST)
write("opendbc_repo/opendbc/safety/tests/test_hyundai_nexo_neutral.py", NEUTRAL_SAFETY_TEST)
write("tools/check_nexo_ai_parity.py", STATIC_CHECK)

replace_once(
  "opendbc_repo/opendbc/car/hyundai/interface.py",
  "from opendbc.car.hyundai.radar_tracks import enable_radar_tracks\n",
  "from opendbc.car.hyundai.radar_tracks import enable_radar_tracks\nfrom opendbc.car.hyundai.nexo_takeover import ensure_nexo_stock_scc_silent\n",
)
replace_once(
  "opendbc_repo/opendbc/car/hyundai/interface.py",
  '''          if not tracks_enabled:\n            raise RuntimeError("NEXO radar track activation failed")\n''',
  '''          if not tracks_enabled:\n            raise RuntimeError("NEXO radar track activation failed")\n\n          _trace_nexo_long_init("STEP 3 re-suppress stock SCC after radar DID write and verify physical source0 silence")\n          stock_scc_silent = ensure_nexo_stock_scc_silent(\n            can_recv, can_send, bus=bus, addr=addr, communication_control=communication_control,\n            trace=_trace_nexo_long_init, attempts=3,\n          )\n          _trace_nexo_long_init(f"STEP 3 verified source0 SCC silence={stock_scc_silent}")\n          if not stock_scc_silent:\n            raise RuntimeError("NEXO stock SCC remained active")\n''',
)

replace_once(
  "opendbc_repo/opendbc/car/hyundai/tests/test_nexo_init.py",
  '''    with patch.object(interface, "disable_ecu", side_effect=disable), \\\n         patch.object(interface, "enable_radar_tracks", return_value=True) as radar_enable:\n      CarInterface.init(self.CP, self.can_recv, self.can_send)\n\n    self.assertEqual([b"\\x28\\x83\\x01"], calls)\n    self.assertEqual(40, radar_enable.call_args.kwargs["retries"])\n''',
  '''    with patch.object(interface, "disable_ecu", side_effect=disable), \\\n         patch.object(interface, "enable_radar_tracks", return_value=True) as radar_enable, \\\n         patch.object(interface, "ensure_nexo_stock_scc_silent", return_value=True) as verify_silence:\n      CarInterface.init(self.CP, self.can_recv, self.can_send)\n\n    self.assertEqual([b"\\x28\\x83\\x01"], calls)\n    self.assertEqual(40, radar_enable.call_args.kwargs["retries"])\n    verify_silence.assert_called_once()\n''',
)
replace_once(
  "opendbc_repo/opendbc/car/hyundai/tests/test_nexo_init.py",
  '''  def test_radar_failure_restores_stock_before_raising(self):\n''',
  '''  def test_source0_scc_verification_failure_restores_stock_before_raising(self):\n    calls = []\n\n    def disable(*args, **kwargs):\n      calls.append(kwargs["com_cont_req"])\n      return True\n\n    with patch.object(interface, "disable_ecu", side_effect=disable), \\\n         patch.object(interface, "enable_radar_tracks", return_value=True), \\\n         patch.object(interface, "ensure_nexo_stock_scc_silent", return_value=False):\n      with self.assertRaisesRegex(RuntimeError, "stock SCC remained active"):\n        CarInterface.init(self.CP, self.can_recv, self.can_send)\n\n    self.assertEqual([b"\\x28\\x83\\x01", b"\\x28\\x80\\x01"], calls)\n    self.assertFalse(interface.nexo_stock_scc_restore_pending())\n\n  def test_radar_failure_restores_stock_before_raising(self):\n''',
)

replace_once(
  "system/nexo_web/web.py",
  "from system.nexo_web import nexo_cluster_warning_policy as warning_policy\n",
  "from system.nexo_web import nexo_cluster_warning_policy as warning_policy\nfrom system.nexo_web import nexo_ai_parity_diagnostics as ai_parity_diagnostics\n",
)
replace_once(
  "system/nexo_web/web.py",
  '''  warning_report = warning_diagnostics.prepend_cluster_warning_report(core, guard_report)\n  return warning_policy.correct_stationary_cluster_warning(warning_report)\n''',
  '''  warning_report = warning_diagnostics.prepend_cluster_warning_report(core, guard_report)\n  stationary_corrected_report = warning_policy.correct_stationary_cluster_warning(warning_report)\n  return ai_parity_diagnostics.prepend_ai_parity_report(core, stationary_corrected_report)\n''',
)
replace_once(
  "system/nexo_web/web.py",
  'core.Handler.server_version = "NexoPilotWeb/7.6"',
  'core.Handler.server_version = "NexoPilotWeb/7.7"',
)

replace_once(
  ".github/workflows/nexo-validation.yml",
  '''      - name: Check NEXO diagnostics\n        run: python tools/check_nexo_diagnostics.py\n''',
  '''      - name: Check NEXO diagnostics\n        run: python tools/check_nexo_diagnostics.py\n\n      - name: Check NEXO verified SCC takeover and AI parity diagnostics\n        run: python tools/check_nexo_ai_parity.py\n''',
)
replace_once(
  ".github/workflows/nexo-validation.yml",
  '''      - name: Test NEXO Panda SCC ownership\n        working-directory: opendbc_repo\n        run: python -m unittest opendbc.safety.tests.test_hyundai.TestHyundaiNexoDynamicSCCOwnership\n''',
  '''      - name: Test NEXO Panda SCC ownership\n        working-directory: opendbc_repo\n        run: python -m unittest opendbc.safety.tests.test_hyundai.TestHyundaiNexoDynamicSCCOwnership\n\n      - name: Test NEXO neutral SCC ownership\n        working-directory: opendbc_repo\n        run: python -m unittest opendbc.safety.tests.test_hyundai_nexo_neutral\n\n      - name: Test NEXO post-radar SCC silence verification\n        working-directory: opendbc_repo\n        run: python -m unittest opendbc.car.hyundai.tests.test_nexo_takeover\n''',
)

# Remove the one-shot patch mechanism from the resulting branch.
(ROOT / "tools/apply_nexo_neutral_scc_ownership_patch.py").unlink(missing_ok=True)
(ROOT / ".github/workflows/nexo-neutral-scc-ownership-patch.yml").unlink(missing_ok=True)
print("NEXO verified SCC takeover patch applied")
