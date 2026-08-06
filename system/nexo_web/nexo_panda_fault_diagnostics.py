from __future__ import annotations

import json
import re
import time

from cereal import messaging


def _enum_name(value) -> str:
  text = str(value)
  return text.rsplit(".", 1)[-1] if text else "확인 불가"


def _fault_names(panda) -> list[str]:
  try:
    names = {_enum_name(fault) for fault in panda.faults}
  except Exception:
    return []
  return sorted(name for name in names if name not in ("none", "0", "확인 불가"))


def _sample_panda_faults(duration_s: float = 1.5) -> dict[str, object]:
  sm = messaging.SubMaster(["pandaStates"])
  deadline = time.monotonic() + duration_s
  seen = False
  latest: list[dict[str, object]] = []
  observed_by_panda: dict[int, set[str]] = {}

  while time.monotonic() < deadline:
    sm.update(100)
    if not sm.seen["pandaStates"]:
      continue

    seen = True
    latest = []
    for index, panda in enumerate(sm["pandaStates"]):
      current_faults = _fault_names(panda)
      observed_by_panda.setdefault(index, set()).update(current_faults)
      latest.append({
        "index": index,
        "safetyModel": _enum_name(panda.safetyModel),
        "safetyParam": int(panda.safetyParam),
        "controlsAllowed": bool(panda.controlsAllowed),
        "rxChecksInvalid": bool(panda.safetyRxChecksInvalid),
        "faultStatus": _enum_name(getattr(panda, "faultStatus", "확인 불가")),
        "currentFaults": current_faults,
      })

  for item in latest:
    index = int(item["index"])
    item["observedFaults"] = sorted(observed_by_panda.get(index, set()))

  observed_faults = sorted({fault for faults in observed_by_panda.values() for fault in faults})
  return {
    "durationSec": duration_s,
    "seen": seen,
    "pandas": latest,
    "observedFaults": observed_faults,
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
  """Prepend a read-only Panda fault-name report to the 8-second diagnostic file."""
  snapshot = _sample_panda_faults()
  pandas = snapshot["pandas"] if isinstance(snapshot.get("pandas"), list) else []
  observed = snapshot["observedFaults"] if isinstance(snapshot.get("observedFaults"), list) else []
  current = sorted({fault for item in pandas for fault in item.get("currentFaults", [])})
  rx_invalid = any(item.get("rxChecksInvalid") is True for item in pandas)

  if snapshot.get("seen") is not True:
    verdict = "[주행 금지] pandaStates를 수신하지 못해 Panda fault 상태를 확인할 수 없습니다."
  elif observed:
    verdict = f"[주행 금지] Panda fault 감지: {', '.join(observed)}"
  elif rx_invalid:
    verdict = "[주행 금지] Panda RX 안전검사가 invalid 상태입니다."
  else:
    verdict = "[정상 후보] 활성 Panda fault가 없고 RX 안전검사가 정상입니다."

  severe = snapshot.get("seen") is not True or bool(observed) or rx_invalid
  report = _synchronize_overall_verdict(report, verdict, severe)

  lines = [
    "============================================================",
    "Panda fault 전용 진단",
    "============================================================",
    f"판정: {verdict}",
    f"현재 fault 이름: {', '.join(current) if current else '없음'}",
    f"1.5초 동안 관측된 fault 이름: {', '.join(observed) if observed else '없음'}",
  ]

  if pandas:
    for item in pandas:
      lines.append(
        f"Panda {item['index']}: safety={item['safetyModel']}({item['safetyParam']}) | "
        f"controlsAllowed={item['controlsAllowed']} | rxInvalid={item['rxChecksInvalid']} | "
        f"faultStatus={item['faultStatus']} | currentFaults={item['currentFaults']} | "
        f"observedFaults={item['observedFaults']}"
      )
  else:
    lines.append("Panda 상태 행 없음")

  if "interruptRateCan2" in observed:
    lines.append("[핵심] interruptRateCan2가 확인됐습니다. CAN2 인터럽트 빈도 fault를 숨기거나 정상으로 처리하지 않습니다.")

  lines.extend([
    "※ fault 이름은 모든 Panda에서 모아 표시하며 순간적으로 나타났다 사라진 fault도 1.5초 관측 목록에 남깁니다.",
    "※ 이 검사는 읽기 전용이며 Panda 설정 변경·CAN 송신·fault 해제를 수행하지 않습니다.",
    "",
    "[Panda fault 기계 판독 JSON]",
    json.dumps(snapshot, ensure_ascii=False, indent=2),
    "",
  ])
  return "\n".join(lines) + report
