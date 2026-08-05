from __future__ import annotations

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
  return "
".join(lines) + report
