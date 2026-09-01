from __future__ import annotations

import time
from collections.abc import Iterable

from cereal import messaging
from opendbc.can.dbc import DBC as CANDBC
from opendbc.can.parser import get_raw_value
from opendbc.car import Bus
from opendbc.car.hyundai.values import CAR, DBC as HYUNDAI_DBC


# Signals that can explain an SCC/FCA/MDPS/ESC warning. These are diagnostic
# observations only; this module never sends CAN, changes Params, or touches ECU state.
WARNING_SIGNALS = {
  "SCC11": ("MainMode_ACC", "SCCInfoDisplay", "ObjValid", "ACC_ObjStatus"),
  "SCC12": ("ACCMode", "ACCFailInfo", "TakeOverReq", "AEB_CmdAct", "CF_VSM_Warn", "CF_VSM_DecCmdAct"),
  "SCC13": ("SCCDrvModeRValue", "SCC_Equip", "Lead_Veh_Dep_Alert_USM"),
  "SCC14": ("ACCMode", "ObjGap", "JerkUpperLimit", "JerkLowerLimit"),
  "FCA11": ("FCA_Status", "CF_VSM_Warn", "FCA_CmdAct", "CF_VSM_DecCmdAct"),
  "FCA12": ("FCA_USM", "FCA_DrvSetState"),
  "MDPS12": ("CF_Mdps_ToiUnavail", "CF_Mdps_ToiFlt", "CF_Mdps_ToiActive"),
  "TCS13": ("ACCEnable", "ACC_REQ", "DriverOverride", "PBRAKE_ACT"),
  "TCS11": ("TCS_PAS", "ABS_ACT"),
  "LKAS11": ("CF_Lkas_LdwsSysState", "CF_Lkas_SysWarning"),
}
KNOWN_MESSAGE_ADDRS = {
  0x389: "SCC14",
  0x38D: "FCA11",
  0x420: "SCC11",
  0x421: "SCC12",
  0x483: "FCA12",
  0x50A: "SCC13",
}
CRITICAL_EVENT_FLAGS = ("immediateDisable", "softDisable", "noEntry")
EVENT_FLAGS = (
  "immediateDisable", "softDisable", "noEntry", "userDisable", "warning",
  "permanent", "preEnable", "enable", "overrideLateral", "overrideLongitudinal",
)
_DBC = None


def _dbc():
  global _DBC
  if _DBC is None:
    _DBC = CANDBC(HYUNDAI_DBC[CAR.HYUNDAI_NEXO_1ST_GEN][Bus.pt])
  return _DBC


def _safe_bool(obj, name: str) -> bool:
  try:
    return bool(getattr(obj, name))
  except Exception:
    return False


def _safe_int(obj, name: str) -> int | None:
  try:
    return int(getattr(obj, name))
  except Exception:
    return None


def _message_addresses() -> dict[int, str]:
  addresses = dict(KNOWN_MESSAGE_ADDRS)
  try:
    for address, message in _dbc().addr_to_msg.items():
      name = str(getattr(message, "name", ""))
      if name in WARNING_SIGNALS:
        addresses[int(address)] = name
  except Exception:
    pass
  return addresses


def _decode(address: int, data: bytes, message_name: str) -> dict[str, int | float]:
  values: dict[str, int | float] = {}
  try:
    message = _dbc().addr_to_msg.get(address)
    if message is None:
      return values
    for signal_name in WARNING_SIGNALS.get(message_name, ()):
      signal = message.sigs.get(signal_name)
      if signal is None:
        continue
      raw = get_raw_value(data, signal)
      if signal.is_signed:
        raw -= ((raw >> (signal.size - 1)) & 1) * (1 << signal.size)
      value = raw * signal.factor + signal.offset
      values[signal_name] = int(value) if float(value).is_integer() else round(float(value), 3)
  except Exception:
    pass
  return values


def _event_snapshot(events: Iterable[object]) -> list[dict[str, object]]:
  output: list[dict[str, object]] = []
  for event in events:
    try:
      name = str(event.name)
    except Exception:
      name = "unknown"
    flags = [flag for flag in EVENT_FLAGS if _safe_bool(event, flag)]
    output.append({"name": name, "flags": flags})
  return output


