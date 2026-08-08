from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from pathlib import Path

from cereal import car, messaging


NEXO_SCC_TAKEOVER_MARKER = Path("/data/nexo_scc_takeover_active")
NEXO_SCC_RESTORE_LOG = Path("/data/nexo_scc_restore.log")
PROCESS_PATTERNS = {
  "manager": ("system.manager.manager", "manager.py"),
  "card": ("selfdrive.car.card", "/card", "./card"),
  "controlsd": ("controlsd",),
  "pandad": ("pandad",),
  "radard": ("radard",),
  "nexo_web": ("system.nexo_web.web", "nexo_web/web.py"),
}
FLOW_NAMES = ("SCC11", "SCC12", "SCC13", "SCC14", "FCA11", "FCA12", "FRT_RADAR11")
COMM_SERVICES = ("driverMonitoringState", "longitudinalPlan", "driverAssistance")
REAL_ERROR = re.compile(
  r"(unknownkeyname|traceback|exception|fatal|bus off|relay malfunction|commissue|"
  r"radar[^\n]*(?:fault|error|unavailable)|safety[^\n]*(?:invalid|fault|violation)|"
  r"(?:^|[^a-z])error(?:[^a-z]|$))",
  re.IGNORECASE,
)


def _text_param(core, key: str) -> str:
  try:
    value = core.Params().get(key)
    if value is None:
      return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
  except Exception:
    return ""


def _safe_attr(obj, name: str, default="확인 불가"):
  try:
    return getattr(obj, name)
  except Exception:
    return default


def _read_tail(path: Path, limit: int = 12000) -> str:
  try:
    return path.read_text(encoding="utf-8", errors="replace")[-limit:]
  except OSError:
    return ""


def _process_snapshot(core) -> dict[str, dict[str, object]]:
  code, output = core.run_command(["ps", "-eo", "pid,args"], timeout=3)
  lines = [] if code != 0 else [line.strip() for line in output.splitlines() if line.strip()]
  snapshot: dict[str, dict[str, object]] = {}
  for name, patterns in PROCESS_PATTERNS.items():
    matches = [line for line in lines if any(pattern in line for pattern in patterns)]
    snapshot[name] = {"running": bool(matches), "sample": matches[0] if matches else ""}
  if code != 0:
    snapshot["ps_error"] = {"running": False, "sample": output}
  return snapshot


def _heartbeat(core) -> tuple[str, float | None]:
  raw = _text_param(core, "NexoCardHeartbeatMono")
  try:
    age = max(0.0, time.monotonic() - float(raw))
    return f"{age:.1f}초 전", age
  except Exception:
    return "확인 불가", None


def _marker_snapshot() -> dict[str, object]:
  exists = NEXO_SCC_TAKEOVER_MARKER.exists()
  raw = _read_tail(NEXO_SCC_TAKEOVER_MARKER, 512).strip() if exists else ""
  stage = raw.split()[-1] if raw else "없음"

  if not exists:
    problem = False
    description = "마커 없음"
  elif stage == "longitudinal_takeover_ready":
    problem = False
    description = "롱컨 takeover 준비 완료 마커(현재 롱컨 세션에서는 정상)"
  elif stage == "restore_pending":
    problem = True
    description = "순정 SCC 복구 재시도 필요"
  elif stage == "stock_scc_disabled":
    problem = True
    description = "순정 SCC 중지 후 초기화가 완료되지 않은 상태"
  else:
    problem = True
    description = f"알 수 없는 마커 상태: {stage}"

  return {
    "exists": exists,
    "raw": raw,
    "stage": stage,
    "problem": problem,
    "description": description,
  }


def correct_legacy_wording(report: str) -> str:
  """Correct old text that treated blocked/inactive SCC traces as simultaneous control."""
  inactive = "selfdrive=disabled/False/False" in report or "controlsAllowed=False" in report
  if inactive:
    report = report.replace(
      "판정: 위험: 순정 SCC와 openpilot SCC가 동시에 관측됐습니다.",
      "판정: 정보: 크루즈 비활성 상태에서 순정 SCC와 Panda 송신/차단 흔적이 함께 보였습니다. 실제 동시 제어로 판정하지 않습니다.",
    )

  marker = _marker_snapshot()
  old_marker = "순정 SCC 복구 대기 마커: 있음 - 일반 크루즈 복구 필요"
  if old_marker in report:
    report = report.replace(old_marker, f"순정 SCC takeover 마커: {marker['description']}")
  return report


