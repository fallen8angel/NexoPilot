from __future__ import annotations

import html
import json
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from cereal import messaging
from opendbc.can.dbc import DBC as CANDBC
from opendbc.can.parser import get_raw_value
from opendbc.car import Bus
from opendbc.car.hyundai.values import CAR, DBC as HYUNDAI_DBC


NEXO_LAST_FAULT_LOG = Path("/data/nexo_last_fault.txt")
NEXO_CARD_CRASH_LOG = Path("/data/nexo_card_crash.txt")
NEXO_LONG_SUCCESS_LOG = Path("/data/nexo_long_success.txt")
NEXO_SCC_TAKEOVER_MARKER = Path("/data/nexo_scc_takeover_active")
NEXO_SCC_RESTORE_LOG = Path("/data/nexo_scc_restore.log")
NEXO_SCC_ADDRS = frozenset((0x389, 0x420, 0x421, 0x50A))
NEXO_FCA_ADDRS = frozenset((0x38D, 0x483))
NEXO_DIAGNOSTIC_ADDRS = NEXO_SCC_ADDRS | NEXO_FCA_ADDRS | frozenset((0x4A2,))
PARKING_SENSOR_ADDRS = frozenset((0x436, 0x4F4))
VEHICLE_NAVI_ADDRS = frozenset((0x544, 0x4B4, 0x4B9, 0x4BE))
OBSERVATION_ONLY_ADDRS = PARKING_SENSOR_ADDRS | VEHICLE_NAVI_ADDRS
WATCHED = {
  0x389: "SCC14", 0x38D: "FCA11", 0x420: "SCC11", 0x421: "SCC12",
  0x483: "FCA12", 0x4A2: "FRT_RADAR11", 0x50A: "SCC13",
  0x436: "PAS11", 0x4F4: "SPAS12", 0x544: "Navi_HU",
  0x4B4: "NAVI_CANFD_4B4", 0x4B9: "NAVI_CANFD_4B9", 0x4BE: "NAVI_CANFD_4BE",
}
SELECTED_SIGNALS = {
  0x420: ("MainMode_ACC", "ObjValid", "ACC_ObjStatus", "ACC_ObjRelSpd", "ACC_ObjDist", "AliveCounterACC"),
  0x421: ("ACCMode", "ACCFailInfo", "StopReq", "aReqRaw", "aReqValue", "CR_VSM_Alive", "CR_VSM_ChkSum"),
  0x389: ("ACCMode", "ObjGap", "JerkUpperLimit", "JerkLowerLimit"),
  0x38D: ("FCA_Status", "CF_VSM_Warn", "CR_FCA_Alive", "CR_FCA_ChkSum"),
  0x483: ("FCA_USM", "FCA_DrvSetState"),
  0x50A: ("SCCDrvModeRValue", "SCC_Equip", "Lead_Veh_Dep_Alert_USM"),
  0x436: (
    "CF_Gway_PASDisplayFLH", "CF_Gway_PASDisplayFRH", "CF_Gway_PASDisplayFCTR",
    "CF_Gway_PASDisplayRLH", "CF_Gway_PASDisplayRRH", "CF_Gway_PASDisplayRCTR",
    "CF_Gway_PASFsound", "CF_Gway_PASRsound", "CF_Gway_PASSystemOn",
    "CF_Gway_PASCheckSound", "CF_Gway_PASDistance",
  ),
  0x4F4: (
    "CF_Spas_FIL_Ind", "CF_Spas_FIR_Ind", "CF_Spas_FOL_Ind", "CF_Spas_FOR_Ind",
    "CF_Spas_RIL_Ind", "CF_Spas_RIR_Ind", "CF_Spas_ROL_Ind", "CF_Spas_ROR_Ind",
    "CF_Spas_FI_Ind", "CF_Spas_RI_Ind", "CF_Spas_FLS_Alarm", "CF_Spas_FCS_Alarm",
    "CF_Spas_FRS_Alarm", "CF_Spas_FR_Alarm", "CF_Spas_RR_Alarm", "CF_Spas_RLS_Alarm",
    "CF_Spas_RCS_Alarm", "CF_Spas_BEEP_Alarm", "CF_Spas_StatAlarm",
  ),
  0x544: ("SpeedLim_Nav_Clu", "SpeedLim_Nav_General", "SpeedLim_Nav_Cam"),
}
_DIAG_DBC = None