def _collect_snapshot(duration: float = 0.7) -> dict[str, object]:
  services = ["carState", "selfdriveState", "pandaStates", "radarState", "onroadEvents"]
  sm = messaging.SubMaster(services)
  can_sock = messaging.sub_sock("can", timeout=20)
  targets = _message_addresses()
  latest: dict[tuple[int, str], dict[str, int | float]] = {}
  frame_stats: dict[tuple[int, str], dict[str, object]] = {}
  started = time.monotonic()

  while time.monotonic() - started < duration:
    sm.update(20)
    event = messaging.recv_one_or_none(can_sock)
    if event is not None:
      for frame in event.can:
        address = int(frame.address)
        source = int(frame.src)
        message_name = targets.get(address)
        if message_name is None or source >= 192:
          continue
        raw = bytes(frame.dat)
        decoded = _decode(address, raw, message_name)
        if decoded:
          latest[(source, message_name)] = decoded
        key = (source, message_name)
        stats = frame_stats.setdefault(key, {"count": 0, "first": raw.hex(" "), "last": "", "payloads": set()})
        stats["count"] = int(stats["count"]) + 1
        stats["last"] = raw.hex(" ")
        payloads = stats["payloads"]
        if isinstance(payloads, set) and len(payloads) < 32:
          payloads.add(raw)
    time.sleep(0.005)

  result: dict[str, object] = {
    "services": {name: {
      "seen": bool(sm.seen[name]),
      "alive": bool(sm.alive[name]),
      "valid": bool(sm.valid[name]),
    } for name in services},
    "can": latest,
    "frameStats": frame_stats,
  }

  try:
    cs = sm["carState"]
    result["carState"] = {
      "accFaulted": _safe_bool(cs, "accFaulted"),
      "steerFaultTemporary": _safe_bool(cs, "steerFaultTemporary"),
      "steerFaultPermanent": _safe_bool(cs, "steerFaultPermanent"),
      "espDisabled": _safe_bool(cs, "espDisabled"),
      "stockFcw": _safe_bool(cs, "stockFcw"),
      "stockAeb": _safe_bool(cs, "stockAeb"),
      "parkingBrake": _safe_bool(cs, "parkingBrake"),
      "doorOpen": _safe_bool(cs, "doorOpen"),
      "seatbeltUnlatched": _safe_bool(cs, "seatbeltUnlatched"),
      "canErrorCounter": _safe_int(cs, "canErrorCounter"),
      "gear": str(cs.gearShifter),
      "vEgo": float(cs.vEgo),
      "cruiseAvailable": bool(cs.cruiseState.available),
      "cruiseEnabled": bool(cs.cruiseState.enabled),
    }
  except Exception as error:
    result["carStateError"] = str(error)

  try:
    ss = sm["selfdriveState"]
    result["selfdriveState"] = {
      "state": str(ss.state),
      "enabled": bool(ss.enabled),
      "active": bool(ss.active),
      "alertText1": str(ss.alertText1),
      "alertText2": str(ss.alertText2),
      "alertType": str(ss.alertType),
    }
  except Exception as error:
    result["selfdriveStateError"] = str(error)

  try:
    pandas = sm["pandaStates"]
    panda = pandas[0] if len(pandas) else None
    faults = [] if panda is None else [str(fault) for fault in panda.faults]
    result["panda"] = {
      "count": len(pandas),
      "controlsAllowed": bool(panda.controlsAllowed) if panda is not None else None,
      "safetyRxChecksInvalid": bool(panda.safetyRxChecksInvalid) if panda is not None else None,
      "faults": faults,
    }
  except Exception as error:
    result["pandaError"] = str(error)

  try:
    radar = sm["radarState"].radarErrors
    result["radar"] = {
      "canError": bool(radar.canError),
      "radarFault": bool(radar.radarFault),
      "wrongConfig": bool(radar.wrongConfig),
      "unavailableTemporary": bool(radar.radarUnavailableTemporary),
    }
  except Exception as error:
    result["radarError"] = str(error)

  try:
    result["events"] = _event_snapshot(sm["onroadEvents"])
  except Exception as error:
    result["eventsError"] = str(error)
    result["events"] = []

  return result


def _signal_for_sources(snapshot: dict[str, object], message: str, signal: str, sources: Iterable[int]):
  can = snapshot.get("can")
  if not isinstance(can, dict):
    return None
  for source in sources:
    values = can.get((source, message))
    if isinstance(values, dict) and signal in values:
      return values[signal]
  return None


def _vehicle_signal(snapshot: dict[str, object], message: str, signal: str):
  return _signal_for_sources(snapshot, message, signal, (0, 1, 2, 128, 129, 130, 131))


