from __future__ import annotations

import json
from pathlib import Path
import re
import time

from cereal import messaging


ROOT = Path(__file__).resolve().parents[2]
PANDA_FW_STATUS = Path("/data/nexopilot/panda_fw/status.json")
PANDA_FW_READY = Path("/data/nexopilot/panda_fw/ready.json")
TRACKED_PANDA_VERSION = ROOT / "panda/board/obj/version"


def _enum_name(value) -> str:
  text = str(value)
  return text.rsplit(".", 1)[-1] if text else "확인 불가"


def _fault_names(panda) -> list[str]:
  try:
    names = {_enum_name(fault) for fault in panda.faults}
  except Exception:
    return []
  return sorted(name for name in names if name not in ("none", "0", "확인 불가"))


def _can_state_snapshot(state, index: int) -> dict[str, object]:
  fields = (
    "busOff", "busOffCnt", "errorWarning", "errorPassive",
    "lastError", "lastStoredError", "lastDataError", "lastDataStoredError",
    "receiveErrorCnt", "transmitErrorCnt", "totalErrorCnt", "totalTxLostCnt",
    "totalRxLostCnt", "totalTxCnt", "totalRxCnt", "totalFwdCnt",
    "canSpeed", "canDataSpeed", "canfdEnabled", "brsEnabled", "canfdNonIso",
    "irq0CallRate", "irq1CallRate", "canCoreResetCnt",
  )
  output: dict[str, object] = {"index": index}
  for field in fields:
    try:
      value = getattr(state, field)
      if field in ("lastError", "lastStoredError", "lastDataError", "lastDataStoredError"):
        output[field] = _enum_name(value)
      elif isinstance(value, bool):
        output[field] = bool(value)
      elif isinstance(value, float):
        output[field] = float(value)
      else:
        output[field] = int(value)
    except Exception:
      output[field] = "확인 불가"
  return output


def _panda_snapshot(panda, index: int) -> dict[str, object]:
  can_states = []
  for can_index in range(3):
    try:
      can_states.append(_can_state_snapshot(getattr(panda, f"canState{can_index}"), can_index))
    except Exception:
      can_states.append({"index": can_index, "error": "확인 불가"})

  return {
    "index": index,
    "safetyModel": _enum_name(panda.safetyModel),
    "safetyParam": int(panda.safetyParam),
    "controlsAllowed": bool(panda.controlsAllowed),
    "rxChecksInvalid": bool(panda.safetyRxChecksInvalid),
    "safetyTxBlocked": int(getattr(panda, "safetyTxBlocked", 0)),
    "safetyRxInvalid": int(getattr(panda, "safetyRxInvalid", 0)),
    "faultStatus": _enum_name(getattr(panda, "faultStatus", "확인 불가")),
    "interruptLoad": round(float(getattr(panda, "interruptLoad", 0.0)), 4),
    "rxBufferOverflow": int(getattr(panda, "rxBufferOverflow", 0)),
    "txBufferOverflow": int(getattr(panda, "txBufferOverflow", 0)),
    "currentFaults": _fault_names(panda),
    "canStates": can_states,
  }


def _counter_deltas(first: dict[str, object], latest: dict[str, object], elapsed_s: float) -> dict[str, object]:
  counters = (
    "totalErrorCnt", "totalTxLostCnt", "totalRxLostCnt",
    "totalTxCnt", "totalRxCnt", "totalFwdCnt", "canCoreResetCnt",
  )
  result: dict[str, object] = {}
  for field in counters:
    start = first.get(field)
    end = latest.get(field)
    if isinstance(start, int) and isinstance(end, int):
      delta = max(0, end - start)
      result[field] = delta
      if field in ("totalTxCnt", "totalRxCnt", "totalFwdCnt") and elapsed_s > 0:
        result[f"{field}PerSec"] = round(delta / elapsed_s, 1)
  return result


def _panda_counter_deltas(first: dict[str, object], latest: dict[str, object]) -> dict[str, int]:
  """Return only growth observed during this sample window.

  Panda buffer-overflow and safety counters are lifetime/cumulative values. A
  large absolute value alone does not prove that the fault is happening now.
  """
  counters = ("rxBufferOverflow", "txBufferOverflow", "safetyTxBlocked", "safetyRxInvalid")
  result: dict[str, int] = {}
  for field in counters:
    start = first.get(field)
    end = latest.get(field)
    if isinstance(start, int) and isinstance(end, int):
      result[field] = max(0, end - start)
  return result