def _dbc():
  global _DIAG_DBC
  if _DIAG_DBC is None:
    _DIAG_DBC = CANDBC(HYUNDAI_DBC[CAR.HYUNDAI_NEXO_1ST_GEN][Bus.pt])
  return _DIAG_DBC


def decode_payload(address: int, dat: bytes) -> str:
  try:
    message = _dbc().addr_to_msg.get(address)
    if message is None:
      return "DBC 메시지 없음"
    output = []
    for name in SELECTED_SIGNALS.get(address, ()):
      signal = message.sigs.get(name)
      if signal is None:
        continue
      raw = get_raw_value(dat, signal)
      if signal.is_signed:
        raw -= ((raw >> (signal.size - 1)) & 1) * (1 << signal.size)
      value = raw * signal.factor + signal.offset
      if signal.calc_checksum is not None:
        expected = signal.calc_checksum(address, signal, bytearray(dat))
        output.append(f"{name}={raw} checksum={'OK' if raw == expected else f'FAIL(expected {expected})'}")
      else:
        rendered = f"{value:.3f}" if isinstance(value, float) and not value.is_integer() else str(int(value))
        output.append(f"{name}={rendered}")
    return ", ".join(output) if output else "선택 신호 없음"
  except Exception as error:
    return f"DBC 해석 실패: {error}"


def decode_values(address: int, dat: bytes) -> dict[str, int | float]:
  """Decode selected observation values without ever transmitting CAN."""
  values: dict[str, int | float] = {}
  try:
    message = _dbc().addr_to_msg.get(address)
    if message is None:
      return values
    for name in SELECTED_SIGNALS.get(address, ()):
      signal = message.sigs.get(name)
      if signal is None:
        continue
      raw = get_raw_value(dat, signal)
      if signal.is_signed:
        raw -= ((raw >> (signal.size - 1)) & 1) * (1 << signal.size)
      value = raw * signal.factor + signal.offset
      values[name] = int(value) if float(value).is_integer() else round(float(value), 3)
  except Exception:
    pass
  return values


def _json_log(path: Path) -> dict[str, object] | None:
  try:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))
  except (OSError, json.JSONDecodeError):
    return None


def _record_freshness(core, payload: dict[str, object] | None) -> str:
  if not payload:
    return "기록 없음"
  current = core.git_value("rev-parse", "--short", "HEAD")
  recorded = str(payload.get("git_commit", ""))
  if recorded and current != "확인 불가" and not recorded.startswith(current) and not current.startswith(recorded[:9]):
    return f"과거 버전 기록 (기록 {recorded[:9]}, 현재 {current[:9]})"
  return "현재 버전 기록 후보"


def last_fault_output(core) -> str:
  payload = _json_log(NEXO_LAST_FAULT_LOG)
  if payload is None:
    return "저장된 롱컨 실패 기록이 없습니다."
  freshness = _record_freshness(core, payload)
  return f"[{freshness}]" + chr(10) + json.dumps(payload, ensure_ascii=False, indent=2)[-60000:]


def card_crash_output(core) -> str:
  payload = _json_log(NEXO_CARD_CRASH_LOG)
  if payload is None:
    return "저장된 card Python crash traceback이 없습니다."
  freshness = _record_freshness(core, payload)
  return f"[{freshness}]" + chr(10) + json.dumps(payload, ensure_ascii=False, indent=2)[-60000:]


