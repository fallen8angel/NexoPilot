from __future__ import annotations

import json
import re
from pathlib import Path


NEXO_GUARD_STATE_LOG = Path("/data/nexo_scc_guard_state.json")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
NEXO_SCC_NAMES = ("SCC11", "SCC12", "SCC13", "SCC14")


def _read_json(path: Path) -> dict[str, object]:
  try:
    payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    return payload if isinstance(payload, dict) else {}
  except (OSError, json.JSONDecodeError):
    return {}


def _boot_id() -> str:
  try:
    return BOOT_ID_PATH.read_text(encoding="utf-8", errors="replace").strip()
  except OSError:
    return ""


def _physical_source0_scc(report: str) -> int:
  """Count only physical src=0 SCC frames from the detailed CAN table.

  The old diagnostic reused the aggregate '순정 SCC' metric, which included
  every source below 128. On NEXO that can include src=1 SCC13 traffic and is
  therefore not equivalent to physical source0 stock-SCC traffic.
  """
  total = 0
  for name in NEXO_SCC_NAMES:
    pattern = rf"^src=\s*0\s+{name}\s+0x[0-9A-Fa-f]+\s+(\d+)회"
    total += sum(int(value) for value in re.findall(pattern, report, re.MULTILINE))
  return total


def _active_long_validation(report: str, source0_scc: int, fault: dict[str, object]) -> tuple[bool, bool, str]:
  """Validate an already-active longitudinal sample without changing vehicle state."""
  state_lines = [
    line for line in report.splitlines()
    if "selfdrive=" in line and "controlsAllowed=" in line and "carControl=" in line
  ]
  active_lines = [
    line for line in state_lines
    if re.search(r"selfdrive=[^\s]+/True/True(?:\s|$)", line)
  ]
  if not active_lines:
    return False, False, "미실시"

  healthy_sample = any(
    "controlsAllowed=True" in line
    and re.search(r"carControl=enabled:True/lat:(?:True|False)/long:True(?:\s|$)", line) is not None
    and "accFault=False" in line
    and "radarErrors=(False, False, False, False)" in line
    for line in active_lines
  )
  passed = healthy_sample and source0_scc == 0 and not fault
  if passed:
    return True, True, "통과"

  reasons: list[str] = []
  if not healthy_sample:
    reasons.append("active 상태의 controlsAllowed/longActive/ACC·Radar 조건 불일치")
  if source0_scc:
    reasons.append(f"물리 source0 SCC {source0_scc}회 관측")
  if fault:
    reasons.append("runtime guard fault 존재")
  return True, False, "확인 필요(" + ", ".join(reasons) + ")"


def runtime_guard_report(report: str) -> str:
  """Explain the NEXO stock-SCC runtime guard without changing vehicle state."""
  state = _read_json(NEXO_GUARD_STATE_LOG)
  current_boot = _boot_id()
  same_boot = bool(state) and bool(current_boot) and state.get("boot_id") == current_boot
  enabled = state.get("enabled") is True
  armed = state.get("armed") is True
  guard_state = str(state.get("state", "기록 없음"))
  fault = state.get("fault") if isinstance(state.get("fault"), dict) else {}

  p_gear = "gear=park" in report or "gear=park " in report
  inactive = "selfdrive=disabled/False/False" in report or "active=False" in report
  no_acc_fault = "첫 accFault 전환: 관측되지 않음" in report and "accFault=False" in report
  no_radar_error = "radarErrors=(False, False, False, False)" in report
  source0_scc = _physical_source0_scc(report)
  active_seen, active_pass, active_text = _active_long_validation(report, source0_scc, fault)

  if not state:
    verdict = "[확인 필요] runtime guard 상태 파일이 없습니다."
  elif not same_boot:
    verdict = "[확인 필요] runtime guard 기록이 현재 부팅에서 생성된 값이 아닙니다."
  elif fault or guard_state == "fault":
    verdict = "[주행 금지] runtime guard가 순정 SCC 복귀를 감지했습니다."
  elif active_seen and not active_pass:
    verdict = "[주행 금지] 실제 주행 활성 표본의 롱컨 안전 조건이 맞지 않습니다."
  elif enabled and armed:
    verdict = "[정상 후보] 현재 부팅에서 runtime guard가 활성화되어 있습니다."
  elif enabled and not armed:
    verdict = "[주행 금지] 롱컨이 설정됐지만 runtime guard가 무장되지 않았습니다."
  else:
    verdict = "[정보] 순정 크루즈 모드이거나 runtime guard가 필요하지 않은 상태입니다."

  stationary_pass = p_gear and inactive and no_acc_fault and no_radar_error and not fault
  stationary_text = "통과" if stationary_pass else "확인 필요"

  return "\n".join([
    "============================================================",
    "NEXO runtime guard·검사 단계 확인",
    "============================================================",
    f"판정: {verdict}",
    f"P단 정지 검사: {stationary_text} | 주행 활성 검사: {active_text}",
    f"guard state={guard_state} | enabled={enabled} | armed={armed} | 현재 부팅 기록={same_boot}",
    f"guard fault={json.dumps(fault, ensure_ascii=False) if fault else '없음'}",
    f"8초 물리 source0 SCC 관측={source0_scc}회",
    "※ 주행 활성 검사는 이미 수집된 selfdrive·Panda·CarControl·CAN 상태만 읽으며 제어를 활성화하거나 CAN을 송신하지 않습니다.",
    "※ 물리 source0 SCC는 상세 CAN 집계에서 src=0인 SCC11/12/13/14만 셉니다.",
    "※ src=1~127의 다른 버스 수신과 Panda TX echo(src>=128)는 source0 순정 SCC에 포함하지 않습니다.",
    "※ 실제 순정 SCC 복귀 여부는 card 제어 경로의 runtime guard fault 기록을 우선합니다.",
    "※ P단 정지 검사 또는 주행 활성 검사 통과는 도로 주행 안전을 보증하지 않습니다.",
  ])


def prepend_runtime_guard_report(report: str) -> str:
  return runtime_guard_report(report) + "\n\n" + report