def _firmware_status() -> dict[str, object]:
  result: dict[str, object] = {
    "readyMarker": PANDA_FW_READY.is_file(),
    "statusFile": PANDA_FW_STATUS.is_file(),
    "trackedRepoFirmwareVersion": "확인 불가",
  }
  try:
    result["trackedRepoFirmwareVersion"] = TRACKED_PANDA_VERSION.read_text(encoding="utf-8", errors="replace").strip()
  except OSError:
    pass

  try:
    status = json.loads(PANDA_FW_STATUS.read_text(encoding="utf-8"))
    if isinstance(status, dict):
      result.update(status)
  except Exception as error:
    result["statusReadError"] = str(error)
  return result


def _sample_panda_faults(duration_s: float = 1.5) -> dict[str, object]:
  sm = messaging.SubMaster(["pandaStates"])
  started = time.monotonic()
  deadline = started + duration_s
  seen = False
  latest: list[dict[str, object]] = []
  first_by_panda: dict[int, dict[str, object]] = {}
  observed_by_panda: dict[int, set[str]] = {}

  while time.monotonic() < deadline:
    sm.update(100)
    if not sm.seen["pandaStates"]:
      continue

    seen = True
    latest = []
    for index, panda in enumerate(sm["pandaStates"]):
      item = _panda_snapshot(panda, index)
      observed_by_panda.setdefault(index, set()).update(item["currentFaults"])
      if index not in first_by_panda:
        first_by_panda[index] = item
      latest.append(item)

  elapsed_s = max(0.001, time.monotonic() - started)
  for item in latest:
    index = int(item["index"])
    item["observedFaults"] = sorted(observed_by_panda.get(index, set()))
    first = first_by_panda.get(index, {})
    item["sampleDeltas"] = _panda_counter_deltas(first, item) if isinstance(first, dict) else {}
    first_can = first.get("canStates", []) if isinstance(first, dict) else []
    latest_can = item.get("canStates", [])
    if isinstance(first_can, list) and isinstance(latest_can, list):
      for can_index, can_state in enumerate(latest_can):
        if can_index < len(first_can) and isinstance(can_state, dict) and isinstance(first_can[can_index], dict):
          can_state["sampleDeltas"] = _counter_deltas(first_can[can_index], can_state, elapsed_s)

  observed_faults = sorted({fault for faults in observed_by_panda.values() for fault in faults})
  return {
    "durationSec": duration_s,
    "actualDurationSec": round(elapsed_s, 3),
    "seen": seen,
    "pandas": latest,
    "observedFaults": observed_faults,
    "firmware": _firmware_status(),
  }


def _synchronize_overall_verdict(report: str, verdict: str, severe: bool) -> str:
  if not severe:
    return report
  report = re.sub(r"^종합 판정\s*:.*$", f"종합 판정 : {verdict}", report, count=1, flags=re.MULTILINE)
  return re.sub(
    r"^다음 조치\s*:.*$",
    "다음 조치 : P단·주차브레이크·완전 정지를 유지하고 Panda fault 원인을 해결하기 전에는 주행하지 마세요.",
    report,
    count=1,
    flags=re.MULTILINE,
  )