def runtime_status_output(core) -> str:
  params = core.Params()
  def value(key: str) -> str:
    try:
      raw = params.get(key)
      if raw is None:
        return ""
      return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    except Exception:
      return ""

  code, processes = core.run_command(["ps", "-eo", "pid,args"], timeout=3)
  card_processes = [] if code != 0 else [
    line.strip() for line in processes.splitlines()
    if "selfdrive.car.card" in line or "./card" in line
  ]
  heartbeat = value("NexoCardHeartbeatMono")
  try:
    heartbeat_age = max(0.0, time.monotonic() - float(heartbeat))
    heartbeat_text = f"{heartbeat_age:.1f}초 전"
  except Exception:
    heartbeat_text = "확인 불가"

  success = _json_log(NEXO_LONG_SUCCESS_LOG)
  takeover_pending = NEXO_SCC_TAKEOVER_MARKER.exists()
  try:
    restore_log = NEXO_SCC_RESTORE_LOG.read_text(encoding="utf-8", errors="replace")[-12000:]
  except OSError:
    restore_log = "복구 시도 기록 없음"
  lines = [
    f"card 프로세스: {'실행 중' if card_processes else '실행 중 아님'}",
    *(card_processes[:4] or ["프로세스 행 없음"]),
    f"card heartbeat: {heartbeat_text}",
    f"세션 상태: {value('NexoCardSessionState') or '확인 불가'}",
    f"마지막 단계: {value('NexoCardStage') or '확인 불가'}",
    f"현재 실패 이유: {value('NexoCardSessionReason') or value('NexoLongitudinalFailure') or '없음'}",
    f"순정 SCC 복구 대기 마커: {'있음 - 일반 크루즈 복구 필요' if takeover_pending else '없음'}",
    f"마지막 성공 기록: {json.dumps(success, ensure_ascii=False) if success else '없음'}",
    "",
    "[순정 SCC 복구 시도]",
    restore_log,
  ]
  return chr(10).join(lines)


def _drain_sendcan(sock, requested: Counter[int]) -> None:
  while True:
    event = messaging.recv_one_or_none(sock)
    if event is None:
      return
    for frame in getattr(event, "sendcan", ()):
      address = int(frame.address)
      if address in NEXO_DIAGNOSTIC_ADDRS:
        requested[address] += 1


def _category_count(counts: Counter[tuple[int, int]], addresses, low: int, high: int | None = None) -> int:
  return sum(count for (source, address), count in counts.items()
             if address in addresses and source >= low and (high is None or source < high))


def _source_count(counts: Counter[tuple[int, int]], addresses, source: int) -> int:
  return sum(count for (src, address), count in counts.items() if address in addresses and src == source)


def _other_vehicle_count(counts: Counter[tuple[int, int]], addresses) -> int:
  return sum(count for (source, address), count in counts.items() if address in addresses and 1 <= source < 128)


def _flow_lines(requested: Counter[int], counts: Counter[tuple[int, int]]) -> list[str]:
  lines = ["메시지        요청     성공     차단   source0수신"]
  for address in (0x420, 0x421, 0x50A, 0x389, 0x38D, 0x483, 0x4A2):
    accepted = sum(count for (source, addr), count in counts.items() if addr == address and 128 <= source < 192)
    blocked = sum(count for (source, addr), count in counts.items() if addr == address and source >= 192)
    source0 = sum(count for (source, addr), count in counts.items() if addr == address and source == 0)
    lines.append(f"{WATCHED[address]:12s} {requested[address]:7d} {accepted:8d} {blocked:8d} {source0:12d}")
  return lines