def _frame_summary(snapshot: dict[str, object], message: str) -> str:
  stats = snapshot.get("frameStats")
  if not isinstance(stats, dict):
    return "수집 없음"
  parts = []
  for (source, name), values in sorted(stats.items(), key=lambda item: item[0][0]):
    if name != message or not isinstance(values, dict):
      continue
    payloads = values.get("payloads")
    unique = len(payloads) if isinstance(payloads, set) else 0
    parts.append(
      f"src={source} count={values.get('count', 0)} unique={unique} "
      f"last={values.get('last', '')}"
    )
  return " | ".join(parts) or "수집 없음"


def _render_can(snapshot: dict[str, object]) -> list[str]:
  can = snapshot.get("can")
  if not isinstance(can, dict) or not can:
    return ["경고 관련 CAN 신호를 수집하지 못했습니다."]
  lines = []
  for (source, message_name), values in sorted(can.items(), key=lambda item: (item[0][0], item[0][1])):
    rendered = ", ".join(f"{name}={value}" for name, value in values.items())
    lines.append(f"src={source:3d} {message_name}: {rendered}")
  return lines


def cluster_warning_report(core, report: str) -> str:
  """Create a read-only warning-light cause report from live state and CAN."""
  del core  # Kept in the signature so web diagnostics modules share one interface.
  snapshot = _collect_snapshot()
  car_state = snapshot.get("carState") if isinstance(snapshot.get("carState"), dict) else {}
  selfdrive = snapshot.get("selfdriveState") if isinstance(snapshot.get("selfdriveState"), dict) else {}
  panda = snapshot.get("panda") if isinstance(snapshot.get("panda"), dict) else {}
  radar = snapshot.get("radar") if isinstance(snapshot.get("radar"), dict) else {}
  events = snapshot.get("events") if isinstance(snapshot.get("events"), list) else []

  critical: list[str] = []
  caution: list[str] = []
  normal_context: list[str] = []

  gear = str(car_state.get("gear", "")).rsplit(".", 1)[-1].lower()
  try:
    stationary = abs(float(car_state.get("vEgo", 0.0))) < 0.05
  except (TypeError, ValueError):
    stationary = False
  expected_stationary_events = {
    "wrongGear", "seatbeltNotLatched", "parkBrake", "wrongCarMode",
    "pcmDisable", "preEnableStandstill", "locationdTemporaryError",
  }

  if car_state.get("accFaulted") is True:
    critical.append("carState.accFaulted=True")
  if car_state.get("steerFaultPermanent") is True:
    critical.append("조향장치 영구 오류")
  if car_state.get("steerFaultTemporary") is True:
    caution.append("조향장치 일시 오류")
  if car_state.get("espDisabled") is True:
    caution.append("ESC/TCS 비활성")
  if car_state.get("stockFcw") is True:
    caution.append("순정 전방충돌 경고 감지")
  if car_state.get("stockAeb") is True:
    caution.append("순정 긴급제동 작동 감지")

  radar_faults = [name for name in ("canError", "radarFault", "wrongConfig") if radar.get(name) is True]
  if radar_faults:
    critical.append("레이더 오류: " + ", ".join(radar_faults))
  if radar.get("unavailableTemporary") is True:
    caution.append("레이더 일시 사용 불가")

  if panda.get("safetyRxChecksInvalid") is True:
    critical.append("Panda RX 안전검사 invalid")
  panda_faults = panda.get("faults") if isinstance(panda.get("faults"), list) else []
  if panda_faults:
    critical.append("Panda fault: " + ", ".join(str(item) for item in panda_faults))

  event_lines = []
  for event in events:
    if not isinstance(event, dict):
      continue
    name = str(event.get("name", "unknown"))
    flags = event.get("flags") if isinstance(event.get("flags"), list) else []
    event_lines.append(f"{name}({','.join(str(flag) for flag in flags) or 'active'})")
    expected_context = stationary and gear in ("park", "reverse") and name in expected_stationary_events
    if expected_context:
      normal_context.append(f"onroadEvent {name}: {','.join(str(flag) for flag in flags)}")
    elif any(flag in CRITICAL_EVENT_FLAGS for flag in flags):
      critical.append(f"onroadEvent {name}: {','.join(str(flag) for flag in flags)}")
    elif flags:
      caution.append(f"onroadEvent {name}: {','.join(str(flag) for flag in flags)}")

  alert_text = " / ".join(text for text in (
    str(selfdrive.get("alertText1", "")).strip(),
    str(selfdrive.get("alertText2", "")).strip(),
  ) if text)
  alert_type = str(selfdrive.get("alertType", ""))
  expected_reverse_alert = gear == "reverse" and (
    alert_type.startswith("reverseGear") or alert_text.strip().lower() == "reverse"
  )
  if alert_text and expected_reverse_alert:
    normal_context.append("화면 경고: Reverse (후진 중 정상 안내)")
  elif alert_text:
    caution.append("화면 경고: " + alert_text)

  acc_fail = _vehicle_signal(snapshot, "SCC12", "ACCFailInfo")
  takeover_req = _vehicle_signal(snapshot, "SCC12", "TakeOverReq")
  fca_warn = _vehicle_signal(snapshot, "FCA11", "CF_VSM_Warn")
  # Never mix physical stock FCA state with Panda's accepted openpilot echo.
  # Both can legitimately coexist during a snapshot and have different values.
  stock_fca_status = _signal_for_sources(snapshot, "FCA11", "FCA_Status", (0,))
  stock_fca_usm = _signal_for_sources(snapshot, "FCA12", "FCA_USM", (0,))
  op_fca_status = _signal_for_sources(snapshot, "FCA11", "FCA_Status", range(128, 192))
  op_fca_usm = _signal_for_sources(snapshot, "FCA12", "FCA_USM", range(128, 192))
  op_fca_driver_state = _signal_for_sources(snapshot, "FCA12", "FCA_DrvSetState", range(128, 192))
  mdps_fault = _vehicle_signal(snapshot, "MDPS12", "CF_Mdps_ToiFlt")
  mdps_unavailable = _vehicle_signal(snapshot, "MDPS12", "CF_Mdps_ToiUnavail")
  if isinstance(acc_fail, (int, float)) and acc_fail != 0:
    critical.append(f"SCC12 ACCFailInfo={acc_fail}")
  if isinstance(mdps_fault, (int, float)) and mdps_fault != 0:
    critical.append(f"MDPS12 CF_Mdps_ToiFlt={mdps_fault}")
  if isinstance(mdps_unavailable, (int, float)) and mdps_unavailable != 0:
    caution.append(f"MDPS12 CF_Mdps_ToiUnavail={mdps_unavailable}")
  if isinstance(takeover_req, (int, float)) and takeover_req != 0:
    caution.append(f"SCC12 TakeOverReq={takeover_req}")
  if isinstance(fca_warn, (int, float)) and fca_warn != 0:
    caution.append(f"FCA11 CF_VSM_Warn={fca_warn}")
  if isinstance(op_fca_status, (int, float)) and op_fca_status == 0 and op_fca_usm not in (None, 1):
    caution.append(
      f"openpilot FCA 상태 조합 불일치: FCA_Status=0인데 FCA_USM={op_fca_usm} (현행 XPlus 기준은 0/1)"
    )
  if op_fca_status is None and isinstance(op_fca_usm, (int, float)):
    caution.append("openpilot FCA11 상태 스트림 없음: FCA12만 송신되어 계기판 통신 단절 경고가 켜질 수 있음")
  if isinstance(op_fca_usm, (int, float)) and op_fca_usm != 1:
    caution.append(f"openpilot FCA12 FCA_USM={op_fca_usm}: 현행 XPlus 기준값 1과 불일치")
  if isinstance(op_fca_driver_state, (int, float)) and op_fca_driver_state != 2:
    caution.append(f"openpilot FCA12 FCA_DrvSetState={op_fca_driver_state}: 운전자 FCA 설정 상태 불일치")

  can_snapshot = snapshot.get("can") if isinstance(snapshot.get("can"), dict) else {}
  stock_fca_present = any(
    source == 0 and message in ("FCA11", "FCA12")
    for source, message in can_snapshot
  )
  openpilot_fca11_present = any(
    128 <= source < 192 and message == "FCA11"
    for source, message in can_snapshot
  )
  openpilot_fca12_present = any(
    128 <= source < 192 and message == "FCA12"
    for source, message in can_snapshot
  )
  xplus_state_match = (openpilot_fca11_present and openpilot_fca12_present and
                       op_fca_status == 0 and op_fca_usm == 1 and op_fca_driver_state == 2)

  # Deduplicate while preserving the first and therefore most useful observation.
  critical = list(dict.fromkeys(critical))
  caution = list(dict.fromkeys(caution))

  if critical:
    verdict = "[주행 금지] 경고등 원인으로 볼 수 있는 치명 신호가 감지됐습니다."
  elif caution:
    verdict = "[주의] 계기판 경고와 관련될 수 있는 신호가 감지됐습니다."
  else:
    verdict = "[원인 미검출] 소프트웨어·CAN에서 확정 가능한 경고 원인을 찾지 못했습니다."

  services = snapshot.get("services") if isinstance(snapshot.get("services"), dict) else {}
  service_line = " | ".join(
    f"{name}={value.get('seen')}/{value.get('alive')}/{value.get('valid')}"
    for name, value in services.items() if isinstance(value, dict)
  ) or "확인 불가"

  lines = [
    "============================================================",
    "계기판 경고등·ADAS 경고 확인",
    "============================================================",
    f"판정: {verdict}",
    f"현재 화면 경고: {alert_text or '없음'} | alertType={selfdrive.get('alertType', '확인 불가')}",
    f"활성 onroadEvents: {' | '.join(event_lines) if event_lines else '없음'}",
    f"차량 상태: accFaulted={car_state.get('accFaulted')} steerTemp/Permanent={car_state.get('steerFaultTemporary')}/{car_state.get('steerFaultPermanent')} espDisabled={car_state.get('espDisabled')} stockFcw/Aeb={car_state.get('stockFcw')}/{car_state.get('stockAeb')}",
    f"Panda: rxInvalid={panda.get('safetyRxChecksInvalid')} faults={panda_faults or '없음'} controlsAllowed={panda.get('controlsAllowed')}",
    f"Radar: canError={radar.get('canError')} radarFault={radar.get('radarFault')} wrongConfig={radar.get('wrongConfig')} temporary={radar.get('unavailableTemporary')}",
    f"메시지 수신 seen/alive/valid: {service_line}",
    "",
    "[계기판 경고 원인 후보]",
    *(f"- 치명: {item}" for item in critical),
    *(f"- 주의: {item}" for item in caution),
    *(("- CAN에서 원인 후보를 찾지 못했습니다.",) if not critical and not caution else ()),
    *(("", "[현재 기어·정지 상태에서 정상으로 제외한 항목]", *(f"- 정보: {item}" for item in normal_context)) if normal_context else ()),
    "",
    "[XPlus식 NEXO 롱컨 FCA 상태 확인]",
    f"현행 XPlus openpilot 상태조합 일치={xplus_state_match} (FCA_Status={op_fca_status}, FCA_USM={op_fca_usm}, FCA_DrvSetState={op_fca_driver_state})",
    f"openpilot FCA11/FCA12 상태 스트림 송신={openpilot_fca11_present}/{openpilot_fca12_present}",
    f"물리 source0 순정 FCA 잔존={stock_fca_present}",
    f"물리 source0 순정 상태(참고용): FCA_Status={stock_fca_status}, FCA_USM={stock_fca_usm}",
    f"FCA11 프레임: {_frame_summary(snapshot, 'FCA11')}",
    f"FCA12 프레임: {_frame_summary(snapshot, 'FCA12')}",
    ("판정: 현행 XPlus의 NEXO 상태 조합과 일치합니다. 완전 전원 재시작 후 계기판 경고 소등을 확인하세요."
     if xplus_state_match else
     "판정: FCA11 단절 또는 FCA11/FCA12 상태 불일치입니다. 위 프레임과 상태값을 확인하세요."),
    "",
    "[경고 관련 CAN 스냅샷 - 8초 수집 직후 0.7초]",
    *_render_can(snapshot),
    "",
    "※ 계기판 전구 자체를 직접 읽는 기능은 아닙니다. CAN·차량상태·openpilot 경고에서 원인 후보를 찾습니다.",
    "※ XPlus 상태값을 맞췄어도 순정 AEB 작동을 의미하지 않습니다. FCA 제동 명령 비트는 0으로 유지되고 Panda가 비정상 작동 명령을 차단합니다.",
    "※ NEXO 롱컨에서는 TCS13 ACCEnable 값이 순정 SCC 중지 때문에 비정상처럼 보일 수 있어 단독 고장 판정에 사용하지 않습니다.",
    "※ 계기판에 SCC·FCA·ADAS·조향 경고등이 실제로 켜져 있으면 위 판정과 무관하게 도로 주행하지 마세요.",
  ]
  return "\n".join(lines)


def prepend_cluster_warning_report(core, report: str) -> str:
  return cluster_warning_report(core, report) + "\n\n" + report