def _carparams_snapshot(core) -> dict[str, object]:
  result: dict[str, object] = {
    "available": False,
    "fingerprint": "없음",
    "brand": "확인 불가",
    "isNexo": False,
    "openpilotLongitudinalControl": None,
    "pcmCruise": None,
    "radarUnavailable": None,
    "sccBus": "필드 없음",
    "flags": "확인 불가",
    "safetyConfigs": [],
  }
  try:
    raw = core.Params().get("CarParams")
  except Exception as error:
    result["error"] = f"CarParams 읽기 실패: {error}"
    return result
  if not raw:
    result["error"] = "CarParams 없음"
    return result

  try:
    with car.CarParams.from_bytes(raw) as cp:
      fingerprint = str(cp.carFingerprint)
      configs = []
      for cfg in cp.safetyConfigs:
        configs.append({
          "model": str(cfg.safetyModel),
          "param": int(cfg.safetyParam),
        })
      result.update({
        "available": bool(fingerprint),
        "fingerprint": fingerprint,
        "brand": str(_safe_attr(cp, "brand")),
        "isNexo": "NEXO" in fingerprint.upper(),
        "openpilotLongitudinalControl": bool(cp.openpilotLongitudinalControl),
        "pcmCruise": bool(cp.pcmCruise),
        "radarUnavailable": bool(cp.radarUnavailable),
        "sccBus": _safe_attr(cp, "sccBus", "필드 없음"),
        "flags": int(cp.flags),
        "safetyConfigs": configs,
      })
  except Exception as error:
    result["error"] = f"CarParams 해석 실패: {error}"
  return result


def _service_snapshot() -> dict[str, dict[str, object]]:
  services = ["carState", "selfdriveState", "pandaStates", "radarState", *COMM_SERVICES]
  result: dict[str, dict[str, object]] = {
    name: {"seen": False, "alive": False, "valid": False, "ageMs": None} for name in services
  }
  try:
    sm = messaging.SubMaster(services)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
      sm.update(100)
      if all(sm.seen[name] for name in services):
        break

    now_ns = time.monotonic_ns()
    for name in services:
      mono = int(sm.logMonoTime[name])
      age_ms = round(max(0, now_ns - mono) / 1e6, 1) if mono else None
      result[name] = {
        "seen": bool(sm.seen[name]),
        "alive": bool(sm.alive[name]),
        "valid": bool(sm.valid[name]),
        "freqOk": bool(sm.freq_ok[name]),
        "ageMs": age_ms,
      }

    cs = sm["carState"]
    ss = sm["selfdriveState"]
    pandas = sm["pandaStates"]
    radar = sm["radarState"]
    panda = pandas[0] if len(pandas) else None
    result["carState"].update({
      "gear": str(cs.gearShifter),
      "vEgoKph": round(float(cs.vEgo) * 3.6, 1),
      "brakePressed": bool(cs.brakePressed),
      "gasPressed": bool(cs.gasPressed),
      "accFaulted": bool(cs.accFaulted),
      "cruiseAvailable": bool(cs.cruiseState.available),
      "cruiseEnabled": bool(cs.cruiseState.enabled),
    })
    result["selfdriveState"].update({
      "state": str(ss.state),
      "enabled": bool(ss.enabled),
      "active": bool(ss.active),
      "alert1": str(ss.alertText1),
      "alert2": str(ss.alertText2),
    })
    result["pandaStates"].update({
      "count": len(pandas),
      "controlsAllowed": bool(panda.controlsAllowed) if panda is not None else None,
      "safetyModel": str(panda.safetyModel) if panda is not None else None,
      "safetyParam": int(panda.safetyParam) if panda is not None else None,
      "rxChecksInvalid": bool(panda.safetyRxChecksInvalid) if panda is not None else None,
    })
    errors = radar.radarErrors
    result["radarState"].update({
      "canError": bool(errors.canError),
      "radarFault": bool(errors.radarFault),
      "wrongConfig": bool(errors.wrongConfig),
      "unavailableTemporary": bool(errors.radarUnavailableTemporary),
    })
  except Exception as error:
    result["snapshotError"] = {
      "seen": False, "alive": False, "valid": False, "ageMs": None, "error": str(error),
    }
  return result


def _section(report: str, heading: str) -> str:
  marker = f"[{heading}]"
  start = report.find(marker)
  if start < 0:
    return ""
  start += len(marker)
  next_heading = report.find("\n[", start)
  return report[start: next_heading if next_heading >= 0 else len(report)].strip()


def _metric(report: str, pattern: str) -> int:
  match = re.search(pattern, report)
  return int(match.group(1)) if match else 0