def parking_sensor_verdict_lines(addresses_resolved: bool, any_raw: bool, any_payload_change: bool,
                                 any_decoded_change: bool, any_nonzero: bool) -> tuple[str, ...]:
  if any_decoded_change:
    return ("[주차센서 신호 확인] 위치별 표시·경고 값이 실제로 변했습니다.",)
  if any_nonzero:
    return ("[주차센서 활성 후보] 주차센서 관련 값은 수신됐지만 8초 동안 단계 변화는 없었습니다.",)
  if any_payload_change:
    return ("[DBC 재확인 필요] 원본 데이터는 변했지만 현재 DBC의 주차센서 값 변화로 해독되지 않았습니다.",)
  if any_raw:
    return (
      "[현재 신호로 상태 판정 불가] SPAS12·PAS11 후보 메시지는 수신됐지만 원본·해독 값이 고정돼 있었습니다.",
      "값이 0이라는 이유만으로 주차센서가 꺼졌다고 판단하지 않습니다. OFF·ON 또는 장애물 거리를 바꿔 다시 수집하세요.",
    )
  if not addresses_resolved:
    return ("[DBC 확인 필요] SPAS12·PAS11 주소를 해석하지 못했습니다.",)
  return ("[버스/메시지 확인 필요] SPAS12·PAS11 원본 CAN을 관측하지 못했습니다.",)


def _parking_sensor_lines(counts: Counter[tuple[int, int]], latest: dict[tuple[int, int], bytes],
                          payloads: dict[tuple[int, int], set[bytes]],
                          decoded_states: dict[tuple[int, int], set[tuple[tuple[str, int | float], ...]]]) -> list[str]:
  keys = sorted(key for key in counts if key[1] in PARKING_SENSOR_ADDRS)
  lines = [
    "[전·후방 주차센서 CAN 후보 신호 · 읽기 전용]",
    "※ SPAS12·PAS11은 DBC 후보입니다. 수동 주차 버튼 입력 유무를 판정 근거로 사용하지 않습니다.",
  ]
  for source, address in keys:
    key = (source, address)
    lines.append(
      f"src={source:3d} {WATCHED[address]} 0x{address:03X}: RX={counts[key]} "
      f"payload종류={len(payloads[key])} 해독상태종류={len(decoded_states[key])} | "
      f"{decode_payload(address, latest[key])}"
    )

  any_raw = bool(keys)
  any_payload_change = any(len(payloads[key]) > 1 for key in keys)
  any_decoded_change = any(len(decoded_states[key]) > 1 for key in keys)
  any_nonzero = any(
    any(value != 0 for _, value in state)
    for key in keys for state in decoded_states[key]
  )
  if not keys:
    lines.append("SPAS12·PAS11 원본 CAN 관측 없음")
  lines.extend(parking_sensor_verdict_lines(True, any_raw, any_payload_change, any_decoded_change, any_nonzero))
  lines.append("※ 이 진단은 CAN·UDS를 송신하거나 차량 설정을 변경하지 않습니다.")
  return lines


