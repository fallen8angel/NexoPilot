#!/usr/bin/env python3
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DIAGNOSTIC_FILES = (
  "selfdrive/car/nexo_guard.py",
  "selfdrive/car/nexo_diagnostics.py",
  "selfdrive/car/nexo_runtime_diagnostics.py",
  "selfdrive/car/card.py",
  "system/nexo_web/web.py",
  "system/nexo_web/nexo_diagnostics_v2.py",
  "system/nexo_web/nexo_unified_diagnostics.py",
  "opendbc_repo/opendbc/car/hyundai/radar_tracks.py",
  "opendbc_repo/opendbc/car/hyundai/interface.py",
)


def require(value: bool, message: str) -> None:
  if not value:
    raise AssertionError(message)


def text(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


def main() -> None:
  sources = {path: text(path) for path in DIAGNOSTIC_FILES}
  for path, source in sources.items():
    ast.parse(source, filename=path)
    print(f"syntax OK: {path}")

  guard = sources["selfdrive/car/nexo_guard.py"]
  writer = sources["selfdrive/car/nexo_diagnostics.py"]
  card = sources["selfdrive/car/card.py"]
  runtime = sources["selfdrive/car/nexo_runtime_diagnostics.py"]
  entry = sources["system/nexo_web/web.py"]
  web = sources["system/nexo_web/nexo_diagnostics_v2.py"]
  unified = sources["system/nexo_web/nexo_unified_diagnostics.py"]
  radar = sources["opendbc_repo/opendbc/car/hyundai/radar_tracks.py"]
  interface = sources["opendbc_repo/opendbc/car/hyundai/interface.py"]

  for token in ("NEXO_FCA_ADDRS", "NEXO_CAN_HISTORY_S = 5.0", "fault_snapshot", "recent_can"):
    require(token in guard, f"runtime diagnostic guard missing: {token}")
  for token in ("NEXO_LAST_FAULT_LOG", "record_nexo_fault_snapshot", "safety_rx_checks_invalid", "radar_errors"):
    require(token in writer, f"persistent fault capture missing: {token}")
  for token in ("record_nexo_fault_snapshot", "record_nexo_card_crash", "record_nexo_long_success",
                "NexoCardHeartbeatMono", "nexo_stage", "selfdriveState", "radarState"):
    require(token in card, f"card diagnostic connection missing: {token}")
  for token in ("NEXO_CARD_CRASH_LOG", "NEXO_LONG_SUCCESS_LOG", "record_nexo_card_crash",
                "record_nexo_long_success", "set_nexo_runtime_state", "traceback"):
    require(token in runtime, f"card runtime diagnostics missing: {token}")
  for token in ("[SCC/FCA 분리 자동 판정]", "[sendcan 요청 → Panda 결과]",
                "[주요 SCC/FCA 신호 DBC 해석]", "last_fault_output",
                "runtime_status_output", "card_crash_output", "과거 버전 기록",
                "순정 FCA11/FCA12 수신은 정상"):
    require(token in web, f"web diagnostics missing: {token}")

  for token in ("NexoPilot 8초 통합진단 - 한눈에 보기", "build_unified_report",
                "_carparams_snapshot", "_process_snapshot", "_service_snapshot",
                "openpilotLongitudinalControl", "pcmCruise", "radarUnavailable", "sccBus",
                "safetyConfigs", "SCC12 핵심", "순정 SCC 복구", "기계 판독 JSON",
                "상세 원문 - 필요할 때만 아래를 확인하세요",
                "controlsAllowed=False와 Panda 차단은 P단·크루즈 비활성 중에는 정상",
                "def _marker_snapshot", "longitudinal_takeover_ready", "restore_pending",
                "def correct_legacy_wording", "brand", "card_healthy", "sm.seen"):
    require(token in unified, f"unified 8-second diagnostics missing: {token}")
  require("cp.carName" not in unified, "CarParams has no carName member in this schema")
  for forbidden in ("pub_sock(", "disable_ecu", "put_bool(", "schedule_reboot", "git_run(\"merge\""):
    require(forbidden not in unified, f"unified diagnostics must remain read-only: {forbidden}")

  require("diagnostics_v2.longitudinal_blackbox_output" in entry, "web v2 blackbox not wired")
  require("unified_diagnostics.correct_legacy_wording" in entry, "legacy false-positive correction not wired")
  require("unified_diagnostics.build_unified_report" in entry, "unified 8-second report not wired")
  require("8초 통합진단 파일 하나 받기" in entry, "single-file diagnostic button label missing")
  require("diagnostics_v2.enhance_diagnostic_page" in entry, "last fault card not wired")
  for token in ("UDS TX", "UDS RX", "UDS ERROR", "request.hex(' ')"):
    require(token in radar, f"radar UDS diagnostics missing: {token}")
  require("def _trace_nexo_long_init" in interface, "NEXO init trace helper missing")
  require("elapsed_ms" in interface, "disable ECU timing trace missing")
  require("DEINIT stock SCC communication restore" in interface, "NEXO deinit restore trace missing")
  require("CarInterface.init(CP, can_recv, can_send, communication_control)" in interface,
          "non-NEXO deinit fallback missing")
  print("NEXO diagnostics v4 corrected unified report PASS")


if __name__ == "__main__":
  main()
