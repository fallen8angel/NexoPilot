from __future__ import annotations

import html
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from cereal import messaging
from opendbc.can.dbc import DBC as CANDBC
from opendbc.can.parser import get_raw_value
from opendbc.car import Bus
from opendbc.car.hyundai.values import CAR, DBC as HYUNDAI_DBC


NEXO_LAST_FAULT_LOG = Path("/data/nexo_last_fault.txt")
NEXO_SCC_ADDRS = frozenset((0x389, 0x420, 0x421, 0x50A))
NEXO_FCA_ADDRS = frozenset((0x38D, 0x483))
NEXO_DIAGNOSTIC_ADDRS = NEXO_SCC_ADDRS | NEXO_FCA_ADDRS | frozenset((0x4A2,))
WATCHED = {
  0x389: "SCC14", 0x38D: "FCA11", 0x420: "SCC11", 0x421: "SCC12",
  0x483: "FCA12", 0x4A2: "FRT_RADAR11", 0x50A: "SCC13",
}
SELECTED_SIGNALS = {
  0x420: ("MainMode_ACC", "ObjValid", "ACC_ObjStatus", "ACC_ObjRelSpd", "ACC_ObjDist", "AliveCounterACC"),
  0x421: ("ACCMode", "ACCFailInfo", "StopReq", "aReqRaw", "aReqValue", "CR_VSM_Alive", "CR_VSM_ChkSum"),
  0x389: ("ACCMode", "ObjGap", "JerkUpperLimit", "JerkLowerLimit"),
  0x38D: ("FCA_Status", "CF_VSM_Warn", "CR_FCA_Alive", "CR_FCA_ChkSum"),
  0x483: ("FCA_USM", "FCA_DrvSetState"),
  0x50A: ("SCCDrvModeRValue", "SCC_Equip", "Lead_Veh_Dep_Alert_USM"),
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


def last_fault_output() -> str:
  try:
    output = NEXO_LAST_FAULT_LOG.read_text(encoding="utf-8", errors="replace")
  except OSError:
    return "저장된 자동 복구 기록이 없습니다."
  return output[-60000:] or "자동 복구 기록이 비어 있습니다."


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


def _flow_lines(requested: Counter[int], counts: Counter[tuple[int, int]]) -> list[str]:
  lines = ["메시지        요청     성공     차단     차량수신"]
  for address in (0x420, 0x421, 0x50A, 0x389, 0x38D, 0x483, 0x4A2):
    accepted = sum(count for (source, addr), count in counts.items() if addr == address and 128 <= source < 192)
    blocked = sum(count for (source, addr), count in counts.items() if addr == address and source >= 192)
    vehicle = sum(count for (source, addr), count in counts.items() if addr == address and source < 128)
    lines.append(f"{WATCHED[address]:12s} {requested[address]:7d} {accepted:8d} {blocked:8d} {vehicle:10d}")
  return lines


def longitudinal_blackbox_output(core, duration: float = 8.0) -> str:
  started = time.monotonic()
  wall_time = datetime.now().astimezone().isoformat(timespec="seconds")
  sm = messaging.SubMaster(["carState", "selfdriveState", "pandaStates", "radarState"])
  can_sock = messaging.sub_sock("can", timeout=20)
  sendcan_sock = messaging.sub_sock("sendcan", timeout=20)
  requested: Counter[int] = Counter()
  counts: Counter[tuple[int, int]] = Counter()
  latest: dict[tuple[int, int], bytes] = {}
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
        if address in WATCHED or 0x500 <= address <= 0x51F:
          counts[(source, address)] += 1
          latest[(source, address)] = bytes(frame.dat)

    try:
      cs = sm["carState"]
      ss = sm["selfdriveState"]
      pandas = sm["pandaStates"]
      radar = sm["radarState"]
      panda = pandas[0] if len(pandas) else None
      errors = radar.radarErrors
      snapshot = (
        str(cs.gearShifter), bool(cs.brakePressed), bool(cs.gasPressed), bool(cs.accFaulted),
        bool(cs.cruiseState.available), bool(cs.cruiseState.enabled), str(ss.state), bool(ss.enabled), bool(ss.active),
        bool(panda.controlsAllowed) if panda is not None else None,
        int(panda.safetyParam) if panda is not None else None,
        bool(panda.safetyRxChecksInvalid) if panda is not None else None,
        bool(errors.canError), bool(errors.radarFault), bool(errors.wrongConfig), bool(errors.radarUnavailableTemporary),
        str(ss.alertText1), str(ss.alertText2),
      )
      if snapshot != previous:
        elapsed = time.monotonic() - started
        if snapshot[3] and (previous is None or not previous[3]) and first_acc_fault_at is None:
          first_acc_fault_at = elapsed
        timeline.append(
          f"{elapsed:6.2f}s gear={snapshot[0]} brake={snapshot[1]} gas={snapshot[2]} accFault={snapshot[3]} "
          f"cruise={snapshot[4]}/{snapshot[5]} selfdrive={snapshot[6]}/{snapshot[7]}/{snapshot[8]} "
          f"controlsAllowed={snapshot[9]} safetyParam={snapshot[10]} rxInvalid={snapshot[11]} "
          f"radarErrors={snapshot[12:16]} alert={snapshot[16]!r} {snapshot[17]!r}"
        )
        previous = snapshot
    except Exception as error:
      timeline.append(f"{time.monotonic() - started:6.2f}s 상태 읽기 실패: {error}")
    time.sleep(0.01)

  stock_scc = _category_count(counts, NEXO_SCC_ADDRS, 0, 128)
  op_scc = _category_count(counts, NEXO_SCC_ADDRS, 128, 192)
  blocked_scc = _category_count(counts, NEXO_SCC_ADDRS, 192)
  stock_fca = _category_count(counts, NEXO_FCA_ADDRS, 0, 128)
  op_fca = _category_count(counts, NEXO_FCA_ADDRS, 128, 192)
  blocked_fca = _category_count(counts, NEXO_FCA_ADDRS, 192)
  radar_tracks = sum(count for (_, address), count in counts.items() if 0x500 <= address <= 0x51F and address != 0x50A)

  if stock_scc and op_scc:
    verdict = "위험: 순정 SCC와 openpilot SCC가 동시에 관측됐습니다."
  elif op_scc:
    verdict = "정상 후보: openpilot SCC만 관측됐습니다. 순정 FCA 수신은 별도 정상 스트림입니다."
  elif stock_scc:
    verdict = "순정 SCC만 관측됐습니다. openpilot 종방향 송신이 시작되지 않았습니다."
  else:
    verdict = "SCC 송신을 관측하지 못했습니다."

  lines = [
    "NexoPilot NEXO 롱컨 블랙박스 v2",
    f"수집 시각: {wall_time}",
    f"Git: {core.git_value('rev-parse', '--short', 'HEAD')}",
    f"수집 시간: {duration:.1f}초",
    "", "[상태 변화]", *(timeline or ["상태 메시지를 수신하지 못했습니다."]),
    "", "[SCC/FCA 분리 자동 판정]",
    f"첫 accFault 전환: {first_acc_fault_at:.2f}초" if first_acc_fault_at is not None else "첫 accFault 전환: 관측되지 않음",
    f"순정 SCC: {stock_scc} | openpilot SCC: {op_scc} | Panda 차단 SCC: {blocked_scc}",
    f"순정 FCA: {stock_fca} | openpilot FCA: {op_fca} | Panda 차단 FCA: {blocked_fca}",
    f"레이더 트랙 프레임: {radar_tracks}", f"판정: {verdict}",
    "", "[sendcan 요청 → Panda 결과]", *_flow_lines(requested, counts),
    "", "[SCC/FCA/레이더 CAN 집계]",
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

  lines.extend(["", "[롱컨 초기화·UDS 추적]", core.nexo_long_init_output()])
  lines.extend(["", "[마지막 자동 복구 기록]", last_fault_output()])
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
    f"SCC: 차량 {_category_count(counts, NEXO_SCC_ADDRS, 0, 128)} | 성공 {_category_count(counts, NEXO_SCC_ADDRS, 128, 192)} | 차단 {_category_count(counts, NEXO_SCC_ADDRS, 192)}",
    f"FCA: 차량 {_category_count(counts, NEXO_FCA_ADDRS, 0, 128)} | 성공 {_category_count(counts, NEXO_FCA_ADDRS, 128, 192)} | 차단 {_category_count(counts, NEXO_FCA_ADDRS, 192)}",
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
  fault_card = (
    '<div class="card"><h2>마지막 자동 복구 기록</h2>'
    '<p class="desc">순정 SCC 재등장 또는 초기화 실패 직전 상태와 최근 5초 CAN 기록입니다. 재부팅 후에도 유지됩니다.</p>'
    f'<pre>{html.escape(last_fault_output())}</pre></div>'
  )
  return page.replace(marker, fault_card + marker, 1)