def _vehicle_navi_lines(counts: Counter[tuple[int, int]], latest: dict[tuple[int, int], bytes],
                        payloads: dict[tuple[int, int], set[bytes]], can_frame_total: int) -> list[str]:
  keys = sorted(key for key in counts if key[1] in VEHICLE_NAVI_ADDRS)
  lines = [
    "[순정 내비 vNAVI 호환성 · 읽기 전용]",
    "※ 순정 내비에 과속카메라 또는 방지턱 안내가 표시되는 동안 실행해야 가장 정확합니다.",
  ]
  for source, address in keys:
    key = (source, address)
    decoded = decode_payload(address, latest[key]) if address == 0x544 else "DBC 선택 신호 없음"
    lines.append(
      f"src={source:3d} {WATCHED[address]} 0x{address:03X}: RX={counts[key]} "
      f"payload종류={len(payloads[key])} | {decoded}"
    )

  classic_keys = [key for key in keys if key[1] == 0x544]
  canfd_seen = any(address in (0x4B4, 0x4B9, 0x4BE) for _, address in keys)
  camera_active = False
  for key in classic_keys:
    values = decode_values(0x544, latest[key])
    limit = values.get("SpeedLim_Nav_Clu", 0)
    camera_active = camera_active or values.get("SpeedLim_Nav_Cam") == 1 and isinstance(limit, (int, float)) and 0 < limit < 255

  if camera_active:
    lines.append("[넥쏘 vNAVI 신호 확인] Navi_HU 0x544에서 활성 카메라와 제한속도가 함께 관측되었습니다.")
  elif classic_keys:
    lines.append("[넥쏘 내비 신호 확인 · 재점검 필요] Navi_HU 0x544는 수신됐지만 활성 카메라 상태는 관측되지 않았습니다.")
  elif canfd_seen:
    lines.append("[CAN-FD 내비 후보 확인] 후보 프레임이 관측됐습니다. NEXO classic-CAN 0x544와 별도로 해석해야 합니다.")
  elif can_frame_total:
    lines.append("[현재 미관측] 차량 CAN은 수신됐지만 내비 후보 ID 0x544/0x4B4/0x4B9/0x4BE는 보이지 않았습니다.")
  else:
    lines.append("[판정 보류] raw CAN이 없어 vNAVI 지원 여부를 판정하지 않습니다.")
  lines.append("※ 이 진단은 수신 신호만 관측하며 CAN·UDS 송신이나 vNAVI 설정 변경을 하지 않습니다.")
  return lines