def _flow_snapshot(report: str) -> dict[str, dict[str, int]]:
  flow: dict[str, dict[str, int]] = {}
  for name in FLOW_NAMES:
    match = re.search(rf"^{name}\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$", report, re.MULTILINE)
    if match:
      flow[name] = {
        "requested": int(match.group(1)),
        "accepted": int(match.group(2)),
        "blocked": int(match.group(3)),
        "vehicle": int(match.group(4)),
      }
    else:
      flow[name] = {"requested": 0, "accepted": 0, "blocked": 0, "vehicle": 0}
  return flow


def _first_real_error(parts: Iterable[str], ignore_comm_issue: bool = False) -> str:
  for part in parts:
    for raw_line in part.splitlines():
      line = re.sub(r"\s+", " ", raw_line).strip()
      if not line or not REAL_ERROR.search(line):
        continue
      lowered = line.lower().replace(" ", "")
      if '"error":false' in lowered or "error=false" in lowered:
        continue
      if ignore_comm_issue and "commissue" in lowered:
        continue
      return line[:220]
  return "없음"


def _live_comm_status(services: dict[str, dict[str, object]]) -> tuple[bool, list[str]]:
  bad: list[str] = []
  for name in COMM_SERVICES:
    info = services.get(name, {})
    if info.get("seen") is not True or info.get("alive") is not True or info.get("valid") is not True or info.get("freqOk") is not True:
      bad.append(
        f"{name}(seen={info.get('seen')}, alive={info.get('alive')}, valid={info.get('valid')}, "
        f"freqOk={info.get('freqOk')}, age={info.get('ageMs')}ms)"
      )
  return not bad, bad


def _label(level: str, detail: str) -> str:
  return f"[{level}] {detail}"


def _yes_no(value) -> str:
  if value is True:
    return "예"
  if value is False:
    return "아니오"
  return "확인 불가"