def prepend_panda_fault_report(report: str) -> str:
  """Prepend a read-only Panda fault, IRQ-rate, and firmware provenance report."""
  snapshot = _sample_panda_faults()
  pandas = snapshot["pandas"] if isinstance(snapshot.get("pandas"), list) else []
  observed = snapshot["observedFaults"] if isinstance(snapshot.get("observedFaults"), list) else []
  current = sorted({fault for item in pandas for fault in item.get("currentFaults", [])})
  rx_invalid = any(item.get("rxChecksInvalid") is True for item in pandas)
  rx_overflow_growth = sum(int(item.get("sampleDeltas", {}).get("rxBufferOverflow", 0)) for item in pandas)
  tx_overflow_growth = sum(int(item.get("sampleDeltas", {}).get("txBufferOverflow", 0)) for item in pandas)

  if snapshot.get("seen") is not True:
    verdict = "[주행 금지] pandaStates를 수신하지 못해 Panda fault 상태를 확인할 수 없습니다."
  elif observed:
    verdict = f"[주행 금지] Panda fault 감지: {', '.join(observed)}"
  elif rx_invalid:
    verdict = "[주행 금지] Panda RX 안전검사가 invalid 상태입니다."
  elif rx_overflow_growth or tx_overflow_growth:
    verdict = f"[주의] 검사 중 Panda buffer overflow 증가 RX/TX={rx_overflow_growth}/{tx_overflow_growth}"
  else:
    verdict = "[정상 후보] 활성 Panda fault가 없고 RX 안전검사가 정상입니다."

  severe = snapshot.get("seen") is not True or bool(observed) or rx_invalid
  report = _synchronize_overall_verdict(report, verdict, severe)

  lines = [
    "============================================================",
    "Panda fault·CAN IRQ·펌웨어 진단",
    "============================================================",
    f"판정: {verdict}",
    f"현재 fault 이름: {', '.join(current) if current else '없음'}",
    f"1.5초 동안 관측된 fault 이름: {', '.join(observed) if observed else '없음'}",
  ]

  if pandas:
    for item in pandas:
      panda_deltas = item.get("sampleDeltas", {}) if isinstance(item.get("sampleDeltas"), dict) else {}
      lines.append(
        f"Panda {item['index']}: safety={item['safetyModel']}({item['safetyParam']}) | "
        f"controlsAllowed={item['controlsAllowed']} | rxInvalid={item['rxChecksInvalid']} | "
        f"faultStatus={item['faultStatus']} | interruptLoad={item['interruptLoad']} | "
        f"safetyTxBlocked={item['safetyTxBlocked']} | currentFaults={item['currentFaults']} | "
        f"observedFaults={item['observedFaults']}"
      )
      lines.append(
        f"  buffer overflow 누적 RX/TX={item.get('rxBufferOverflow', '확인 불가')}/{item.get('txBufferOverflow', '확인 불가')} | "
        f"1.5초 증가 RX/TX={panda_deltas.get('rxBufferOverflow', '확인 불가')}/{panda_deltas.get('txBufferOverflow', '확인 불가')}"
      )
      for can_state in item.get("canStates", []):
        if not isinstance(can_state, dict):
          continue
        lines.append(
          f"  CAN core {can_state.get('index')}: speed={can_state.get('canSpeed')} | "
          f"irq0/irq1={can_state.get('irq0CallRate')}/{can_state.get('irq1CallRate')} per sec | "
          f"busOff={can_state.get('busOff')} | warn/passive={can_state.get('errorWarning')}/{can_state.get('errorPassive')} | "
          f"REC/TEC={can_state.get('receiveErrorCnt')}/{can_state.get('transmitErrorCnt')} | "
          f"RX/TX/FWD delta={can_state.get('sampleDeltas', {}).get('totalRxCnt', '확인 불가')}/"
          f"{can_state.get('sampleDeltas', {}).get('totalTxCnt', '확인 불가')}/"
          f"{can_state.get('sampleDeltas', {}).get('totalFwdCnt', '확인 불가')}"
        )
  else:
    lines.append("Panda 상태 행 없음")

  firmware = snapshot.get("firmware", {})
  if isinstance(firmware, dict):
    lines.extend([
      "",
      "[Panda 펌웨어 출처]",
      f"준비 상태={firmware.get('state', '확인 불가')} | readyMarker={firmware.get('readyMarker', False)}",
      f"현재 safety source hash={str(firmware.get('sourceHash', '확인 불가'))[:16]}",
      f"준비된 펌웨어 버전={firmware.get('firmwareVersion', '확인 불가')}",
      f"저장소 prebuilt 펌웨어 버전={firmware.get('trackedRepoFirmwareVersion', '확인 불가')}",
      f"빌드 실패 이유={firmware.get('reason', '없음')}",
    ])

  if "interruptRateCan2" in observed:
    lines.append("[핵심] interruptRateCan2가 확인됐습니다. Panda의 FDCAN2 인터럽트 fault이며 위 CAN core 1의 irq/error/rate 값을 함께 확인하세요. fault를 숨기거나 임계값을 올리지 않습니다.")
  if rx_overflow_growth or tx_overflow_growth:
    lines.append("[주의] buffer overflow 누적 총값이 아니라 이번 1.5초 검사 중 실제 증가가 확인됐습니다. CAN 부하와 pandad 소비 지연을 함께 확인하세요.")

  lines.extend([
    "※ buffer overflow 절대값은 Panda 부팅 이후 누적값입니다. 현재 문제 여부는 1.5초 증가량(delta)을 우선해서 봅니다.",
    "※ fault 이름은 모든 Panda에서 모아 표시하며 순간적으로 나타났다 사라진 fault도 1.5초 관측 목록에 남깁니다.",
    "※ CAN core의 RX/TX/FWD delta는 이 1.5초 표본 안의 증가량이며 물리 버스 번호와 FDCAN core 번호는 하네스 방향에 따라 구분해서 봅니다.",
    "※ 이 검사는 읽기 전용이며 Panda 설정 변경·CAN 송신·fault 해제를 수행하지 않습니다.",
    "",
    "[Panda fault 기계 판독 JSON]",
    json.dumps(snapshot, ensure_ascii=False, indent=2),
    "",
  ])
  return "\n".join(lines) + report