def longitudinal_blackbox_output(core, duration: float = 8.0) -> str:
  started = time.monotonic()
  wall_time = datetime.now().astimezone().isoformat(timespec="seconds")
  sm = messaging.SubMaster(["carState", "selfdriveState", "carControl", "pandaStates", "radarState"])
  can_sock = messaging.sub_sock("can", timeout=20)
  sendcan_sock = messaging.sub_sock("sendcan", timeout=20)
  requested: Counter[int] = Counter()
  counts: Counter[tuple[int, int]] = Counter()
  latest: dict[tuple[int, int], bytes] = {}
  observation_payloads: dict[tuple[int, int], set[bytes]] = defaultdict(set)
  observation_decoded: dict[tuple[int, int], set[tuple[tuple[str, int | float], ...]]] = defaultdict(set)
  can_frame_total = 0
  timeline = []
  previous = None
  first_acc_fault_at = None

  while time.monotonic() - started < duration:
    sm.update(20)
    _drain_sendcan(sendcan_sock, requested)
    event = messaging.recv_one_or_none(can_sock)
    if event is not None:
      for frame in event.can:
        address, source = int(frame.address), int(frame.src)
        can_frame_total += 1
        if address in WATCHED or 0x500 <= address <= 0x51F:
          counts[(source, address)] += 1
          latest[(source, address)] = bytes(frame.dat)
          if address in OBSERVATION_ONLY_ADDRS:
            key = (source, address)
            if len(observation_payloads[key]) < 64:
              observation_payloads[key].add(bytes(frame.dat))
            decoded = tuple(decode_values(address, bytes(frame.dat)).items())
            if decoded and len(observation_decoded[key]) < 64:
              observation_decoded[key].add(decoded)

    try:
      cs = sm["carState"]
      ss = sm["selfdriveState"]
      cc = sm["carControl"]
      pandas = sm["pandaStates"]
      radar = sm["radarState"]
      lead = radar.leadOne
      panda = pandas[0] if len(pandas) else None
      errors = radar.radarErrors
      snapshot = (
        str(cs.gearShifter), bool(cs.brakePressed), bool(cs.gasPressed), bool(cs.accFaulted),
        bool(cs.cruiseState.available), bool(cs.cruiseState.enabled), str(ss.state), bool(ss.enabled), bool(ss.active),
        bool(panda.controlsAllowed) if panda is not None else None,
        str(panda.safetyModel) if panda is not None else None,
        int(panda.safetyParam) if panda is not None else None,
        bool(panda.safetyRxChecksInvalid) if panda is not None else None,
        bool(errors.canError), bool(errors.radarFault), bool(errors.wrongConfig), bool(errors.radarUnavailableTemporary),
        bool(lead.status), float(lead.dRel), float(lead.vRel), float(lead.aRel),
        str(ss.alertText1), str(ss.alertText2),
        bool(ss.experimentalMode), str(ss.personality),
        bool(cc.enabled), bool(cc.latActive), bool(cc.longActive),
        str(cc.actuators.longControlState), float(cc.actuators.accel),
        tuple(f"{button.type}:{'down' if button.pressed else 'up'}" for button in cs.buttonEvents),
      )
      if snapshot != previous:
        elapsed = time.monotonic() - started
        if snapshot[3] and (previous is None or not previous[3]) and first_acc_fault_at is None:
          first_acc_fault_at = elapsed
        timeline.append(
          f"{elapsed:6.2f}s gear={snapshot[0]} brake={snapshot[1]} gas={snapshot[2]} accFault={snapshot[3]} "
          f"cruise={snapshot[4]}/{snapshot[5]} selfdrive={snapshot[6]}/{snapshot[7]}/{snapshot[8]} "
          f"controlsAllowed={snapshot[9]} safety={snapshot[10]}/{snapshot[11]} rxInvalid={snapshot[12]} "
          f"radarErrors={snapshot[13:17]} leadOne=status:{snapshot[17]} dRel:{snapshot[18]:.2f} "
          f"vRel:{snapshot[19]:.2f} aRel:{snapshot[20]:.2f} alert={snapshot[21]!r} {snapshot[22]!r} "
          f"experimental={snapshot[23]} personality={snapshot[24]} "
          f"carControl=enabled:{snapshot[25]}/lat:{snapshot[26]}/long:{snapshot[27]} "
          f"longState:{snapshot[28]} accel:{snapshot[29]:.3f} buttons={snapshot[30]}"
        )
        previous = snapshot
    except Exception as error:
      timeline.append(f"{time.monotonic() - started:6.2f}s 상태 읽기 실패: {error}")
    time.sleep(0.01)

  stock_scc = _source_count(counts, NEXO_SCC_ADDRS, 0)
  other_scc = _other_vehicle_count(counts, NEXO_SCC_ADDRS)
  op_scc = _category_count(counts, NEXO_SCC_ADDRS, 128, 192)
  blocked_scc = _category_count(counts, NEXO_SCC_ADDRS, 192)
  stock_fca = _source_count(counts, NEXO_FCA_ADDRS, 0)
  other_fca = _other_vehicle_count(counts, NEXO_FCA_ADDRS)
  op_fca = _category_count(counts, NEXO_FCA_ADDRS, 128, 192)
  blocked_fca = _category_count(counts, NEXO_FCA_ADDRS, 192)
  radar_tracks = sum(count for (_, address), count in counts.items() if 0x500 <= address <= 0x51F and address != 0x50A)

  if stock_scc and op_scc:
    verdict = "위험: 물리 source0 순정 SCC와 openpilot SCC가 동시에 관측됐습니다."
  elif op_scc:
    verdict = "정상 후보: 물리 source0 순정 SCC 없이 openpilot SCC가 관측됐습니다."
  elif stock_scc:
    verdict = "물리 source0 순정 SCC만 관측됐습니다. openpilot 종방향 송신이 시작되지 않았습니다."
  else:
    verdict = "물리 source0 SCC와 openpilot SCC 송신을 관측하지 못했습니다."

  lines = [
    "NexoPilot NEXO 롱컨 블랙박스 v2",
    f"수집 시각: {wall_time}",
    f"Git: {core.git_value('rev-parse', '--short', 'HEAD')}",
    f"수집 시간: {duration:.1f}초",
    "", "[상태 변화]", *(timeline or ["상태 메시지를 수신하지 못했습니다."]),
    "", "[SCC/FCA 분리 자동 판정]",
    f"첫 accFault 전환: {first_acc_fault_at:.2f}초" if first_acc_fault_at is not None else "첫 accFault 전환: 관측되지 않음",
    f"순정 SCC: {stock_scc} | 기타 버스 SCC: {other_scc} | openpilot SCC: {op_scc} | Panda 차단 SCC: {blocked_scc}",
    f"순정 FCA: {stock_fca} | 기타 버스 FCA: {other_fca} | openpilot FCA: {op_fca} | Panda 차단 FCA: {blocked_fca}",
    f"레이더 트랙 프레임: {radar_tracks}", f"판정: {verdict}",
    "※ src=0은 물리 bus0 RX입니다. src=1~127은 다른 물리 버스 RX로 분리합니다.",
    "※ src=128~191은 Panda가 돌려준 openpilot TX echo/accepted이며 물리 bus=src-128입니다.",
    "※ src>=192는 Panda safety가 거부한 TX이며 물리 bus=src-192입니다. 현재 CAN 메타데이터는 C safety hook의 세부 거부 사유 코드를 싣지 않으므로 address/payload와 controlsAllowed/safetyModel/safetyParam을 함께 확인합니다.",
    "", "[sendcan 요청 → Panda 결과]", *_flow_lines(requested, counts),
    "", "[SCC/FCA/레이더/관측 CAN 집계]",
  ]
  for (source, address), count in sorted(counts.items()):
    data = latest[(source, address)]
    lines.append(f"src={source:3d} {WATCHED.get(address, 'RADAR_TRACK'):12s} 0x{address:03X} {count:5d}회 | {data.hex(' ')}")
  if not counts:
    lines.append("감시 대상 CAN 메시지를 수신하지 못했습니다.")

  lines.extend(["", "[주요 SCC/FCA 신호 DBC 해석]"])
  decoded = False
  for (source, address), data in sorted(latest.items()):
    if address in SELECTED_SIGNALS:
      decoded = True
      lines.append(f"src={source:3d} 0x{address:03X}: {decode_payload(address, data)}")
  if not decoded:
    lines.append("해석할 SCC/FCA 메시지가 없습니다.")

  lines.extend(["", *_parking_sensor_lines(counts, latest, observation_payloads, observation_decoded)])
  lines.extend(["", *_vehicle_navi_lines(counts, latest, observation_payloads, can_frame_total)])

  lines.extend(["", "[롱컨 초기화·UDS 추적]", core.nexo_long_init_output()])
  lines.extend(["", "[card 런타임 상태]", runtime_status_output(core)])
  lines.extend(["", "[마지막 롱컨 실패 기록]", last_fault_output(core)])
  lines.extend(["", "[마지막 card crash traceback]", card_crash_output(core)])
  lines.extend(["", "[핵심 오류 로그]", core.important_log_output()])
  return "\n".join(lines)