def build_unified_report(core, report: str, duration: float = 8.0) -> str:
  """Prepend a compact, read-only diagnosis to the existing full 8-second report."""
  cp = _carparams_snapshot(core)
  processes = _process_snapshot(core)
  services = _service_snapshot()
  heartbeat_text, heartbeat_age = _heartbeat(core)
  flow = _flow_snapshot(report)
  marker = _marker_snapshot()

  stock_scc = _metric(report, r"순정 SCC:\s*(\d+)")
  op_scc = _metric(report, r"(?:openpilot SCC|Panda 송신 성공 SCC):\s*(\d+)")
  blocked_scc = _metric(report, r"Panda 차단 SCC:\s*(\d+)")
  stock_fca = _metric(report, r"순정 FCA:\s*(\d+)")
  op_fca = _metric(report, r"(?:openpilot FCA|Panda 송신 성공 FCA):\s*(\d+)")
  blocked_fca = _metric(report, r"Panda 차단 FCA:\s*(\d+)")
  radar_tracks = _metric(report, r"레이더 트랙 프레임:\s*(\d+)")

  active = bool(services.get("selfdriveState", {}).get("active")) or bool(re.search(r"selfdrive=[^\n]*/True(?:/|\s)", report))
  controls_allowed = services.get("pandaStates", {}).get("controlsAllowed") is True or "controlsAllowed=True" in report
  carstate_alive = bool(services.get("carState", {}).get("seen")) and bool(services.get("carState", {}).get("valid"))
  card_running = bool(processes.get("card", {}).get("running"))
  heartbeat_fresh = heartbeat_age is not None and heartbeat_age <= 3.0
  card_healthy = card_running and (heartbeat_fresh or carstate_alive)
  live_comm_ok, live_comm_bad = _live_comm_status(services)

  restore_log = _read_tail(NEXO_SCC_RESTORE_LOG)
  restore_ack = "acknowledged=True" in restore_log
  restore_last_line = next((line.strip() for line in reversed(restore_log.splitlines()) if line.strip()), "기록 없음")

  radar_info = services.get("radarState", {})
  radar_error = any(bool(radar_info.get(key)) for key in ("canError", "radarFault", "wrongConfig", "unavailableTemporary"))
  cs_info = services.get("carState", {})
  acc_fault = bool(cs_info.get("accFaulted")) or "accFault=True" in report
  rx_invalid = services.get("pandaStates", {}).get("rxChecksInvalid") is True or "rxInvalid=True" in report

  init_section = _section(report, "롱컨 초기화·UDS 추적")
  runtime_section = _section(report, "card 런타임 상태")
  crash_section = _section(report, "마지막 card crash traceback")
  error_section = _section(report, "핵심 오류 로그")
  current_crash = bool(crash_section and "현재 버전 기록 후보" in crash_section and "저장된 card Python crash" not in crash_section)
  first_error = _first_real_error(
    (runtime_section, init_section, current_crash and crash_section or "", error_section),
    ignore_comm_issue=live_comm_ok,
  )
  if not live_comm_ok:
    first_error = "현재 commIssue: " + "; ".join(live_comm_bad)[:190]

  uds_suppress_ok = "acknowledged=True" in init_section or "completed=True" in init_section
  radar_init_ok = "radar-track request completed=True" in init_section or ("RADAR ATTEMPT" in init_section and "completed" in init_section)
  scc12 = flow["SCC12"]
  long_enabled = cp.get("openpilotLongitudinalControl") is True

  problems: list[str] = []
  warnings: list[str] = []
  if not cp.get("available") or not cp.get("isNexo"):
    problems.append("차량이 HYUNDAI_NEXO_1ST_GEN으로 확인되지 않음")
  if not card_healthy:
    problems.append("card 프로세스와 carState/heartbeat가 모두 비정상")
  if not carstate_alive:
    problems.append("carState가 수신되지 않거나 유효하지 않음")
  if bool(marker.get("problem")):
    problems.append(str(marker.get("description")))
  if current_crash:
    problems.append("현재 버전 card crash 기록 존재")
  if radar_error:
    problems.append("radarState 오류 존재")
  if acc_fault:
    problems.append("ACC fault 관측")
  if rx_invalid:
    problems.append("Panda RX 안전검사 invalid")
  if active and controls_allowed and blocked_scc:
    problems.append("실제 제어 활성 중 SCC Panda 차단 관측")
  if not live_comm_ok:
    warnings.append("현재 핵심 서비스 통신 이상: " + "; ".join(live_comm_bad))

  # Missing legacy heartbeat Params is informational when the live card process
  # and a fresh, valid carState already prove the card loop is healthy.
  # Likewise, inactive stock/Panda SCC address traces are kept in the detailed
  # flow output below but must not downgrade the overall result to [주의].
  if not active and blocked_scc:
    warnings.append("P단·크루즈 비활성 상태의 Panda 차단은 정상일 수 있음")
  if long_enabled and not active and scc12["requested"] == 0:
    warnings.append("P단·크루즈 비활성 진단이라 SCC12 요청 0회는 정상일 수 있음")
  if long_enabled and radar_tracks == 0:
    warnings.append("레이더 트랙을 관측하지 못함")

  if problems:
    overall = _label("주행 금지", problems[0])
    action = "P단에서만 유지하고 상세 원문의 첫 오류와 복구 상태를 확인하세요."
  elif warnings:
    overall = _label("주의", "치명 오류는 없지만 일부 보조 진단값을 확인하지 못했습니다.")
    action = "이 파일 하나만 공유하면 됩니다. 실제 주행 전 계기판 경고가 없어야 합니다."
  else:
    overall = _label("정상 후보", "8초 P단 진단에서 치명 오류를 찾지 못했습니다.")
    action = "정적·P단 진단 결과이며 실제 도로 안전을 보증하지 않습니다."

  vehicle_status = _label("정상" if cp.get("isNexo") else "실패", f"{cp.get('fingerprint')} / brand={cp.get('brand')}")
  card_status = _label("정상" if card_healthy else "실패", f"process={card_running}, heartbeat={heartbeat_text}, carStateFresh={carstate_alive}")
  carstate_status = _label("정상" if carstate_alive else "실패", f"seen={services.get('carState', {}).get('seen')}, valid={services.get('carState', {}).get('valid')}, age={services.get('carState', {}).get('ageMs')}ms")
  radar_status = _label("정상" if radar_tracks > 0 and not radar_error else "주의", f"tracks={radar_tracks}, errors={{{', '.join(f'{k}={radar_info.get(k)}' for k in ('canError', 'radarFault', 'wrongConfig', 'unavailableTemporary'))}}}")
  panda_status = _label("실패" if active and controls_allowed and blocked_scc else "정보", f"active={active}, controlsAllowed={controls_allowed}, rxInvalid={rx_invalid}, SCC blocked={blocked_scc}")
  restore_status = _label("실패" if marker.get("problem") else "정상", str(marker.get("description")))
  comm_status = _label("정상" if live_comm_ok else "주의", "현재 핵심 서비스 모두 alive/valid/freqOk" if live_comm_ok else "; ".join(live_comm_bad))

  safety_configs = cp.get("safetyConfigs") or []
  safety_text = ", ".join(f"{item['model']}({item['param']})" for item in safety_configs) or "없음"
  process_text = " | ".join(f"{name}={'ON' if data.get('running') else 'OFF'}" for name, data in processes.items() if name != "ps_error")
  flow_text = " | ".join(
    f"{name} 요청{data['requested']}/성공{data['accepted']}/차단{data['blocked']}/차량{data['vehicle']}"
    for name, data in flow.items()
  )

  summary = [
    "============================================================",
    "NexoPilot 8초 통합진단 - 한눈에 보기",
    "============================================================",
    f"종합 판정 : {overall}",
    f"다음 조치 : {action}",
    "",
    f"1. 차량 인식 : {vehicle_status}",
    f"2. card 상태 : {card_status}",
    f"3. carState  : {carstate_status}",
    f"4. 레이더    : {radar_status}",
    f"5. Panda     : {panda_status}",
    f"6. 순정 복구 : {restore_status}",
    f"7. 현재 통신 : {comm_status}",
    f"8. 첫 오류   : {first_error}",
    "",
    "[차량·제어 설정]",
    f"Git={core.git_value('rev-parse', '--short', 'HEAD')} | Branch={core.git_value('rev-parse', '--abbrev-ref', 'HEAD')} | Dirty={core.git_value('status', '--porcelain') != ''}",
    f"openpilotLong={_yes_no(cp.get('openpilotLongitudinalControl'))} | pcmCruise={_yes_no(cp.get('pcmCruise'))} | radarUnavailable={_yes_no(cp.get('radarUnavailable'))} | sccBus={cp.get('sccBus')} | flags={cp.get('flags')}",
    f"Safety={safety_text}",
    f"gear={cs_info.get('gear', '확인 불가')} | speed={cs_info.get('vEgoKph', '확인 불가')}km/h | cruise available/enabled={cs_info.get('cruiseAvailable')}/{cs_info.get('cruiseEnabled')} | brake/gas={cs_info.get('brakePressed')}/{cs_info.get('gasPressed')}",
    "",
    "[8초 CAN 흐름]",
    f"SCC 전체: 차량수신={stock_scc} Panda송신흔적={op_scc} Panda차단={blocked_scc}",
    f"FCA 전체: 차량수신={stock_fca} Panda송신흔적={op_fca} Panda차단={blocked_fca}",
    f"SCC12 핵심: 요청={scc12['requested']} 성공={scc12['accepted']} 차단={scc12['blocked']} 차량수신={scc12['vehicle']}",
    f"레이더 트랙={radar_tracks} | UDS 순정SCC중지 후보={uds_suppress_ok} | 레이더설정 후보={radar_init_ok}",
    "※ controlsAllowed=False와 Panda 차단은 P단·크루즈 비활성 중에는 정상일 수 있습니다.",
    "※ 같은 주소가 여러 src에 보여도 버스·방향·차단 상태가 달라 실제 동시 제어를 뜻하지 않습니다.",
    "※ 과거 commIssue 로그는 현재 핵심 서비스가 모두 정상일 때 첫 오류에서 제외합니다.",
    "",
    f"[핵심 프로세스] {process_text}",
    f"[takeover 마커] stage={marker.get('stage')} raw={marker.get('raw') or '없음'}",
    f"[순정 SCC 복구 마지막 기록] {restore_last_line[:240]}",
    f"[메시지별 흐름] {flow_text}",
  ]
  if problems:
    summary.extend(["", "[실패 원인]", *(f"- {item}" for item in problems)])
  if warnings:
    summary.extend(["", "[주의 사항]", *(f"- {item}" for item in warnings)])

  machine = {
    "durationSec": duration,
    "overall": overall,
    "problems": problems,
    "warnings": warnings,
    "carParams": cp,
    "processes": processes,
    "services": services,
    "liveCommOk": live_comm_ok,
    "liveCommProblems": live_comm_bad,
    "flow": flow,
    "counts": {
      "stockScc": stock_scc,
      "openpilotScc": op_scc,
      "blockedScc": blocked_scc,
      "stockFca": stock_fca,
      "openpilotFca": op_fca,
      "blockedFca": blocked_fca,
      "radarTracks": radar_tracks,
    },
    "takeoverMarkerExists": marker.get("exists"),
    "takeoverMarkerStage": marker.get("stage"),
    "takeoverMarkerPending": marker.get("problem"),
    "restoreAcknowledgedInLog": restore_ack,
    "restoreLastLine": restore_last_line,
    "currentFailureReason": _text_param(core, "NexoCardSessionReason") or _text_param(core, "NexoLongitudinalFailure"),
    "currentCardStage": _text_param(core, "NexoCardStage"),
  }

  return "\n".join(summary) + "\n\n[기계 판독 JSON]\n" + json.dumps(machine, ensure_ascii=False, indent=2) + \
         "\n\n============================================================\n상세 원문 - 필요할 때만 아래를 확인하세요\n============================================================\n" + report