def raw_can_diagnostic_output(core) -> str:
  requested: Counter[int] = Counter()
  counts: Counter[tuple[int, int]] = Counter()
  latest: dict[tuple[int, int], bytes] = {}
  track_counts: Counter[int] = Counter()
  try:
    can_sock = messaging.sub_sock("can", timeout=100)
    sendcan_sock = messaging.sub_sock("sendcan", timeout=20)
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
      _drain_sendcan(sendcan_sock, requested)
      event = messaging.recv_one_or_none(can_sock)
      if event is None:
        continue
      for frame in event.can:
        address, source = int(frame.address), int(frame.src)
        if address in WATCHED:
          counts[(source, address)] += 1
          latest[(source, address)] = bytes(frame.dat)
        elif 0x500 <= address <= 0x51F:
          track_counts[source] += 1
          latest[(source, address)] = bytes(frame.dat)
  except Exception as error:
    return f"CAN 수집 실패: {error}"

  lines = [
    "1.5초 실제 CAN 및 sendcan 수집 결과",
    f"SCC: source0 {_source_count(counts, NEXO_SCC_ADDRS, 0)} | 기타버스 {_other_vehicle_count(counts, NEXO_SCC_ADDRS)} | 성공 {_category_count(counts, NEXO_SCC_ADDRS, 128, 192)} | 차단 {_category_count(counts, NEXO_SCC_ADDRS, 192)}",
    f"FCA: source0 {_source_count(counts, NEXO_FCA_ADDRS, 0)} | 기타버스 {_other_vehicle_count(counts, NEXO_FCA_ADDRS)} | 성공 {_category_count(counts, NEXO_FCA_ADDRS, 128, 192)} | 차단 {_category_count(counts, NEXO_FCA_ADDRS, 192)}",
    "※ source0(src=0)만 순정 ECU 물리 버스 판정에 사용하며 src=1~127은 별도 버스 수신으로 표시합니다.",
    "※ src=128~191은 openpilot TX 반환 echo이며 src>=192는 Panda safety 차단 TX입니다.",
    "※ 순정 FCA11/FCA12 수신은 정상이며 SCC 중복 제어 판정에 포함하지 않습니다.",
    "", "[sendcan 요청 → Panda 결과]", *_flow_lines(requested, counts), "",
  ]
  for (source, address), count in sorted(counts.items()):
    status, physical_bus = core.can_source_info(source)
    data = latest[(source, address)]
    decoded = decode_payload(address, data) if address in SELECTED_SIGNALS else ""
    lines.append(
      f"{status} (물리 bus {physical_bus}, src {source}) {WATCHED[address]} 0x{address:03X}: {count}회 | "
      f"{data.hex(' ')}" + (f" | {decoded}" if decoded else "")
    )
  for source, count in sorted(track_counts.items()):
    ids = {address for (src, address) in latest if src == source and 0x500 <= address <= 0x51F}
    status, physical_bus = core.can_source_info(source)
    lines.append(f"{status} (물리 bus {physical_bus}, src {source}) RADAR 0x500~0x51F: {count}회 | 고유 ID {len(ids)}개")
  if not track_counts:
    lines.append("RADAR 0x500~0x51F: 수신 없음")
  if not counts:
    lines.append("SCC/FCA 감시 메시지 수신 없음")
  return "\n".join(lines)


def enhance_diagnostic_page(page: str) -> str:
  page = page.replace(
    "disable ECU 요청, 레이더 트랙 활성화, 최종 재차단 단계를 보여줍니다. 요청 성공만으로 순정 SCC 정지가 보장되지는 않으므로 실제 정지 여부는 블랙박스 자동 판정으로 확인합니다.",
    "순정 SCC 통신 중지 → NEXOdriveAI 레이더 트랙 설정 → 런타임 SCC 감시 순서와 UDS 요청·응답·소요시간을 보여줍니다.",
  ).replace("<h2>롱컨 초기화 추적</h2>", "<h2>롱컨 초기화·UDS 추적</h2>")
  marker = '<div class="card"><h2>핵심 오류 요약</h2>'
  core = __import__("system.nexo_web.web_core", fromlist=["*"])
  runtime_card = (
    '<div class="card"><h2>card 런타임·종료 진단</h2>'
    '<p class="desc">card 생존 여부와 heartbeat 및 마지막 실행 단계와 Python traceback을 구분해 표시합니다.</p>'
    f'<pre>{html.escape(runtime_status_output(core))}' + chr(10) + chr(10) +
    f'{html.escape(card_crash_output(core))}</pre></div>'
  )
  fault_card = (
    '<div class="card"><h2>마지막 롱컨 실패 기록</h2>'
    '<p class="desc">현재 Git과 다른 과거 기록은 과거 버전 기록으로 표시합니다. 설정 자동해제나 자동 재부팅 없이 저장됩니다.</p>'
    f'<pre>{html.escape(last_fault_output(core))}</pre></div>'
  )
  return page.replace(marker, runtime_card + fault_card + marker, 1)
